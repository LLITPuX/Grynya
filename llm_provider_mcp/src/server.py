import os
import sys
import asyncio
import json
import threading
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import StringIO
from dotenv import load_dotenv

# Ensure we have access to original streams
_original_stdout = sys.stdout
_original_stderr = sys.stderr

# Configure logging to go to stderr by default
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=_original_stderr
)
logger = logging.getLogger("llm-provider-mcp")

load_dotenv()

# --- Task Manager Infrastructure (Phase 1) ---

current_task_id = ContextVar("current_task_id", default=None)

@dataclass
class TaskState:
    id: str
    status: str  # "running", "completed", "failed", "cancelled"
    logs_buffer: list = field(default_factory=list)
    result: str = None
    error: str = None
    task_obj: asyncio.Task = None

TaskManager: dict[str, TaskState] = {}
log_lock = threading.Lock()

class AsyncIOSafeLogHandler(logging.Handler):
    def emit(self, record):
        task_id = current_task_id.get()
        if task_id and task_id in TaskManager:
            msg = self.format(record)
            with log_lock:
                TaskManager[task_id].logs_buffer.append(msg + "\n")

# Add the task-aware handler to our logger
task_handler = AsyncIOSafeLogHandler()
logger.addHandler(task_handler)

# Redirect print to logger.info to ensure it goes to stderr and gets captured
def safe_print(*args, **kwargs):
    msg = " ".join(map(str, args))
    logger.info(msg)

# Monkeypatch print for this module
print = safe_print

from fastmcp import FastMCP
# Create the MCP server
mcp = FastMCP("llm-provider-mcp")

def call_gemini(prompt: str, system_prompt: str, model: str, tools_info: str = None) -> str:
    from google import genai
    from google.genai import types
    from google.oauth2.credentials import Credentials
    import os
    
    print("[call_gemini] Entering Gemini API wrapper")
    token_path = os.environ.get("GEMINI_TOKEN_PATH", "credentials/token.json")
    
    if not os.path.exists(token_path):
        return f"Error: Token file not found at {token_path}. Please generate it via OAuth and place it in the credentials folder."
        
    try:
        from google.auth.transport.requests import Request
        import requests
        
        creds = Credentials.from_authorized_user_file(token_path)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            
        if not model.startswith("models/"):
            full_model_name = f"models/{model}"
        else:
            full_model_name = model

        print(f"[call_gemini] Using direct REST API request with Bearer token.")
        
        url = f"https://generativelanguage.googleapis.com/v1beta/{full_model_name}:generateContent"
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json"
        }
        
        payload = {}
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
        
        # Add tool context to prompt if available
        final_prompt = prompt
        if tools_info:
            final_prompt = f"Available tools context:\n{tools_info}\n\nTask: {prompt}"
            
        payload["contents"] = [{
            "parts": [{"text": final_prompt}]
        }]
            
        print(f"[call_gemini] Sending request to Gemini {model}... This might take a while.")
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error: Gemini API returned status {response.status_code}: {response.text}"
            
        data = response.json()
        print("[call_gemini] Received response from Gemini API.")
        
        if "candidates" in data and len(data["candidates"]) > 0:
            parts = data["candidates"][0].get("content", {}).get("parts", [])
            text = "".join([p.get("text", "") for p in parts])
            return text
        else:
            return f"Returned unexpected format: {data}"
    except Exception as e:
        print(f"[call_gemini] Encountered an error: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Gemini API Error: {str(e)}"

def call_openai(prompt: str, system_prompt: str, model: str) -> str:
    from openai import OpenAI
    
    print("[call_openai] Entering OpenAI API wrapper")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "Error: OPENAI_API_KEY not configured."
        
    client = OpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    try:
        print(f"[call_openai] Sending request to OpenAI {model}... This might take a while.")
        response = client.chat.completions.create(
            model=model,
            messages=messages
        )
        print("[call_openai] Received response from OpenAI API.")
        return response.choices[0].message.content
    except Exception as e:
        print(f"[call_openai] Encountered an error: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"OpenAI API Error: {str(e)}"

async def agent_task_wrapper(task_id: str, prompt: str, system_prompt: str, model: str):
    """Background wrapper that executes the LLM task via a thread and manages state."""
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession
    
    current_task_id.set(task_id)
    state = TaskManager[task_id]
    try:
        print(f"--- [Task {task_id}] Execution Started ---")
        
        server_url = "http://grynya-mcp-server:8000/sse"
        print(f"[{task_id}] Connecting to MCP server at {server_url}...")
        
        async with sse_client(server_url, headers={"Host": "localhost"}) as streams:
            print(f"[{task_id}] SSE connection established.")
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                print(f"[{task_id}] MCP Session initialized.")
                
                tools_response = await session.list_tools()
                tool_names = [t.name for t in tools_response.tools]
                print(f"[{task_id}] Discovered tools: {tool_names}")
                print(f"[{task_id}] Бачу базу та інструменти, полет нормальний.")

                # Direct write to the graph (Phase 3)
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                day_id_str = "d_" + now.strftime("%Y_%m_%d")
                
                print(f"[{task_id}] Writing progress to FalkorDB directly via grynya-mcp-server...")
                try:
                    save_res = await session.call_tool("add_node", arguments={
                        "node_type": "Analysis",
                        "node_data": {
                            "id": f"klim_progress_{task_id}",
                            "full_text": f"[Status Update from Klim] Task ID: {task_id}. Proceeding with model {model}.",
                            "time": now.isoformat()
                        },
                        "day_id": day_id_str
                    })
                    print(f"[{task_id}] Graph save response: {save_res}")
                except Exception as e:
                    print(f"[{task_id}] Failed to save to graph: {e}")

        
        # Execute blocking calls off the main event loop
        model_lower = model.lower()
        if "gemini" in model_lower:
            result = await asyncio.to_thread(call_gemini, prompt, system_prompt, model)
        elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            result = await asyncio.to_thread(call_openai, prompt, system_prompt, model)
        else:
            result = f"Error: Unsupported model identifier '{model}'. Must contain 'gemini', 'gpt', 'o1' or 'o3'."
            
        state.result = f"{result}\n\n[Автономний агент рапортує: Бачу базу та інструменти, полет нормальний.]"
        state.status = "completed"
        print(f"--- [Task {task_id}] Execution Completed ---")
    except asyncio.CancelledError:
        print(f"--- [Task {task_id}] Execution Cancelled ---")
        state.status = "cancelled"
        state.error = "Cancelled by user"
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"--- [Task {task_id}] Execution Failed ---")
        print(error_msg)
        state.status = "failed"
        state.error = str(e)

@mcp.tool()
async def run_agent_task(prompt: str, system_prompt: str = None, model: str = "gemini-2.5-flash") -> str:
    """
    [БЛОКУЄ] Запускає задачу агента синхронно через вказаного LLM провайдера.
    Примітка: Блокує event loop сервера FastMCP при інтенсивному використанні.
    """
    print(f"[run_agent_task] Received request for model: {model}")
    
    # Discovery tools from grynya-mcp-server
    tools_info = "No database tools discovered."
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession
    
    server_url = "http://grynya-mcp-server:8000/sse"
    try:
        async with sse_client(server_url, headers={"Host": "localhost"}) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                tools_list = []
                for t in tools_response.tools:
                    tools_list.append(f"Tool: {t.name}, Description: {t.description}")
                tools_info = "\n".join(tools_list)
                print(f"[run_agent_task] Discovered {len(tools_response.tools)} database tools.")
    except Exception as e:
        print(f"[run_agent_task] Failed to discover tools: {e}")

    model_lower = model.lower()
    if "gemini" in model_lower:
        return await asyncio.to_thread(call_gemini, prompt, system_prompt, model, tools_info)
    elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        # OpenAI doesn't get tools metadata yet in this simple wrapper
        return await asyncio.to_thread(call_openai, prompt, system_prompt, model)
    else:
        return f"Error: Unsupported model identifier '{model}'."

@mcp.tool()
async def start_async_agent_task(prompt: str, system_prompt: str = None, model: str = "gemini-2.5-flash") -> str:
    """
    Запускає асинхронну задачу агента у фоновому режимі. 
    Повертає task_id негайно без блокування.
    Використовуйте `check_task_status(task_id)` для отримання логів та результатів.
    """
    import uuid
    task_id = str(uuid.uuid4())
    state = TaskState(id=task_id, status="running")
    TaskManager[task_id] = state
    
    # create background task without blocking
    task_obj = asyncio.create_task(agent_task_wrapper(task_id, prompt, system_prompt, model))
    state.task_obj = task_obj
    
    return json.dumps({
        "status": "success",
        "task_id": task_id,
        "message": "Task started asynchronously in the background."
    })

@mcp.tool()
def cancel_agent_task(task_id: str) -> str:
    """
    Скасовує асинхронну задачу агента, яка виконується у фоновому режимі.
    """
    if task_id not in TaskManager:
        return json.dumps({"status": "error", "message": f"Task {task_id} not found."})
        
    state = TaskManager[task_id]
    
    if state.status == "running":
        if state.task_obj and not state.task_obj.done():
            state.task_obj.cancel()
            return json.dumps({"status": "success", "message": f"Task {task_id} has been cancelled."})
        else:
            return json.dumps({"status": "error", "message": f"Task {task_id} has no running task object."})
    else:
        return json.dumps({"status": "error", "message": f"Task {task_id} is not running (current status: {state.status})."})

@mcp.tool()
def check_task_status(task_id: str) -> str:
    """
    Перевіряє статус, логи та потенційний результат/помилку асинхронної задачі.
    """
    if task_id not in TaskManager:
        return json.dumps({"status": "error", "message": f"Task {task_id} not found."})
        
    state = TaskManager[task_id]
    
    with log_lock:
        logs = "".join(state.logs_buffer)
    
    response = {
        "task_id": state.id,
        "status": state.status,
        "logs": logs
    }
    
    if state.result is not None:
        response["result"] = state.result
    if state.error is not None:
        response["error"] = state.error
        
    return json.dumps(response)

if __name__ == "__main__":
    import sys
    if "--sse" in sys.argv:
        mcp.run(transport="sse", host="0.0.0.0", port=8001)
    else:
        mcp.run()
