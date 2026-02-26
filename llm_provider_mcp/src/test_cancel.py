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
            
            # Start the task with a heavy prompt to ensure it takes a bit of time
            result = await session.call_tool("start_async_agent_task", arguments={
                "prompt": "Write a 1000-word essay about the history of quantum physics.",
                "system_prompt": "You are a detailed historian.",
                "model": "gemini-2.5-flash"
            })
            
            task_info = json.loads(result.content[0].text)
            task_id = task_info.get("task_id")
            print("Started task for cancellation test:", task_info)
            
            # Wait a moment, then cancel
            await asyncio.sleep(2)
            
            print(f"Cancelling task {task_id}...")
            cancel_result = await session.call_tool("cancel_agent_task", arguments={
                "task_id": task_id
            })
            print("Cancel result:", cancel_result.content[0].text)
            
            # Loop check status one or two times
            for _ in range(3):
                status_res = await session.call_tool("check_task_status", arguments={
                    "task_id": task_id
                })
                print("Status:", status_res.content[0].text)
                await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
