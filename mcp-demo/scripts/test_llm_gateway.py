"""
测试 LLM 网关 MCP Server — 验证 chat_completion、embedding、list_models。

前提：先启动 LLM 网关
    uv run shared/llm_gateway/server.py

运行：
    uv run scripts/test_llm_gateway.py
"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = "http://127.0.0.1:9001/mcp"


def parse_tool_result(result) -> dict | str:
    """解析 MCP Tool 调用结果。"""
    text = result.content[0].text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return text


async def main():
    print(f"正在连接 LLM 网关: {SERVER_URL}")

    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("连接成功!\n")

            # ── 列出可用工具 ──
            tools = await session.list_tools()
            print(f"可用工具 ({len(tools.tools)} 个):")
            for t in tools.tools:
                print(f"  - {t.name}")
            print()

            # ── 测试 list_models ──
            print("=" * 50)
            print("测试 1: list_models")
            print("=" * 50)
            result = await session.call_tool("list_models", {})
            data = parse_tool_result(result)
            if isinstance(data, dict) and "models" in data:
                for m in data["models"]:
                    tag = " (默认)" if m.get("is_default") else ""
                    print(f"  {m['name']}{tag}: {m['description']}")
            else:
                print(f"  结果: {data}")
            print()

            # ── 测试 chat_completion ──
            print("=" * 50)
            print("测试 2: chat_completion")
            print("=" * 50)
            result = await session.call_tool("chat_completion", {
                "messages": [{"role": "user", "content": "用一句话介绍 MCP 协议是什么。"}],
                "project_id": "test",
                "model": "auto",
            })
            if result.isError:
                print(f"  错误: {result.content[0].text}")
            else:
                data = parse_tool_result(result)
                print(f"  模型:  {data['model']}")
                print(f"  回复:  {data['content']}")
                print(f"  用量:  {data['usage']}")
            print()

            # ── 测试 embedding ──
            print("=" * 50)
            print("测试 3: embedding")
            print("=" * 50)
            result = await session.call_tool("embedding", {
                "texts": ["你好世界", "MCP 共享服务"],
                "project_id": "test",
            })
            if result.isError:
                print(f"  错误: {result.content[0].text}")
                print("  提示: 请检查 .env 中的 ARK_EMBEDDING_MODEL 是否正确")
            else:
                data = parse_tool_result(result)
                print(f"  向量数量: {data['embeddings'].__len__()}")
                print(f"  向量维度: {data['dimensions']}")
                for i, vec in enumerate(data["embeddings"]):
                    preview = ", ".join(f"{v:.4f}" for v in vec[:5])
                    print(f"  向量 {i}: [{preview}, ...]")
            print()

            print("所有测试完成!")


if __name__ == "__main__":
    asyncio.run(main())
