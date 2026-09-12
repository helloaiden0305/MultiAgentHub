"""
MCP 客户端管理器 — 管理到各 MCP Server 的 Streamable HTTP 连接。

为每次调用创建独立的短连接：简单可靠，适合演示场景。
生产环境可改为持久连接池以降低延迟。
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("gateway.mcp")


class MCPCallError(Exception):
    """MCP 调用失败。"""


def _unwrap_exception(exc: BaseException) -> BaseException:
    """递归解包 ExceptionGroup，提取最内层的真实异常。"""
    while hasattr(exc, "exceptions") and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


class MCPClientManager:
    def __init__(self, services: dict[str, dict]):
        self._services = services

    @property
    def service_names(self) -> list[str]:
        return list(self._services.keys())

    def _get_url(self, service: str) -> str:
        if service not in self._services:
            available = ", ".join(self._services.keys())
            raise MCPCallError(f"未知服务: {service}（可用: {available}）")
        return self._services[service]["url"]

    @asynccontextmanager
    async def _session(self, service: str):
        """创建到指定服务的临时 MCP 会话。"""
        url = self._get_url(service)
        try:
            async with streamablehttp_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except BaseException as e:
            real = _unwrap_exception(e)
            if real is not e:
                raise MCPCallError(
                    f"连接 {service} ({url}) 失败: {real}"
                ) from real
            raise

    @staticmethod
    def _parse_tool_result(result) -> dict | str:
        if result.isError:
            error_text = result.content[0].text if result.content else "Unknown error"
            raise MCPCallError(error_text)
        text = result.content[0].text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, IndexError):
            return text

    async def call_tool(self, service: str, tool: str, arguments: dict) -> Any:
        """调用 MCP Tool 并返回解析后的结果。"""
        logger.info("call_tool | service=%s tool=%s", service, tool)
        async with self._session(service) as session:
            result = await session.call_tool(tool, arguments)
        return self._parse_tool_result(result)

    async def get_prompt(self, service: str, prompt: str, arguments: dict) -> dict:
        """获取渲染后的 MCP Prompt。"""
        logger.info("get_prompt | service=%s prompt=%s", service, prompt)
        async with self._session(service) as session:
            result = await session.get_prompt(prompt, arguments=arguments)
        messages = []
        for msg in result.messages:
            if hasattr(msg.content, "text"):
                text = msg.content.text
            elif isinstance(msg.content, str):
                text = msg.content
            else:
                text = str(msg.content)
            messages.append({"role": msg.role, "content": text})
        return {"messages": messages}

    async def list_prompts(self, service: str) -> dict:
        """列出指定服务的所有 MCP Prompt。"""
        async with self._session(service) as session:
            result = await session.list_prompts()
        prompts = []
        for p in result.prompts:
            args = [
                {"name": a.name, "description": a.description or "", "required": a.required}
                for a in (p.arguments or [])
            ]
            prompts.append({
                "name": p.name,
                "description": p.description or "",
                "arguments": args,
            })
        return {"prompts": prompts, "count": len(prompts)}

    async def list_tools(self, service: str) -> dict:
        """列出指定服务的所有 MCP Tool。"""
        async with self._session(service) as session:
            result = await session.list_tools()
        tools = []
        for t in result.tools:
            tools.append({"name": t.name, "description": t.description or ""})
        return {"tools": tools, "count": len(tools)}

    async def check_health(self, service: str) -> dict:
        """检查指定服务是否可连接。"""
        try:
            async with self._session(service) as session:
                result = await session.list_tools()
            return {"service": service, "status": "ok", "tools": len(result.tools)}
        except Exception as e:
            return {"service": service, "status": "error", "message": str(e)}
