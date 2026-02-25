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

def get_db():
    global db_client, db_graph
    if db_graph is None:
        db_host = os.getenv("FALKORDB_HOST", "localhost")
        if os.getenv("FALKORDB_PORT", None) is None:
            db_host = "falkordb"
        db_port = int(os.getenv("FALKORDB_PORT", "6379"))
        try:
            db_client = FalkorDB(host=db_host, port=db_port)
            db_graph = db_client.select_graph('Grynya')
            logger.info("FalkorDB is connected successfully lazily!")
        except Exception as e:
            logger.error(f"FalkorDB lazy connection failed: {e}")
            raise e
    return db_graph

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
# Передаємо host="0.0.0.0", щоб вимкнути захист DNS Rebinding у FastMCP 
# і дозволити підключення з інших Docker контейнерів
mcp = FastMCP("grynya-falkordb-mcp", host="0.0.0.0")

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
        graph = get_db()
        if not graph:
            return json.dumps({"status": "error", "message": "FalkorDB is not connected"})
        
        result = graph.query(query)
        res_list = []
        for record in result.result_set:
            res_list.append(record)
        return json.dumps({"status": "success", "results": res_list})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
async def create_session(session_id: str, name: str, topic: str, trigger: str, date: str, year: int) -> str:
    """Відкриває нову сесію в графі та налаштовує хронологічні вузли (Year, Day)."""
    try:
        graph = get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
        
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
            graph.query(q)
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
    try:
        graph = get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
        
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
            graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
            
    return json.dumps({"status": "success", "results": results})

@mcp.tool()
async def link_nodes(source_id: str, target_id: str, rel_type: str, props: dict = None) -> str:
    """Створює зв'язок між двома вузлами (наприклад NEXT)."""
    try:
        graph = get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
    
    if props:
        ps = ", ".join([f"{k}: {e_str(v)}" for k, v in props.items()])
        q = f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[r:{rel_type}]->(t) SET r += {{{ps}}}"
    else:
        q = f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[:{rel_type}]->(t)"
    try:
        graph.query(q)
        return json.dumps({"status": "success", "query": q})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
async def update_last_event(session_id: str, event_id: str) -> str:
    """Оновлює вказівник LAST_EVENT для конкретної сесії."""
    try:
        graph = get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
    
    queries = [
        f"MATCH (s:Session {{id: '{session_id}'}})-[rel:LAST_EVENT]->() DELETE rel",
        f"MATCH (s:Session {{id: '{session_id}'}}), (last {{id: '{event_id}'}}) MERGE (s)-[:LAST_EVENT]->(last)"
    ]
    results = []
    for q in queries:
        try:
            graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
    return json.dumps({"status": "success", "results": results})

@mcp.tool()
async def batch_add_nodes(node_type: str, nodes: list, day_id: str = None, time: str = None) -> str:
    """
    Додає декілька вузлів одного типу (наприклад, Entity) в граф за один раз.
    nodes - список словників (dict) з атрибутами вузлів, включаючи обов'язковий 'id'.
    """
    try:
        graph = get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

    queries = []
    for node_data in nodes:
        n_id = node_data.get('id')
        if not n_id:
            continue
        
        props = ", ".join([f"{k}: {e_str(v)}" for k, v in node_data.items() if k != 'id'])
        queries.append(f"MERGE (n:{node_type} {{id: '{n_id}'}}) SET n += {{{props}}}")
        
        if day_id and time and node_type != 'Entity':
            queries.append(f"MATCH (n {{id: '{n_id}'}}), (d:Day {{id: '{day_id}'}}) MERGE (n)-[:HAPPENED_AT {{time: '{time}'}}]->(d)")

    results = []
    for q in queries:
        try:
            graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})

    return json.dumps({"status": "success", "results": results})

@mcp.tool()
async def batch_link_nodes(links: list) -> str:
    """
    Створює декілька зв'язків між вузлами за один раз.
    links - список словників (dict), де кожен містить: "source_id", "target_id", "type" та опціонально "props".
    """
    try:
        graph = get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

    queries = []
    for link in links:
        source_id = link.get('source_id')
        target_id = link.get('target_id')
        rel_type = link.get('type')
        props = link.get('props')
        
        if not source_id or not target_id or not rel_type:
            continue
            
        if props:
            ps = ", ".join([f"{k}: {e_str(v)}" for k, v in props.items()])
            queries.append(f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[r:{rel_type}]->(t) SET r += {{{ps}}}")
        else:
            queries.append(f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[:{rel_type}]->(t)")

    results = []
    for q in queries:
        try:
            graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})

    return json.dumps({"status": "success", "results": results})
