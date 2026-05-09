from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx
from openai import AsyncOpenAI


class AIClient:
    """负责异步调用 DeepSeek，并处理超长文本的分层摘要。"""

    SYSTEM_PROMPT = "你是一个QQ群消息助手，帮助用户高效获取群内重要信息。回答简洁，使用中文。"
    UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后再试"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.model = model
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.endswith("/v1"):
            normalized_base_url = f"{normalized_base_url}/v1"

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=normalized_base_url,
            http_client=self.http_client,
        )

    async def close(self) -> None:
        await self.http_client.aclose()

    async def chat(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        try:
            final_system_prompt = self.SYSTEM_PROMPT
            if system_prompt:
                final_system_prompt = f"{final_system_prompt}\n{system_prompt}"

            if self._estimate_tokens(user_prompt) > 4000:
                return await self._hierarchical_summary(user_prompt, final_system_prompt, temperature)

            return await self._complete(
                messages=[
                    {"role": "system", "content": final_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
        except Exception:
            self.logger.exception("AI 调用失败")
            return self.UNAVAILABLE_MESSAGE

    async def _hierarchical_summary(
        self,
        user_prompt: str,
        system_prompt: str,
        temperature: float,
    ) -> str:
        chunk_summaries: list[str] = []
        for index, chunk in enumerate(self._split_text(user_prompt, max_chars=6000), start=1):
            chunk_prompt = (
                f"下面是第 {index} 段超长群消息内容，请保留通知、待办、链接、文件和结论，忽略闲聊水消息，输出简洁摘要：\n\n"
                f"{chunk}"
            )
            chunk_summary = await self._complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": chunk_prompt},
                ],
                temperature=temperature,
            )
            chunk_summaries.append(chunk_summary)

        merged_prompt = (
            "下面是多段群消息摘要，请合并去重后输出最终结果，优先保留通知、文件、链接、时间线和待办事项：\n\n"
            + "\n\n".join(chunk_summaries)
        )
        return await self._complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": merged_prompt},
            ],
            temperature=temperature,
        )

    async def _complete(self, messages: list[dict[str, str]], temperature: float) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            timeout=30,
        )
        content = response.choices[0].message.content or ""
        return content.strip() or self.UNAVAILABLE_MESSAGE

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # 这里使用近似估算，足够满足分片判断。
        return max(1, len(text) // 2)

    @staticmethod
    def _split_text(text: str, max_chars: int) -> Iterable[str]:
        if len(text) <= max_chars:
            yield text
            return

        current = ""
        for line in text.splitlines():
            candidate = line if not current else f"{current}\n{line}"
            if len(candidate) <= max_chars:
                current = candidate
                continue

            if current:
                yield current
                current = ""

            while len(line) > max_chars:
                yield line[:max_chars]
                line = line[max_chars:]
            current = line

        if current:
            yield current
