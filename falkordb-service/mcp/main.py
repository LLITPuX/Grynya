import os
import logging
import json
import redis.asyncio as redis
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-falkordb")

# Ініціалізація FastAPI
app = FastAPI(title="FalkorDB MCP Server (Async)", version="0.1.0")

# Глобальні змінні бази даних
db_client = None
GRAPH_NAME = "Grynya"

async def get_db():
    global db_client
    if db_client is None:
        db_host = os.getenv("FALKORDB_HOST", "localhost")
        if os.getenv("FALKORDB_PORT", None) is None:
            db_host = "falkordb"
        db_port = int(os.getenv("FALKORDB_PORT", "6379"))
        try:
            db_client = redis.Redis(host=db_host, port=db_port, decode_responses=False)
            await db_client.ping()
            logger.info("FalkorDB (redis.asyncio) connected lazily!")
        except Exception as e:
            logger.error(f"FalkorDB lazy connection failed: {e}")
            raise e
    return db_client

@app.on_event("startup")
async def startup_event():
    global db_client
    db_host = os.getenv("FALKORDB_HOST", "falkordb")
    db_port = int(os.getenv("FALKORDB_PORT", "6379"))
    try:
        db_client = redis.Redis(host=db_host, port=db_port, decode_responses=False)
        await db_client.ping()
        logger.info(f"FalkorDB connected successfully at startup!")
    except Exception as e:
        logger.error(f"FalkorDB connection failed at startup: {e}")

from mcp.server.fastmcp import FastMCP

# Ініціалізація MCP Сервера
mcp = FastMCP("grynya-falkordb-mcp", host="0.0.0.0")

@app.get("/health")
async def health():
    return JSONResponse(content={
        "status": "ok", 
        "falkordb_connected": db_client is not None
    })

# Mount the MCP SSE application
app.mount("/", mcp.sse_app())

def e_str(value):
    """Екранування рядків для Cypher."""
    if value is None:
        return '""'
    escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'

def decode_falkor(item):
    if isinstance(item, bytes):
        try:
            return item.decode('utf-8')
        except:
            return str(item)
    elif isinstance(item, list):
        return [decode_falkor(i) for i in item]
    elif isinstance(item, dict):
        return {decode_falkor(k): decode_falkor(v) for k, v in item.items()}
    else:
        return item

def format_falkordb_results(res):
    res_decoded = decode_falkor(res)
    if len(res_decoded) < 3:
        return []
    
    headers = res_decoded[0]
    data = res_decoded[1]
    
    formatted_data = []
    if not isinstance(headers, list) or not isinstance(data, list):
        return []

    for row in data:
        row_dict = {}
        for idx, col_name in enumerate(headers):
            val = row[idx]
            
            # check if it's a node or edge
            is_graph_entity = False
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], list) and len(val[0]) == 2 and val[0][0] == 'id':
                is_graph_entity = True
                
            if is_graph_entity:
                obj_dict = {}
                for prop_pair in val:
                    if isinstance(prop_pair, list) and len(prop_pair) == 2:
                        k, v = prop_pair
                        if k == 'properties' and isinstance(v, list):
                            props_dict = {}
                            for p in v:
                                if isinstance(p, list) and len(p) == 2:
                                    props_dict[p[0]] = p[1]
                            obj_dict[k] = props_dict
                        else:
                            obj_dict[k] = v
                row_dict[col_name] = obj_dict
            else:
                row_dict[col_name] = val
        formatted_data.append(row_dict)
    return formatted_data


@mcp.tool()
async def query_graph(query: str) -> str:
    """Виконує Cypher запит до бази FalkorDB та повертає результат."""
    try:
        r = await get_db()
        res = await r.execute_command("GRAPH.QUERY", GRAPH_NAME, query)
        formatted = format_falkordb_results(res)
        return json.dumps({"status": "success", "results": formatted})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
async def create_session(session_id: str, name: str, topic: str, trigger: str, date: str, year: int) -> str:
    """Відкриває нову сесію в графі та налаштовує хронологічні вузли (Year, Day)."""
    try:
        r = await get_db()
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
            await r.execute_command("GRAPH.QUERY", GRAPH_NAME, q)
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
        r = await get_db()
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
            await r.execute_command("GRAPH.QUERY", GRAPH_NAME, q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
            
    return json.dumps({"status": "success", "results": results})


@mcp.tool()
async def link_nodes(source_id: str, target_id: str, rel_type: str, props: dict = None) -> str:
    """Створює зв'язок між двома вузлами (наприклад NEXT)."""
    try:
        r = await get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
    
    if props:
        ps = ", ".join([f"{k}: {e_str(v)}" for k, v in props.items()])
        q = f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[r:{rel_type}]->(t) SET r += {{{ps}}}"
    else:
        q = f"MATCH (s {{id: '{source_id}'}}), (t {{id: '{target_id}'}}) MERGE (s)-[:{rel_type}]->(t)"
    try:
        await r.execute_command("GRAPH.QUERY", GRAPH_NAME, q)
        return json.dumps({"status": "success", "query": q})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
async def update_last_event(session_id: str, event_id: str) -> str:
    """Оновлює вказівник LAST_EVENT для конкретної сесії."""
    try:
        r = await get_db()
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
    
    queries = [
        f"MATCH (s:Session {{id: '{session_id}'}})-[rel:LAST_EVENT]->() DELETE rel",
        f"MATCH (s:Session {{id: '{session_id}'}}), (last {{id: '{event_id}'}}) MERGE (s)-[:LAST_EVENT]->(last)"
    ]
    results = []
    for q in queries:
        try:
            await r.execute_command("GRAPH.QUERY", GRAPH_NAME, q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})
    return json.dumps({"status": "success", "results": results})


@mcp.tool()
async def batch_add_nodes(node_type: str, nodes: list, day_id: str = None, time: str = None) -> str:
    """
    Додає декілька вузлів одного типу (наприклад, Entity) в граф за один раз.
    """
    try:
        r = await get_db()
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
            await r.execute_command("GRAPH.QUERY", GRAPH_NAME, q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})

    return json.dumps({"status": "success", "results": results})


@mcp.tool()
async def batch_link_nodes(links: list) -> str:
    """
    Створює декілька зв'язків між вузлами за один раз.
    """
    try:
        r = await get_db()
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
            await r.execute_command("GRAPH.QUERY", GRAPH_NAME, q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})

    return json.dumps({"status": "success", "results": results})
