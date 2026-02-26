import os
import sys
import asyncio
import json
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import StringIO
from dotenv import load_dotenv
from fastmcp import FastMCP

# Optional: Add any pre-startup configuration here
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

class AsyncIOSafeStdout:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout

    def write(self, s):
        task_id = current_task_id.get()
        if task_id and task_id in TaskManager:
            with log_lock:
                TaskManager[task_id].logs_buffer.append(s)
        self.original_stdout.write(s)
        
    def flush(self):
        self.original_stdout.flush()
        
    def __getattr__(self, name):
        return getattr(self.original_stdout, name)

sys.stdout = AsyncIOSafeStdout(sys.stdout)
sys.stderr = AsyncIOSafeStdout(sys.stderr)

# Create the MCP server
mcp = FastMCP("llm-provider-mcp")

def call_gemini(prompt: str, system_prompt: str, model: str) -> str:
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
            
        print(f"[call_gemini] Using direct REST API request with Bearer token.")
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
            
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
async def run_agent_task(prompt: str, system_prompt: str = None, model: str = "gemini-2.5-flash-thinking-exp") -> str:
    """
    [BLOCKING] Run an agent task synchronously via the specified LLM provider.
    Note: Blocks the fastMCP server event loop if used heavily.
    """
    print(f"[run_agent_task] Received request for model: {model}")
    model_lower = model.lower()
    
    if "gemini" in model_lower:
        return await asyncio.to_thread(call_gemini, prompt, system_prompt, model)
    elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return await asyncio.to_thread(call_openai, prompt, system_prompt, model)
    else:
        return f"Error: Unsupported model identifier '{model}'."

@mcp.tool()
async def start_async_agent_task(prompt: str, system_prompt: str = None, model: str = "gemini-2.5-flash-thinking-exp") -> str:
    """
    Starts an asynchronous agent task in the background. 
    Returns the task_id immediately without blocking.
    Use `check_task_status(task_id)` to get logs and results.
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
def check_task_status(task_id: str) -> str:
    """
    Checks the status, logs, and potential result/error of an asynchronous task.
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
