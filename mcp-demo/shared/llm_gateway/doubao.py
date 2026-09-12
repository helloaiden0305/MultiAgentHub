"""
豆包 API 调用封装 — chat 用 OpenAI SDK，embedding 用 httpx 直调。

- Chat: 走 OpenAI 兼容的 /chat/completions 端点
- Embedding: 走火山引擎专有的 /embeddings/multimodal 端点
  （多模态模型的路径和输入格式与 OpenAI SDK 不兼容，需 httpx 直调）
"""

import logging

import httpx
from openai import AsyncOpenAI, APIError

logger = logging.getLogger("llm-gateway.doubao")


class UpstreamCallError(RuntimeError):
    """保留可用于路由决策的上游错误分类，不向业务侧暴露敏感信息。"""

    def __init__(self, message: str, *, retryable: bool, error_type: str) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_type = error_type


class OpenAICompatibleClient:
    """OpenAI 兼容协议客户端；Ark、DeepSeek 等 Endpoint 可复用此适配器。"""

    def __init__(self, api_key: str, base_url: str, timeout_ms: int = 60000):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=max(0.1, timeout_ms / 1000),
        )

    async def close(self) -> None:
        """关闭一次性动态 Endpoint 客户端。"""
        await self.client.close()

    async def chat_completion(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> dict:
        """调用豆包对话接口（OpenAI 兼容格式）。

        Returns:
            {"content": str, "usage": {"prompt_tokens": int, ...}}
        """
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except APIError as error:
            status_code = getattr(error, "status_code", None)
            retryable = (
                status_code is None
                or status_code == 429
                or isinstance(status_code, int) and status_code >= 500
            )
            error_type = "rate_limit" if status_code == 429 else (
                "upstream_5xx" if isinstance(status_code, int) and status_code >= 500 else "connection"
            )
            logger.warning("OpenAI-compatible Chat API error: %s", error)
            raise UpstreamCallError(
                "上游模型服务调用失败",
                retryable=retryable,
                error_type=error_type,
            ) from error

        choice = response.choices[0]
        usage = response.usage
        return {
            "content": choice.message.content or "",
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
        }

    async def stream_chat_completion(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        """逐段返回上游模型内容；首个 delta 用于计算真实 TTFT。"""
        try:
            stream = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                if getattr(chunk, "choices", None):
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield {"type": "delta", "content": delta}
                usage = getattr(chunk, "usage", None)
                if usage:
                    yield {
                        "type": "usage",
                        "usage": {
                            "prompt_tokens": usage.prompt_tokens or 0,
                            "completion_tokens": usage.completion_tokens or 0,
                            "total_tokens": usage.total_tokens or 0,
                        },
                    }
        except APIError as error:
            status_code = getattr(error, "status_code", None)
            retryable = (
                status_code is None
                or status_code == 429
                or isinstance(status_code, int) and status_code >= 500
            )
            error_type = "rate_limit" if status_code == 429 else (
                "upstream_5xx" if isinstance(status_code, int) and status_code >= 500 else "connection"
            )
            logger.warning("OpenAI-compatible streaming API error: %s", error)
            raise UpstreamCallError(
                "上游模型流式调用失败",
                retryable=retryable,
                error_type=error_type,
            ) from error

    async def embedding(self, model: str, texts: list[str]) -> dict:
        """调用豆包 Embedding 接口。

        自动判断模型类型：
        - 多模态模型 (含 "vision"): httpx → /embeddings/multimodal
        - 纯文本模型: OpenAI SDK → /embeddings

        Returns:
            {"embeddings": [[float, ...], ...], "dimensions": int}
        """
        if "vision" in model:
            return await self._embedding_multimodal(model, texts)
        return await self._embedding_text(model, texts)

    async def _embedding_text(self, model: str, texts: list[str]) -> dict:
        """标准 OpenAI 兼容的文本 embedding（/embeddings 端点）。"""
        try:
            response = await self.client.embeddings.create(
                model=model,
                input=texts,
            )
        except APIError as error:
            logger.warning("OpenAI-compatible Embedding API error: %s", error)
            raise RuntimeError("Embedding 服务调用失败") from error

        vectors = [item.embedding for item in response.data]
        return {
            "embeddings": vectors,
            "dimensions": len(vectors[0]) if vectors else 0,
        }

    async def _embedding_multimodal(self, model: str, texts: list[str]) -> dict:
        """多模态 embedding（/embeddings/multimodal 端点）。

        火山引擎多模态 embedding 返回格式: {"data": {"embedding": [...]}}
        每次调用返回一个向量，所以逐条文本分别调用。
        """
        url = f"{self.base_url}/embeddings/multimodal"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        vectors = []
        async with httpx.AsyncClient(timeout=30.0) as http:
            for text in texts:
                payload = {
                    "model": model,
                    "input": [{"type": "text", "text": text}],
                }
                try:
                    resp = await http.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    body = e.response.text
                    logger.error("Multimodal embedding error: %d %s", e.response.status_code, body)
                    raise RuntimeError(f"豆包多模态 Embedding API 调用失败: {body}") from e

                data = resp.json()
                emb_data = data["data"]
                if isinstance(emb_data, list):
                    vectors.append(emb_data[0]["embedding"])
                elif isinstance(emb_data, dict):
                    vectors.append(emb_data["embedding"])
                else:
                    raise RuntimeError(f"豆包多模态 Embedding 返回格式异常: data type={type(emb_data)}")

        return {
            "embeddings": vectors,
            "dimensions": len(vectors[0]) if vectors else 0,
        }


# 兼容现有 import；后续业务代码统一使用更准确的协议名称。
DoubaoClient = OpenAICompatibleClient
