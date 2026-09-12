"""
ODM 测试报告 / 问题复盘助手 — 命令行交互入口。

通过 HTTP 网关调用共享服务：模板检索、用户画像、Prompt 中心、LLM、会话记忆。

启动前确保网关与各共享服务已运行：
    uv run scripts/start_all.py
    uv run scripts/init_knowledge.py
    uv run projects/odm_report_agent/app.py

命令：
    /clear     清空当前会话记忆
    /profile   查看当前用户画像
    /style 结构化|简洁|详细   设置后续输出风格（默认：结构化）
    /quit      退出
"""

import asyncio
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import OdmReportAgent
from gateway_client import GatewayClient

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(message)s",
)

API_KEY = "xzy-odm-report-agent-key"
PROJECT_ID = "odm-report-agent"
DEFAULT_USER = "demo-user"
ALLOWED_STYLES = frozenset({"结构化", "简洁", "详细", "复盘报告", "风险说明"})


async def cmd_clear(gw: GatewayClient, session_id: str):
    await gw.call_tool("memory-service", "clear_memory", {
        "project_id": PROJECT_ID,
        "session_id": session_id,
    })
    print("  [系统] 会话记忆已清空。\n")


async def cmd_profile(gw: GatewayClient, user_id: str):
    data = await gw.call_tool("memory-service", "recall_user_facts", {
        "user_id": user_id,
    })
    facts = data.get("facts", [])
    if not facts:
        print("  [系统] 暂无用户画像。\n")
        return
    print("  [系统] 当前用户画像：")
    for f in facts:
        source = f" (来源: {f['source']})" if f.get("source") else ""
        print(f"    • {f['key']}: {f['value']}{source}")
    print()


async def main():
    user_id = DEFAULT_USER
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    style = "结构化"

    print("=" * 55)
    print("  XZY ODM 测试报告 / 问题复盘助手")
    print("=" * 55)
    print(f"  用户: {user_id}  |  会话: {session_id}  |  文风: {style}")
    print("  命令: /clear  /profile  /style <结构化|简洁|详细|复盘报告|风险说明>  /quit")
    print("=" * 55)
    print()

    async with GatewayClient(API_KEY) as gw:
        agent = OdmReportAgent(gw, user_id=user_id, session_id=session_id, style=style)

        print("[助手] 你好。请提供测试记录、缺陷现象、日志摘要或复盘需求。\n")

        while True:
            try:
                user_input = input("[你]   ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n  再见！")
                break

            if not user_input:
                continue

            low = user_input.lower()
            if low in ("/quit", "/exit", "/q"):
                print("\n  再见！")
                break
            if low == "/clear":
                await cmd_clear(gw, session_id)
                continue
            if low == "/profile":
                await cmd_profile(gw, user_id)
                continue
            if low.startswith("/style"):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or parts[1] not in ALLOWED_STYLES:
                    print(f"  [系统] 用法: /style {'/'.join(sorted(ALLOWED_STYLES))}\n")
                    continue
                style = parts[1]
                agent.style = style
                print(f"  [系统] 文风已设为：{style}\n")
                continue

            try:
                print("[助手] 创作中...", end="", flush=True)
                reply = await agent.handle_message(user_input)
                print(f"\r[助手] {reply}\n")
            except Exception as e:
                print(f"\r[助手] 出错了: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
