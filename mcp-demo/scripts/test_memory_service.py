"""
测试 会话记忆 MCP Server — 验证 save_memory / recall_memory / clear_memory。

前提：先启动记忆服务
    uv run shared/memory_service/server.py

运行：
    uv run scripts/test_memory_service.py
"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = "http://127.0.0.1:9003/mcp"

PROJECT_ID = "test-project"
SESSION_ID = "test-session-001"


def parse_tool_result(result) -> dict | str:
    text = result.content[0].text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return text


async def main():
    print(f"正在连接 记忆服务: {SERVER_URL}")

    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("连接成功!\n")

            tools = await session.list_tools()
            print(f"可用工具 ({len(tools.tools)} 个):")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:60]}")
            print()

            # ── 测试 1：保存对话记录 ──
            print("=" * 50)
            print("测试 1: save_memory (保存多条对话)")
            print("=" * 50)

            conversations = [
                ("user", "你好，请帮我介绍一下你们的产品"),
                ("assistant", "您好！我们有三款产品：基础版、专业版和企业版，请问您对哪款感兴趣？"),
                ("user", "专业版多少钱？"),
                ("assistant", "专业版每月 299 元，年付享 8 折优惠，即每年 2870 元。"),
            ]

            for role, content in conversations:
                result = await session.call_tool("save_memory", {
                    "project_id": PROJECT_ID,
                    "session_id": SESSION_ID,
                    "role": role,
                    "content": content,
                })
                if result.isError:
                    print(f"  [FAIL] 保存失败: {result.content[0].text}")
                    return
                data = parse_tool_result(result)
                print(f"  [OK] [{role}] id={data.get('id')} status={data.get('status')}")

            print()

            # ── 测试 2：召回历史记录 ──
            print("=" * 50)
            print("测试 2: recall_memory (召回最近对话)")
            print("=" * 50)

            result = await session.call_tool("recall_memory", {
                "project_id": PROJECT_ID,
                "session_id": SESSION_ID,
                "last_n": 10,
            })
            if result.isError:
                print(f"  [FAIL] 召回失败: {result.content[0].text}")
                return

            data = parse_tool_result(result)
            print(f"  返回条数: {data['count']}")
            for i, msg in enumerate(data["messages"]):
                preview = msg["content"][:50]
                print(f"  [{i}] {msg['role']}: {preview}...")

            if data["count"] == len(conversations):
                print(f"  → 验证通过：保存 {len(conversations)} 条，召回 {data['count']} 条，数量一致")
            else:
                print(f"  → 注意：保存 {len(conversations)} 条，但召回 {data['count']} 条（可能有历史数据）")
            print()

            # ── 测试 3：数据隔离验证 ──
            print("=" * 50)
            print("测试 3: recall_memory (不同会话隔离)")
            print("=" * 50)

            result = await session.call_tool("recall_memory", {
                "project_id": PROJECT_ID,
                "session_id": "non-existent-session",
                "last_n": 10,
            })
            if result.isError:
                print(f"  [FAIL] 召回失败: {result.content[0].text}")
            else:
                data = parse_tool_result(result)
                print(f"  不存在的会话返回条数: {data['count']}")
                if data["count"] == 0:
                    print("  → 隔离验证通过：不同 session_id 之间数据互不可见")
                else:
                    print("  → 隔离验证失败：不同 session_id 返回了数据")
            print()

            # ── 测试 4：清空记忆 ──
            print("=" * 50)
            print("测试 4: clear_memory (清空会话记忆)")
            print("=" * 50)

            result = await session.call_tool("clear_memory", {
                "project_id": PROJECT_ID,
                "session_id": SESSION_ID,
            })
            if result.isError:
                print(f"  [FAIL] 清空失败: {result.content[0].text}")
                return

            data = parse_tool_result(result)
            print(f"  状态: {data['status']}, 删除条数: {data['deleted']}")

            result = await session.call_tool("recall_memory", {
                "project_id": PROJECT_ID,
                "session_id": SESSION_ID,
                "last_n": 10,
            })
            data = parse_tool_result(result)
            print(f"  清空后召回条数: {data['count']}")
            if data["count"] == 0:
                print("  → 清空验证通过：记忆已完全清除")
            else:
                print("  → 清空验证失败：仍有残留数据")
            print()

            print("=" * 50)
            print("所有测试完成!")
            print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
