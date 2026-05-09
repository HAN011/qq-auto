from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from ai_client import AIClient
from db import MessageRepository


class CrossGroupHandler:
    """处理跨群同主题汇总。"""

    def __init__(
        self,
        repo: MessageRepository,
        ai_client: AIClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repo = repo
        self.ai_client = ai_client
        self.logger = logger or logging.getLogger(__name__)

    async def handle(self, command_text: str, reply: Callable[[str], Awaitable[None]]) -> None:
        try:
            keyword = re.sub(r"^汇总\s+", "", command_text.strip(), count=1).strip()
            if not keyword:
                await reply("用法：汇总 话题关键词")
                return

            records = await self.repo.search_messages(keyword=keyword, group_ids=None, since=None, limit=200)
            if not records:
                await reply("没有找到相关话题消息。")
                return

            lines = []
            for record in sorted(records, key=lambda item: item["timestamp"]):
                lines.append(
                    f"[{record['timestamp']}] [{record['group_name']}] {record['nickname']}: {record['content']}"
                )

            prompt = (
                f"请基于以下多个群关于“{keyword}”的消息记录，生成跨群汇总。\n"
                "要求：\n"
                "1. 去除重复信息。\n"
                "2. 按时间线排列关键进展。\n"
                "3. 按群维度补充差异点。\n"
                "4. 给出最终要点与值得继续关注的事项。\n\n"
                f"聊天记录：\n{chr(10).join(lines)}"
            )
            result = await self.ai_client.chat(prompt)
            await reply(result)
        except Exception:
            self.logger.exception("跨群汇总处理失败")
            await reply("跨群汇总失败，请稍后再试。")
