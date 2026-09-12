"""验证当前环境中的 Ark 对话接入点可达，不输出任何敏感配置。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.llm_gateway.doubao import DoubaoClient


async def main() -> None:
    env = dotenv_values(PROJECT_ROOT / ".env")
    client = DoubaoClient(str(env["ARK_API_KEY"]), str(env["ARK_BASE_URL"]))
    try:
        result = await client.chat_completion(
            model=str(env["ARK_CHAT_MODEL"]),
            messages=[{"role": "user", "content": "仅回复：ok"}],
            max_tokens=4,
        )
    finally:
        await client.close()

    assert result["content"].strip(), "模型没有返回内容"
    print("Direct endpoint connectivity passed.")


if __name__ == "__main__":
    asyncio.run(main())
