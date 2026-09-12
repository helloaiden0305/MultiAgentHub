"""验证 Prompt Hub 仅加载报告复盘模板。"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = "http://127.0.0.1:9004/mcp"


async def main():
    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            prompts = await session.list_prompts()
            assert [prompt.name for prompt in prompts.prompts] == ["odm_report_agent"]

            result = await session.call_tool("list_prompt_templates", {})
            templates = json.loads(result.content[0].text)
            assert templates["count"] == 1
            assert templates["templates"][0]["name"] == "odm_report_agent"

            rendered = await session.get_prompt("odm_report_agent", arguments={
                "topic": "根据蓝牙回连失败现象生成问题复盘。",
                "style": "结构化",
            })
            text = rendered.messages[0].content.text
            assert "蓝牙回连失败" in text
            assert "结构化" in text

    print("Prompt Hub report-agent checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
