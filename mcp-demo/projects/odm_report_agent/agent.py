"""
ODM 测试报告 / 问题复盘助手 Agent 编排逻辑。

流程：
  1. recall_memory     → 获取本轮会话中的报告 / 复盘对话
  2. recall_user_facts → 获取用户画像
  3. get_prompt        → odm_report_agent（含用户画像与历史）
  4. chat_completion   → 生成报告或复盘初稿
  5. save_memory       → 保存本轮用户指令与生成结果
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from typing import TypeVar

from gateway_client import GatewayClient

PROJECT_ID = "odm-report-agent"
T = TypeVar("T")


class AgentExecutionError(Exception):
    """业务调用失败时携带已完成的页面轨迹。"""

    def __init__(self, trace: dict):
        self.trace = trace
        super().__init__("报告生成链路调用失败")


class OdmReportAgent:
    def __init__(
        self,
        gw: GatewayClient,
        user_id: str,
        session_id: str,
        chain_id: str,
        style: str = "结构化",
    ):
        self._gw = gw
        self._user_id = user_id
        self._session_id = session_id
        self._chain_id = chain_id
        self.style = style
        self._steps: list[dict] = []

    async def handle_message(self, user_message: str) -> dict:
        """处理一条测试记录、缺陷单、日志摘要或复盘需求。"""
        started = time.monotonic()

        try:
            history_data, facts_data = await self._run_step(
                "memory_recall",
                "memory-service",
                "读取会话上下文",
                self._load_context(),
            )
            history_str = self._format_history(history_data)
            profile_str = self._format_profile(facts_data)

            prompt_data = await self._run_step(
                "prompt_render",
                "prompt-hub",
                "生成报告提示词",
                self._gw.get_prompt("prompt-hub", "odm_report_agent", {
                    "topic": user_message,
                    "style": self.style,
                    "user_profile": profile_str,
                    "history": history_str,
                }),
            )

            llm_result = await self._run_step(
                "llm_generate",
                "llm-gateway",
                "生成报告初稿",
                self._gw.call_tool("llm-gateway", "chat_completion", {
                    "messages": prompt_data["messages"],
                    "project_id": PROJECT_ID,
                    "model": "report-generation",
                    "temperature": 0.75,
                    "max_tokens": 2500,
                }),
            )
            text = llm_result.get("content", "抱歉，生成内容时出错了。")

            await self._run_step(
                "memory_save",
                "memory-service",
                "保存本轮会话",
                self._save_turn(user_message, text),
            )
        except Exception as error:
            raise AgentExecutionError(self._trace("failed", started)) from error

        return {"reply": text, "trace": self._trace("completed", started)}

    async def handle_message_stream(self, user_message: str):
        """按 SSE 事件生成报告；首段到达后业务页面即可开始渲染。"""
        started = time.monotonic()
        try:
            history_data, facts_data = await self._run_step(
                "memory_recall",
                "memory-service",
                "读取会话上下文",
                self._load_context(),
            )
            prompt_data = await self._run_step(
                "prompt_render",
                "prompt-hub",
                "生成报告提示词",
                self._gw.get_prompt("prompt-hub", "odm_report_agent", {
                    "topic": user_message,
                    "style": self.style,
                    "user_profile": self._format_profile(facts_data),
                    "history": self._format_history(history_data),
                }),
            )

            llm_started = time.monotonic()
            text_parts: list[str] = []
            async for event, data in self._gw.stream_llm_completion({
                "messages": prompt_data["messages"],
                "model": "report-generation",
                "temperature": 0.75,
                "max_tokens": 2500,
            }):
                if event == "delta":
                    content = str(data.get("content", ""))
                    text_parts.append(content)
                    yield "delta", {"content": content}
                    continue
                if event == "meta":
                    yield "meta", data
                    continue
                if event == "error":
                    self._steps.append({
                        "stage": "llm_generate",
                        "service": "llm-gateway",
                        "label": "流式生成报告初稿",
                        "status": "failed",
                        "duration_ms": self._duration_ms(llm_started),
                        "message": "模型流式服务调用失败，请检查可靠性治理页面。",
                    })
                    yield "error", {
                        "error": data.get("error", "生成失败，请稍后重试。"),
                        "trace": self._trace("failed", started),
                    }
                    return
                if event == "done":
                    ttft_ms = data.get("ttft_ms")
                    label = "流式生成报告初稿"
                    if ttft_ms is not None:
                        label += f"（TTFT {ttft_ms}ms）"
                    self._steps.append({
                        "stage": "llm_generate",
                        "service": "llm-gateway",
                        "label": label,
                        "status": "completed",
                        "duration_ms": self._duration_ms(llm_started),
                    })
                    text = "".join(text_parts)
                    await self._run_step(
                        "memory_save",
                        "memory-service",
                        "保存本轮会话",
                        self._save_turn(user_message, text),
                    )
                    yield "done", {
                        "reply": text,
                        "trace": self._trace("completed", started),
                        "llm": data,
                    }
                    return
            raise RuntimeError("流式服务未返回完成事件")
        except Exception:
            yield "error", {
                "error": "生成失败，请检查调用轨迹中的服务状态后重试。",
                "trace": self._trace("failed", started),
            }

    async def _load_context(self) -> tuple[dict, dict]:
        history = await self._gw.call_tool("memory-service", "recall_memory", {
            "project_id": PROJECT_ID,
            "session_id": self._session_id,
            "last_n": 10,
        })
        facts = await self._gw.call_tool("memory-service", "recall_user_facts", {
            "user_id": self._user_id,
        })
        return history, facts

    async def _save_turn(self, user_message: str, reply: str) -> None:
        await self._gw.call_tool("memory-service", "save_memory", {
            "project_id": PROJECT_ID,
            "session_id": self._session_id,
            "role": "user",
            "content": user_message,
        })
        await self._gw.call_tool("memory-service", "save_memory", {
            "project_id": PROJECT_ID,
            "session_id": self._session_id,
            "role": "assistant",
            "content": reply,
        })

    async def _run_step(
        self,
        stage: str,
        service: str,
        label: str,
        operation: Awaitable[T],
    ) -> T:
        started = time.monotonic()
        try:
            result = await operation
        except Exception:
            self._steps.append({
                "stage": stage,
                "service": service,
                "label": label,
                "status": "failed",
                "duration_ms": self._duration_ms(started),
                "message": "服务调用失败，请检查服务状态后重试。",
            })
            raise

        self._steps.append({
            "stage": stage,
            "service": service,
            "label": label,
            "status": "completed",
            "duration_ms": self._duration_ms(started),
        })
        return result

    def _trace(self, status: str, started: float) -> dict:
        return {
            "status": status,
            "chain_id": self._chain_id,
            "total_duration_ms": self._duration_ms(started),
            "steps": self._steps,
        }

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(1, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _format_history(data: dict) -> str:
        messages = data.get("messages", [])
        if not messages:
            return "(无历史对话)"
        lines = []
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "助手"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    @staticmethod
    def _format_profile(data: dict) -> str:
        facts = data.get("facts", [])
        if not facts:
            return "(无用户画像)"
        lines = [f"- {f['key']}: {f['value']}" for f in facts]
        return "\n".join(lines)
