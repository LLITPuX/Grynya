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

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", ".gemini", "antigravity", "skills")

def load_skill(skill_name: str) -> str:
    """
    Завантажує вміст SKILL.md з папки .gemini/antigravity/skills/<skill_name>/.
    Повертає текст скілу або fallback-промпт якщо файл не знайдено.
    """
    skill_path = os.path.join(SKILLS_DIR, skill_name, "SKILL.md")
    skill_path = os.path.normpath(skill_path)
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()
        print(f"[load_skill] Loaded skill '{skill_name}' from {skill_path}")
        # Strip YAML frontmatter (--- ... ---) — передаємо тільки тіло інструкцій
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        return content
    else:
        print(f"[load_skill] Skill '{skill_name}' not found at {skill_path}, using fallback.")
        return (
            "You are a graph research agent. Use query_graph tool to search FalkorDB. "
            "MANDATORY: call query_graph at least once. Start with: "
            "MATCH (n) RETURN labels(n) AS type, count(n) AS cnt ORDER BY cnt DESC. "
            "Return valid JSON: {\"summary\": \"...\", \"found_nodes\": [], "
            "\"graphs_searched\": [], \"queries_executed\": [], \"is_empty\": true/false}"
        )

def _get_gemini_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    token_path = os.environ.get("GEMINI_TOKEN_PATH", "credentials/token.json")
    if not os.path.exists(token_path):
        raise FileNotFoundError(f"Token file not found at {token_path}")
    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def _gemini_api_call(url: str, headers: dict, payload: dict) -> dict:
    """Синхронний HTTP виклик до Gemini API — запускається через asyncio.to_thread."""
    import requests as http_requests
    response = http_requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


async def call_gemini_agentic_loop(
    prompt: str,
    system_prompt: str,
    model: str,
    falkordb_session,
    max_iterations: int = 10
) -> tuple[str, list[str], list[str]]:
    """
    Запускає Gemini у агентному циклі з Function Calling для query_graph.
    HTTP-виклики до Gemini виконуються через asyncio.to_thread (не блокують event loop).
    Повертає: (final_text, queries_executed, graphs_searched)
    """
    creds = await asyncio.to_thread(_get_gemini_credentials)

    if not model.startswith("models/"):
        model = f"models/{model}"

    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent"
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}

    tools_declaration = [{
        "functionDeclarations": [{
            "name": "query_graph",
            "description": "Execute a Cypher query against FalkorDB graph database and return results.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {
                        "type": "STRING",
                        "description": "The Cypher query to execute"
                    },
                    "graphs": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "List of graph names to search (e.g. ['Grynya', 'Cursa4']). Defaults to current graph."
                    }
                },
                "required": ["query"]
            }
        }]
    }]

    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    queries_executed = []
    graphs_searched = set()
    final_text = ""

    for iteration in range(max_iterations):
        payload = {"contents": contents, "tools": tools_declaration}
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        print(f"[agentic_loop] Iteration {iteration + 1}/{max_iterations}")
        try:
            data = await asyncio.to_thread(_gemini_api_call, url, headers, payload)
        except Exception as api_err:
            print(f"[agentic_loop] Gemini API call failed: {api_err}")
            raise

        candidates = data.get("candidates", [])
        if not candidates:
            prompt_feedback = data.get("promptFeedback", {})
            block_reason = prompt_feedback.get("blockReason", "UNKNOWN")
            print(f"[agentic_loop] Empty candidates! blockReason={block_reason}, raw={json.dumps(data)[:300]}")
            break

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        finish_reason = candidate.get("finishReason", "STOP")

        print(f"[agentic_loop] finishReason={finish_reason}, parts_count={len(parts)}")

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not function_calls:
            final_text = "".join(p.get("text", "") for p in parts)
            print(f"[agentic_loop] Final text response ({len(final_text)} chars)")
            break

        contents.append({"role": "model", "parts": parts})

        function_responses = []
        for fc in function_calls:
            fc_name = fc["name"]
            fc_args = fc.get("args", {})
            cypher = fc_args.get("query", "")
            fc_graphs = fc_args.get("graphs", None)

            queries_executed.append(cypher)
            if fc_graphs:
                graphs_searched.update(fc_graphs)

            print(f"[agentic_loop] Executing {fc_name}: {cypher[:80]}...")
            try:
                result = await falkordb_session.call_tool(
                    "query_graph",
                    arguments={"query": cypher, "graphs": fc_graphs} if fc_graphs else {"query": cypher}
                )
                result_text = result.content[0].text if result.content else "{}"
            except Exception as e:
                result_text = json.dumps({"status": "error", "message": str(e)})

            function_responses.append({
                "functionResponse": {
                    "name": fc_name,
                    "response": {"result": result_text}
                }
            })

        contents.append({"role": "user", "parts": function_responses})
    else:
        final_text = f"Досягнуто ліміт ітерацій ({max_iterations}). Останні результати збережено."

    return final_text, queries_executed, list(graphs_searched)


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
async def research_graph(
    user_query: str,
    graphs: list = None,
    model: str = "gemini-2.5-flash",
    skill_name: str = "graph-research",
    save_to_graph: bool = True
) -> str:
    """
    Досліджує граф(и) FalkorDB за запитом користувача через Klim (Gemini Function Calling).
    Klim самостійно формує та виконує Cypher запити в агентному циклі.
    Зберігає вузол :Research в граф та повертає node_id + summary.

    user_query: запит/тема для дослідження (зазвичай перший запит користувача в сесії)
    graphs: список графів для пошуку (наприклад ['Grynya', 'Cursa4']). За замовчуванням — ['Grynya'].
    model: модель Gemini для використання (default: gemini-2.5-flash)
    skill_name: назва скілу в .gemini/antigravity/skills/<skill_name>/SKILL.md (default: graph-research)
    """
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession
    import datetime

    server_url = "http://grynya-mcp-server:8000/sse"
    print(f"[research_graph] Starting research for query: {user_query[:80]}...")
    print(f"[research_graph] Target graphs: {graphs}, skill: {skill_name}")

    skill_prompt = load_skill(skill_name)

    try:
        async with sse_client(server_url, headers={"Host": "localhost"}) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                print("[research_graph] FalkorDB session initialized.")

                graphs_to_search = graphs if graphs else ["Grynya"]
                search_prompt = (
                    f"Search graphs {graphs_to_search} for information relevant to this query:\n"
                    f"«{user_query}»\n\n"
                    f"Follow the instructions in your system prompt. Return valid JSON."
                )

                final_text, queries_executed, graphs_searched = await call_gemini_agentic_loop(
                    prompt=search_prompt,
                    system_prompt=skill_prompt,
                    model=model,
                    falkordb_session=session
                )

                if not graphs_searched:
                    graphs_searched = graphs_to_search

                now = datetime.datetime.now(datetime.timezone.utc)
                research_id = f"research_{now.strftime('%Y%m%d_%H%M%S')}"
                day_id = f"d_{now.strftime('%Y_%m_%d')}"

                def _strip_markdown_json(text: str) -> str:
                    """Видаляє ```json ... ``` або ``` ... ``` обгортку якщо є."""
                    text = text.strip()
                    if text.startswith("```"):
                        lines = text.split("\n")
                        # Відкидаємо перший рядок (```json або ```) і останній (```)
                        inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
                        if inner and inner[-1].strip() == "```":
                            inner = inner[:-1]
                        text = "\n".join(inner).strip()
                    return text

                clean_text = _strip_markdown_json(final_text) if final_text else ""
                try:
                    report_data = json.loads(clean_text)
                    summary = report_data.get("summary", clean_text[:300])
                    found_nodes = report_data.get("found_nodes", [])
                    source_node_ids = [n["id"] for n in found_nodes if "id" in n]
                    is_empty = report_data.get("is_empty", not bool(found_nodes))
                except (json.JSONDecodeError, TypeError):
                    summary = clean_text[:500] if clean_text else "Дослідження завершено, результати відсутні."
                    source_node_ids = []
                    is_empty = not bool(clean_text)

                node_data = {
                    "id": research_id,
                    "name": f"Research: {user_query[:60]}",
                    "query": user_query,
                    "summary": summary,
                    "full_report": final_text[:4000] if final_text else "",
                    "cypher_queries": json.dumps(queries_executed),
                    "graphs_searched": json.dumps(graphs_searched),
                    "source_node_ids": json.dumps(source_node_ids),
                    "is_empty": is_empty,
                    "time": now.isoformat()
                }

                if save_to_graph:
                    save_result = await session.call_tool("add_node", arguments={
                        "node_type": "Research",
                        "node_data": node_data,
                        "day_id": day_id,
                        "time": now.strftime("%H:%M:%S")
                    })
                    print(f"[research_graph] :Research node saved: {research_id}")

                    if source_node_ids:
                        links = [
                            {"source_id": research_id, "target_id": nid, "type": "SOURCED_FROM"}
                            for nid in source_node_ids[:20]
                        ]
                        await session.call_tool("batch_link_nodes", arguments={"links": links})
                        print(f"[research_graph] Linked {len(links)} source nodes.")
                else:
                    print(f"[research_graph] Skipping DB modifications: save_to_graph=False")

                return json.dumps({
                    "status": "success",
                    "research_node_id": research_id,
                    "summary": summary,
                    "graphs_searched": graphs_searched,
                    "queries_executed_count": len(queries_executed),
                    "source_nodes_found": len(source_node_ids),
                    "is_empty": is_empty
                })

    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"[research_graph] Error: {error_msg}")
        return json.dumps({"status": "error", "message": str(e)})


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

async def background_listener():
    import redis.asyncio as aioredis
    import os
    import json
    db_host = os.getenv("FALKORDB_HOST", "falkordb")
    db_port = int(os.getenv("FALKORDB_PORT", "6379"))
    
    while True:
        try:
            r = aioredis.Redis(host=db_host, port=db_port, decode_responses=False)
            await r.ping()
            print("[background_listener] Connected to Redis for klim:tasks")
            pubsub = r.pubsub()
            await pubsub.subscribe("klim:tasks")
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = message["data"]
                        if isinstance(data, bytes):
                            data = data.decode('utf-8')
                        payload = json.loads(data)
                        
                        session_id = payload.get("session_id")
                        task_type = payload.get("task_type")
                        query = payload.get("query")
                        
                        if task_type == "research_context":
                            print(f"[background_listener] Processing research task for session: {session_id}")
                            try:
                                result_str = await research_graph(user_query=query, save_to_graph=False)
                                result_data = json.loads(result_str)
                                
                                if result_data.get("status") == "error":
                                    resp_payload = {
                                        "session_id": session_id,
                                        "status": "error",
                                        "error_msg": result_data.get("message")
                                    }
                                else:
                                    # Ensure we just return context summary. Spec says 'context: Зібраний Markdown текст...'
                                    resp_payload = {
                                        "session_id": session_id,
                                        "status": "success",
                                        "context": result_data.get("summary", "Done.")
                                    }
                            except Exception as ex:
                                resp_payload = {
                                    "session_id": session_id,
                                    "status": "error",
                                    "error_msg": str(ex)
                                }
                                
                            await r.publish(f"klim:results:{session_id}", json.dumps(resp_payload))
                            print(f"[background_listener] Published result for {session_id}")
                    except Exception as e:
                        print(f"[background_listener] Error handling message: {e}")
        except Exception as e:
            print(f"[background_listener] Redis connection error, retrying in 5s: {e}")
            await asyncio.sleep(5)

def start_redis_listener_thread():
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(background_listener())
    
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

start_redis_listener_thread()

if __name__ == "__main__":
    import sys
    if "--sse" in sys.argv:
        mcp.run(transport="sse", host="0.0.0.0", port=8001)
    else:
        mcp.run()
