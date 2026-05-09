from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from db import MessageRepository


class SearchHandler:
    """处理关键词历史搜索。"""

    def __init__(self, repo: MessageRepository, logger: logging.Logger | None = None) -> None:
        self.repo = repo
        self.logger = logger or logging.getLogger(__name__)

    async def handle(self, command_text: str, reply: Callable[[str], Awaitable[None]]) -> None:
        try:
            keyword, group_ids, since = await self._parse_command(command_text)
            if not keyword:
                await reply("用法：搜索 关键词 [群号或群名] [最近3天]")
                return

            records = await self.repo.search_messages(keyword=keyword, group_ids=group_ids, since=since, limit=20)
            if not records:
                await reply("没有找到匹配的历史消息。")
                return

            lines = [f"搜索结果：{keyword}"]
            for record in records:
                content = self._shorten(record["content"], 50)
                lines.append(
                    f"{record['timestamp']} | {record['group_name']} | {record['nickname']} | {content}"
                )

            await reply("\n".join(lines))
        except Exception:
            self.logger.exception("搜索处理失败")
            await reply("搜索失败，请稍后再试。")

    async def _parse_command(self, command_text: str) -> tuple[str, list[int] | None, str | None]:
        body = re.sub(r"^搜索\s+", "", command_text.strip(), count=1)
        body = body.strip()
        if not body:
            return "", None, None

        since: str | None = None
        time_match = re.search(r"\s+最近\s*(\d+)\s*(天|小时)$", body)
        if time_match:
            value = int(time_match.group(1))
            unit = time_match.group(2)
            delta = timedelta(days=value) if unit == "天" else timedelta(hours=value)
            since = (datetime.now() - delta).strftime("%Y-%m-%d %H:%M:%S")
            body = body[: time_match.start()].strip()

        group_ids: list[int] | None = None
        tokens = body.split()
        if len(tokens) >= 2:
            maybe_group = tokens[-1]
            matched_groups = await self.repo.resolve_groups(maybe_group)
            if len(matched_groups) == 1:
                group_ids = [int(matched_groups[0]["group_id"])]
                body = " ".join(tokens[:-1]).strip()
            elif maybe_group.isdigit():
                group_ids = [int(maybe_group)]
                body = " ".join(tokens[:-1]).strip()

        return body, group_ids, since

    @staticmethod
    def _shorten(text: str, max_length: int) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= max_length:
            return cleaned
        return f"{cleaned[:max_length]}..."
