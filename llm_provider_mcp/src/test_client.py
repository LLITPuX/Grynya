import asyncio
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

async def main():
    # URL для підключення до MCP-сервера по SSE.
    # Так як тест будемо запускати прямо всередині контейнера llm-provider-mcp, 
    # використовуємо внутрішнє докерівське ім'я:
    server_url = "http://grynya-mcp-server:8000/sse"
    
    print(f"Connecting to {server_url}...")
    
    try:
        # Відкриваємо SSE-потоки підключення
        # Додаємо Host: localhost, щоб задовольнити TransportSecurityMiddleware у FastMCP
        async with sse_client(server_url, headers={"Host": "localhost"}) as streams:
            print("SSE connection established.")
            # Ініціалізуємо MCP сесію
            async with ClientSession(streams[0], streams[1]) as session:
                print("MCP Session instance created, initializing protocol...")
                await session.initialize()
                print("Session initialized successfully.")
                
                # Fetch available tools
                tools_response = await session.list_tools()
                print(f"\nDiscovered {len(tools_response.tools)} tools:")
                for i, tool in enumerate(tools_response.tools, 1):
                    # description maybe long, only showing the first line
                    desc = tool.description.split('\n')[0] if tool.description else 'No description'
                    print(f" {i}. [{tool.name}]: {desc}")
                    
    except Exception as e:
        import traceback
        print(f"Error during MCP client execution: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
