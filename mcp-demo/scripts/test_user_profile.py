"""验证报告复盘业务使用的用户上下文读写能力。"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = "http://127.0.0.1:9003/mcp"
USER_ID = "report-agent-test-user"
SOURCE_PROJECT = "odm-report-agent"
FACT_KEYS = ("role", "common_project", "focus_module")


def parse(result) -> dict:
    return json.loads(result.content[0].text)


async def main():
    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for key in FACT_KEYS:
                await session.call_tool("delete_user_fact", {"user_id": USER_ID, "fact_key": key})

            for key, value in (("role", "测试工程师"), ("common_project", "X100"), ("focus_module", "蓝牙连接")):
                result = await session.call_tool("save_user_fact", {
                    "user_id": USER_ID,
                    "fact_key": key,
                    "fact_value": value,
                    "source_project": SOURCE_PROJECT,
                })
                assert not result.isError, result.content[0].text

            facts = parse(await session.call_tool("recall_user_facts", {"user_id": USER_ID}))
            assert facts["count"] == 3, facts
            assert {fact["source"] for fact in facts["facts"]} == {SOURCE_PROJECT}

            updated = await session.call_tool("save_user_fact", {
                "user_id": USER_ID,
                "fact_key": "common_project",
                "fact_value": "X100 EVT2",
                "source_project": SOURCE_PROJECT,
            })
            assert not updated.isError, updated.content[0].text

            for key in FACT_KEYS:
                await session.call_tool("delete_user_fact", {"user_id": USER_ID, "fact_key": key})

    print("Memory profile report-agent checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
