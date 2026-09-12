"""
会话记忆 + 用户画像 MCP Server。

暴露 Tool：
  对话记忆（按 project_id + session_id 隔离）：
    - save_memory      : 保存一条对话记录
    - recall_memory    : 召回最近 N 条历史（时间正序）
    - clear_memory     : 清空指定会话的记忆

  用户画像（按 user_id 跨项目共享）：
    - save_user_fact   : 写入一条用户事实
    - recall_user_facts: 读取该用户的全部事实
    - delete_user_fact : 删除一条用户事实

启动：uv run shared/memory_service/server.py
端口：9003
传输：Streamable HTTP → http://127.0.0.1:9003/mcp
"""

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from store import MemoryStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("memory-service")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

server = FastMCP("memory-service", host="127.0.0.1", port=9003)

store = MemoryStore(PROJECT_ROOT / "data" / "sqlite" / "memory.db")


@server.tool()
async def save_memory(
    project_id: str,
    session_id: str,
    role: str,
    content: str,
) -> dict:
    """保存一条对话记录。

    Args:
        project_id: 项目标识（与业务项目对应，用于隔离）
        session_id: 会话标识（同一用户/线程的对话）
        role: 发言角色，"user" 或 "assistant"
        content: 消息正文
    """
    logger.info(
        "save_memory | project=%s session=%s role=%s len=%d",
        project_id,
        session_id,
        role,
        len(content or ""),
    )
    return store.save_memory(project_id, session_id, role, content)


@server.tool()
async def recall_memory(
    project_id: str,
    session_id: str,
    last_n: int = 10,
) -> dict:
    """召回指定会话的最近 N 条历史记录（按时间从早到晚排列）。

    Args:
        project_id: 项目标识
        session_id: 会话标识
        last_n: 最多返回条数（默认 10，上限 500）
    """
    logger.info(
        "recall_memory | project=%s session=%s last_n=%d",
        project_id,
        session_id,
        last_n,
    )
    return store.recall_memory(project_id, session_id, last_n=last_n)


@server.tool()
async def clear_memory(
    project_id: str,
    session_id: str,
) -> dict:
    """清空指定会话在该项目下的全部记忆。"""
    logger.info("clear_memory | project=%s session=%s", project_id, session_id)
    return store.clear_memory(project_id, session_id)


# ── 用户画像 Tool（跨项目共享） ────────────────────────


@server.tool()
async def save_user_fact(
    user_id: str,
    fact_key: str,
    fact_value: str,
    source_project: str = "",
) -> dict:
    """保存一条用户事实（跨项目共享）。同一 user_id + fact_key 会覆盖旧值。

    Args:
        user_id: 用户标识（跨项目唯一）
        fact_key: 事实类别，如 "role"、"common_project"、"focus_module"
        fact_value: 事实内容，如 "测试工程师"、"X100"、"蓝牙连接"
        source_project: 写入来源项目（可选，便于溯源）
    """
    logger.info(
        "save_user_fact | user=%s key=%s source=%s",
        user_id, fact_key, source_project,
    )
    return store.save_user_fact(user_id, fact_key, fact_value, source_project)


@server.tool()
async def recall_user_facts(
    user_id: str,
) -> dict:
    """读取该用户的所有事实标签（任何项目都可调用）。

    Args:
        user_id: 用户标识
    """
    logger.info("recall_user_facts | user=%s", user_id)
    return store.recall_user_facts(user_id)


@server.tool()
async def delete_user_fact(
    user_id: str,
    fact_key: str,
) -> dict:
    """删除某条用户事实。

    Args:
        user_id: 用户标识
        fact_key: 要删除的事实类别
    """
    logger.info("delete_user_fact | user=%s key=%s", user_id, fact_key)
    return store.delete_user_fact(user_id, fact_key)


if __name__ == "__main__":
    logger.info("记忆服务 MCP Server 启动中...")
    logger.info("  SQLite: %s", PROJECT_ROOT / "data" / "sqlite" / "memory.db")
    server.run(transport="streamable-http")
