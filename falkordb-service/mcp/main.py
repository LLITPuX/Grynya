import os
import logging
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from falkordb import FalkorDB

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-falkordb")

# Ініціалізація FastAPI
app = FastAPI(title="FalkorDB MCP Server", version="0.1.0")

# Глобальні змінні бази даних
db_client = None
db_graph = None

@app.on_event("startup")
async def startup_event():
    global db_client, db_graph
    
    # Хост бази даних за замовчуванням 'falkordb' (всередині docker мережі)
    db_host = os.getenv("FALKORDB_HOST", "falkordb")
    db_port = int(os.getenv("FALKORDB_PORT", "6379"))
    
    try:
        db_client = FalkorDB(host=db_host, port=db_port)
        db_graph = db_client.select_graph('Grynya')
        # Робимо тестовий запит для перевірки з'єднання
        test_query = db_graph.query("MATCH (n:Year) RETURN n LIMIT 1")
        logger.info(f"FalkorDB is connected successfully! Tested query: {len(test_query.result_set)} results.")
    except Exception as e:
        logger.error(f"FalkorDB connection failed at startup: {e}")

from mcp.server.fastmcp import FastMCP

# Ініціалізація MCP Сервера
mcp = FastMCP("grynya-falkordb-mcp")

@app.get("/health")
async def health():
    return JSONResponse(content={
        "status": "ok", 
        "falkordb_connected": db_client is not None
    })

# Mount the MCP SSE application
app.mount("/", mcp.sse_app())


import json

def e_str(value):
    """Екранування рядків для Cypher."""
    if value is None:
        return '""'
    escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'

@mcp.tool()
async def query_graph(query: str) -> str:
    """Виконує Cypher запит до бази FalkorDB та повертає результат."""
    try:
        if not db_graph:
            return json.dumps({"status": "error", "message": "FalkorDB is not connected"})
        
        result = db_graph.query(query)
        res_list = []
        for record in result.result_set:
            res_list.append(record)
        return json.dumps({"status": "success", "results": res_list})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
async def create_session(session_id: str, name: str, topic: str, trigger: str, date: str, year: int) -> str:
    """Відкриває нову сесію в графі та налаштовує хронологічні вузли (Year, Day)."""
    if not db_graph: return json.dumps({"status": "error", "message": "DB disconnected"})
    
    queries = []
    # 1. Session
    props = f"name: {e_str(name)}, topic: {e_str(topic)}, status: 'active', trigger: {e_str(trigger)}"
    queries.append(f"MERGE (s:Session {{id: '{session_id}'}}) SET s += {{{props}}}")
    
    # 2. Year & Day
    month_num = date.split('-')[1]
    y_id = f"year_{year}"
    day_id = f"d_{date.replace('-','_')}"
    queries.append(f"MERGE (y:Year {{value: {year}, id: '{y_id}', name: '{year}'}})")
    queries.append(f"MERGE (d:Day {{date: '{date}', id: '{day_id}', name: '{date}'}})")
    queries.append(f"MATCH (y:Year {{id: '{y_id}'}}), (d:Day {{id: '{day_id}'}}) MERGE (y)-[:MONTH {{number: {month_num}}}]->(d)")
    
    results = []
    for q in queries:
        try:
            db_graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
            
    return json.dumps({"status": "success", "results": results})

@mcp.tool()
async def add_node(node_type: str, node_data: dict, day_id: str = None, time: str = None, relations: list = []) -> str:
    """
    Додає вузол в граф та зв'язує його з днем та іншими вузлами.
    relations is a list of dicts: [{"type": "PART_OF", "target_id": "session_01", "props": {}}]
    """
    if not db_graph: return json.dumps({"status": "error", "message": "DB disconnected"})
    n_id = node_data.get('id')
    if not n_id:
        return json.dumps({"status": "error", "message": "Missing node id"})
        
    queries = []
    props = ", ".join([f"{k}: {e_str(v)}" for k, v in node_data.items() if k != 'id'])
    queries.append(f"MERGE (n:{node_type} {{id: '{n_id}'}}) SET n += {{{props}}}")
    
    if day_id and time and node_type != 'Entity':
        queries.append(f"MATCH (n {{id: '{n_id}'}}), (d:Day {{id: '{day_id}'}}) MERGE (n)-[:HAPPENED_AT {{time: '{time}'}}]->(d)")
        
    for rel in relations:
        r_type = rel.get('type')
        target_id = rel.get('target_id')
        r_props = rel.get('props')
        if not r_type or not target_id: continue
        
        if r_props and isinstance(r_props, dict):
            ps = ", ".join([f"{k}: {e_str(v)}" for k, v in r_props.items()])
            queries.append(f"MATCH (s {{id: '{n_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[r:{r_type}]->(t) SET r += {{{ps}}}")
        else:
            queries.append(f"MATCH (s {{id: '{n_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[:{r_type}]->(t)")
            
    results = []
    for q in queries:
        try:
            db_graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
            
    return json.dumps({"status": "success", "results": results})

@mcp.tool()
async def link_nodes(source_id: str, target_id: str, rel_type: str, props: dict = None) -> str:
    """Створює зв'язок між двома вузлами (наприклад NEXT)."""
    if not db_graph: return json.dumps({"status": "error", "message": "DB disconnected"})
    
    if props:
        ps = ", ".join([f"{k}: {e_str(v)}" for k, v in props.items()])
        q = f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[r:{rel_type}]->(t) SET r += {{{ps}}}"
    else:
        q = f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[:{rel_type}]->(t)"
    try:
        db_graph.query(q)
        return json.dumps({"status": "success", "query": q})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
async def update_last_event(session_id: str, event_id: str) -> str:
    """Оновлює вказівник LAST_EVENT для конкретної сесії."""
    if not db_graph: return json.dumps({"status": "error", "message": "DB disconnected"})
    
    queries = [
        f"MATCH (s:Session {{id: '{session_id}'}})-[rel:LAST_EVENT]->() DELETE rel",
        f"MATCH (s:Session {{id: '{session_id}'}}), (last {{id: '{event_id}'}}) MERGE (s)-[:LAST_EVENT]->(last)"
    ]
    results = []
    for q in queries:
        try:
            db_graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
    return json.dumps({"status": "success", "results": results})

