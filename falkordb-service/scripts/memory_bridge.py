import sys
import json
from falkordb import FalkorDB


def e_str(value):
    """Екранування рядків для Cypher."""
    if value is None:
        return '""'
    escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def main():
    try:
        input_data = sys.stdin.read()
        if input_data.startswith('\ufeff'):
            input_data = input_data[1:]
        if not input_data.strip():
            print(json.dumps({"status": "error", "message": "No input data provided"}))
            sys.exit(1)
        data = json.loads(input_data)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"Invalid JSON: {e}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Error reading input: {e}"}))
        sys.exit(1)

    try:
        db = FalkorDB(host='falkordb', port=6379)
        graph = db.select_graph('Grynya')
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Connection error: {e}"}))
        sys.exit(1)

    queries = []

    # 1. Session
    if 'session' in data:
        s = data['session']
        s_id = s.get('id')
        props = ", ".join([f"{k}: {e_str(v)}" for k, v in s.items() if k != 'id'])
        queries.append(f"MERGE (s:Session {{id: '{s_id}'}}) SET s += {{{props}}}")

    # 2. Chronology (Year -> Day)
    if 'chronology' in data:
        c = data['chronology']
        day_id = c.get('day_id')
        date_str = c.get('date')
        y_val = c.get('year')
        if day_id and date_str and y_val:
            y_id = f"year_{y_val}"
            month_num = date_str.split('-')[1]
            queries.append(f"MERGE (y:Year {{value: {y_val}, id: '{y_id}', name: '{y_val}'}})")
            queries.append(f"MERGE (d:Day {{date: '{date_str}', id: '{day_id}', name: '{date_str}'}})")
            queries.append(f"MATCH (y:Year {{id: '{y_id}'}}), (d:Day {{id: '{day_id}'}}) MERGE (y)-[:MONTH {{number: {month_num}}}]->(d)")

    # 3. Nodes + their relations
    if 'nodes' in data:
        for i, node in enumerate(data['nodes']):
            n_type = node.get('type')
            n_data = node.get('data', {})
            n_id = n_data.get('id')
            if not n_type or not n_id:
                continue

            props = ", ".join([f"{k}: {e_str(v)}" for k, v in n_data.items() if k != 'id'])
            queries.append(f"MERGE (n:{n_type} {{id: '{n_id}'}}) SET n += {{{props}}}")

            if 'chronology' in data and data['chronology'].get('time'):
                t = data['chronology']['time']
                d_id = data['chronology'].get('day_id')
                queries.append(
                    f"MATCH (n {{id: '{n_id}'}}), (d:Day {{id: '{d_id}'}}) "
                    f"MERGE (n)-[:HAPPENED_AT {{time: '{t}'}}]->(d)"
                )

            for rel in node.get('relations', []):
                r_type = rel.get('type')
                target_id = rel.get('target_id')
                r_props = rel.get('props')
                if not r_type or not target_id:
                    continue
                if r_props and isinstance(r_props, dict):
                    ps = ", ".join([f"{k}: {e_str(v)}" for k, v in r_props.items()])
                    queries.append(
                        f"MATCH (s {{id: '{n_id}'}}), (t {{id: '{target_id}'}}) "
                        f"MERGE (s)-[r:{r_type}]->(t) SET r += {{{ps}}}"
                    )
                else:
                    queries.append(
                        f"MATCH (s {{id: '{n_id}'}}), (t {{id: '{target_id}'}}) "
                        f"MERGE (s)-[:{r_type}]->(t)"
                    )

    # 4. NEXT chains
    if 'chronology' in data:
        for link in data['chronology'].get('next_links', []):
            src = link.get('source_id')
            tgt = link.get('target_id')
            if src and tgt:
                queries.append(
                    f"MATCH (s {{id: '{src}'}}), (t {{id: '{tgt}'}}) "
                    f"MERGE (s)-[:NEXT]->(t)"
                )

    # 5. LAST_EVENT
    if 'chronology' in data and data['chronology'].get('last_event_id') and 'session' in data:
        s_id = data['session']['id']
        last_id = data['chronology']['last_event_id']
        queries.append(f"MATCH (s:Session {{id: '{s_id}'}})-[rel:LAST_EVENT]->() DELETE rel")
        queries.append(
            f"MATCH (s:Session {{id: '{s_id}'}}), (last {{id: '{last_id}'}}) "
            f"MERGE (s)-[:LAST_EVENT]->(last)"
        )

    # Execute
    results = []
    for q in queries:
        try:
            graph.query(q)
            results.append({"query": q, "status": "success"})
        except Exception as e:
            results.append({"query": q, "status": "error", "message": str(e)})

    print(json.dumps({"status": "success", "results": results}))


if __name__ == "__main__":
    main()
