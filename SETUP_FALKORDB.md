# Розгортання FalkorDB (Пам'ять Агента)

Інструкція для створення інфраструктури графової пам'яті з нуля.

## Архітектура

Три контейнери:

| Контейнер | Образ | Порти | Призначення |
|---|---|---|---|
| `falkordb` | `falkordb/falkordb:latest` | 6379, 3000 (UI) | Графова БД (Redis-сумісна) |
| `grynya-bridge` | python:3.11-slim + `falkordb` | — | Виконує `memory_bridge.py`: приймає JSON → генерує Cypher → пише у граф |
| `ollama` | `ollama/ollama:latest` | 11434 | Локальні ембедінги (embeddinggemma або інша модель) |

## Файлова структура

Створи наступну структуру у корені проєкту:

```
falkordb-service/
├── docker-compose.yml
├── bridge.Dockerfile
├── scripts/
│   └── memory_bridge.py
└── debug/                  ← для тимчасових payload-файлів (.gitignore'd)
```

## 1. docker-compose.yml

```yaml
version: '3.8'

services:
  falkordb:
    image: falkordb/falkordb:latest
    container_name: falkordb
    ports:
      - "6379:6379"
      - "3000:3000"
    volumes:
      - falkordb_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - grynya-net

  bridge:
    build:
      context: .
      dockerfile: bridge.Dockerfile
    container_name: grynya-bridge
    depends_on:
      falkordb:
        condition: service_healthy
    volumes:
      - ./scripts:/app/scripts
    restart: unless-stopped
    stdin_open: true
    networks:
      - grynya-net

  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped
    networks:
      - grynya-net

volumes:
  falkordb_data:
    driver: local
  ollama_data:
    driver: local

networks:
  grynya-net:
    driver: bridge
```

## 2. bridge.Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir falkordb
COPY scripts/ /app/scripts/
CMD ["tail", "-f", "/dev/null"]
```

## 3. memory_bridge.py

Скрипт-міст: приймає JSON через STDIN, перетворює на Cypher-запити, виконує у графі `Grynya`.

```python
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

    # 2. Chronology (Year → Day)
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
```

## 4. .gitignore (для debug/)

Створи `falkordb-service/.gitignore`:

```
debug/*.json
```

## 5. Запуск

```powershell
cd falkordb-service
docker compose up -d --build
```

## 6. Перевірка

```powershell
# FalkorDB
docker exec falkordb redis-cli PING
# → PONG

# Bridge з'єднання з БД
docker exec grynya-bridge python -c "from falkordb import FalkorDB; db = FalkorDB(host='falkordb', port=6379); print('OK')"
# → OK

# Ollama
docker exec ollama ollama list
# → (порожній або список моделей)
```

Завантаження моделі ембедінгів (одноразово):

```powershell
docker exec ollama ollama pull embeddinggemma
```

## 7. Як працює запис у граф

Агент **не використовує** `redis-cli` для запису. Алгоритм:

1. Агент формує JSON-payload згідно зі схемою графа.
2. Зберігає його у тимчасовий файл `falkordb-service/debug/payload.json`.
3. Виконує:

```powershell
docker cp "falkordb-service/debug/payload.json" grynya-bridge:/tmp/payload.json
docker exec grynya-bridge python -c "import json,subprocess; payload=open('/tmp/payload.json','rb').read(); p=subprocess.Popen(['python','/app/scripts/memory_bridge.py'], stdin=subprocess.PIPE); p.communicate(input=payload)"
```

4. Видаляє тимчасовий файл.

Це гарантує коректне збереження кирилиці (UTF-8) незалежно від налаштувань терміналу хост-машини.

## 8. Як працює читання з графа

Для читання дозволено використовувати `redis-cli` напряму:

```powershell
docker exec falkordb redis-cli GRAPH.QUERY Grynya "MATCH (n) RETURN labels(n), count(n)"
```

## 9. FalkorDB Browser UI

Після запуску доступний за адресою: **http://localhost:3000**

Дозволяє візуально переглядати граф та виконувати Cypher-запити.

## 10. Ollama API

Ембедінги доступні через HTTP:

```powershell
curl http://localhost:11434/api/embeddings -d '{"model":"embeddinggemma","prompt":"текст для ембедінгу"}'
```

Або з Python (всередині контейнера з доступом до `grynya-net`):

```python
import httpx
resp = httpx.post("http://ollama:11434/api/embeddings", json={"model": "embeddinggemma", "prompt": "текст"})
embedding = resp.json()["embedding"]  # list[float]
```
