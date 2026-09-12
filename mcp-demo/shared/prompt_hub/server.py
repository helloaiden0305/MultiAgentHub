"""
Prompt 管理中心 MCP Server — 使用 MCP 原生 Prompt 能力管理模板。

暴露 Prompt（MCP 原生）：
  - odm_report_agent : ODM 测试报告 / 问题复盘模板

暴露 Tool：
  - list_prompt_templates : 列出所有模板及参数说明

启动：uv run shared/prompt_hub/server.py
端口：9004
传输：Streamable HTTP → http://127.0.0.1:9004/mcp
"""

import json
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("prompt-hub")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

server = FastMCP("prompt-hub", host="127.0.0.1", port=9004)


# ── 加载 JSON 模板 ────────────────────────────────────


def _load_templates() -> dict[str, dict]:
    templates = {}
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        with open(f, encoding="utf-8") as fp:
            tpl = json.load(fp)
        templates[tpl["name"]] = tpl
        logger.info("loaded template: %s (%s)", tpl["name"], f.name)
    return templates


_templates = _load_templates()


# ── MCP 原生 Prompt ───────────────────────────────────


@server.prompt()
def odm_report_agent(
    topic: str,
    style: str = "结构化",
    user_profile: str = "",
    history: str = "",
) -> str:
    """ODM 测试报告 / 问题复盘 — 根据用户需求生成结构化初稿"""
    tpl = _templates["odm_report_agent"]["template"]
    return tpl.format(
        topic=topic,
        style=style,
        user_profile=user_profile or "(无)",
        history=history or "(无)",
    )


# ── Tool：列出模板 ────────────────────────────────────


@server.tool()
async def list_prompt_templates() -> dict:
    """列出所有可用的 Prompt 模板及其参数说明。"""
    result = []
    for name, tpl in _templates.items():
        result.append({
            "name": name,
            "description": tpl.get("description", ""),
            "parameters": tpl.get("parameters", []),
        })
    return {"templates": result, "count": len(result)}


if __name__ == "__main__":
    logger.info("Prompt 中心 MCP Server 启动中...")
    logger.info("  模板目录: %s", TEMPLATES_DIR)
    logger.info("  已加载模板: %s", list(_templates.keys()))
    server.run(transport="streamable-http")
