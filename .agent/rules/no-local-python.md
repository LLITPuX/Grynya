# NO LOCAL PYTHON EXECUTION

> [!CRITICAL]
> **STRICT PROHIBITION**: DO NOT ATTEMPT TO RUN PYTHON SCRIPTS ON THE LOCAL HOST.

## The Rule
The user **DOES NOT** have a local Python environment installed.
Any attempt to run `python script.py` or `pip install` on the local machine will **FAIL**.

## The Solution: Docker Exec
ALWAYS execute scripts inside the running Docker containers.

**Incorrect:**
```bash
python scripts/verify_graph.py
```

**Correct:**
```bash
docker exec -it gemini-observer-bot-1 python scripts/verify_graph.py
```

## Protocol
1.  **Check Containers:** Verify which container is running (`docker ps`). usually `gemini-observer-bot-1` or `gemini-observer-thinking-1`.
2.  **Copy if needed:** If the script is new and not mounted, you might need to ensure it's in the volume or `docker cp` it (but usually the workspace is mounted).
3.  **Exec:** Run the command via `docker exec`.

## Exception
There are NO exceptions.
