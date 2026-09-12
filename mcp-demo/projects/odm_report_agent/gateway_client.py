"""
网关 HTTP 客户端 — ODM 报告复盘助手通过此客户端访问所有 MCP 共享服务。

用法：
    async with GatewayClient("xzy-odm-report-agent-key") as gw:
        result = await gw.call_tool("llm-gateway", "chat_completion", {...})
        prompt = await gw.get_prompt("prompt-hub", "odm_report_agent", {...})
"""

from __future__ import annotations

import json

import httpx

GATEWAY_URL = "http://127.0.0.1:8000"


class GatewayError(Exception):
    """网关调用失败，携带状态码和错误详情。"""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(f"[{status_code}] {detail}")


class GatewayClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = GATEWAY_URL,
        timeout: float = 60,
        chain_id: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._chain_id = chain_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GatewayClient:
        headers = {"X-API-Key": self._api_key}
        if self._chain_id:
            headers["X-Chain-ID"] = self._chain_id

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=headers,
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    @staticmethod
    def _check(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            detail = body.get("error") or body.get("detail") or resp.text
        except Exception:
            detail = resp.text or f"HTTP {resp.status_code}"
        raise GatewayError(resp.status_code, detail)

    async def call_tool(self, service: str, tool: str, arguments: dict | None = None) -> dict:
        """调用指定 MCP 服务的 Tool，返回解析后的 JSON。"""
        resp = await self._client.post(
            "/api/tool/call",
            json={"service": service, "tool": tool, "arguments": arguments or {}},
        )
        self._check(resp)
        return resp.json()

    async def get_prompt(self, service: str, prompt: str, arguments: dict | None = None) -> dict:
        """获取渲染后的 MCP Prompt。"""
        resp = await self._client.post(
            "/api/prompt/get",
            json={"service": service, "prompt": prompt, "arguments": arguments or {}},
        )
        self._check(resp)
        return resp.json()

    async def stream_llm_completion(self, arguments: dict):
        """读取网关 SSE 事件，浏览器不会直接接触内部 LLM Service。"""
        assert self._client is not None
        async with self._client.stream("POST", "/api/llm/stream", json=arguments) as response:
            if not response.is_success:
                detail = (await response.aread()).decode("utf-8", errors="replace")
                raise GatewayError(response.status_code, detail or "LLM 流式服务调用失败")
            current_event = "message"
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                yield current_event, data
                current_event = "message"

    async def list_prompts(self, service: str = "prompt-hub") -> dict:
        """列出指定服务的所有 Prompt。"""
        resp = await self._client.get("/api/prompt/list", params={"service": service})
        self._check(resp)
        return resp.json()
