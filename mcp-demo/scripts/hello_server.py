"""
最小 MCP Server — 验证 SDK 和 Streamable HTTP 传输能跑通。
启动：uv run scripts/hello_server.py
访问：http://127.0.0.1:9000/mcp
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hello-world", host="127.0.0.1", port=9000)


@mcp.tool()
def hello(name: str) -> str:
    """向指定的人打招呼，用于验证 MCP Server 是否正常工作"""
    return f"你好, {name}! 欢迎使用 MCP 共享服务。"


@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加，用于验证参数传递和返回值"""
    return a + b


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
