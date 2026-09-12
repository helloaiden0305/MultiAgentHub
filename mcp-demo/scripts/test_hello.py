"""
测试客户端 — 连接 Hello World MCP Server，调用 Tool 验证连通性。
前提：先启动 hello_server.py
运行：uv run scripts/test_hello.py
"""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    url = "http://127.0.0.1:9000/mcp"
    print(f"正在连接 MCP Server: {url}")

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("连接成功!\n")

            tools = await session.list_tools()
            print(f"可用工具 ({len(tools.tools)} 个):")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")
            print()

            result = await session.call_tool("hello", {"name": "ODM Agent Hub"})
            print(f"调用 hello: {result.content[0].text}")

            result = await session.call_tool("add", {"a": 17, "b": 25})
            print(f"调用 add:   {result.content[0].text}")

            print("\n所有测试通过!")


if __name__ == "__main__":
    asyncio.run(main())
