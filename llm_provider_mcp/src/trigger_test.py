import asyncio
import json
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

async def main():
    server_url = "http://localhost:8001/sse"
    print(f"Connecting to {server_url}...")
    
    async with sse_client(server_url, headers={"Host": "localhost"}) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            print("Session initialized.")
            
            # Start the task
            result = await session.call_tool("start_async_agent_task", {
                "prompt": "Say exactly: OK",
                "system_prompt": "You are a test bot.",
                "model": "gemini-2.5-flash"
            })
            
            if not result.content:
                print("Error: No content returned")
                return
                
            task_info = json.loads(result.content[0].text)
            print("Start task response:", task_info)
            task_id = task_info.get("task_id")
            
            if not task_id:
                print("Failed to get task_id")
                return
                
            # Poll status
            while True:
                status_res = await session.call_tool("check_task_status", {
                    "task_id": task_id
                })
                
                status_info = json.loads(status_res.content[0].text)
                state = status_info.get("status")
                
                print(f"Status: {state} | Logs length: {len(status_info.get('logs', ''))}")
                
                if state in ["completed", "failed", "cancelled"]:
                    print("\nFinal State Data:")
                    print(json.dumps(status_info, indent=2, ensure_ascii=False))
                    break
                    
                await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
