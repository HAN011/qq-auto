from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from ai_client import AIClient
from db import MessageRepository


class SmartQueryHandler:
    """处理更自然语言的群消息检索与归纳。"""

    COMMAND_PREFIXES = ("查询 ", "查找 ", "帮我找", "帮我查", "问问 ", "看看 ")
    TIME_PATTERN = re.compile(r"最近\s*(\d+)\s*(天|小时)")
    CJK_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_#@.:-]{2,}")

    STOP_WORDS = {
        "帮我",
        "查一下",
        "查找",
        "查询",
        "帮忙",
        "找一下",
        "看看",
        "问问",
        "最近",
        "消息",
        "内容",
        "记录",
        "群里",
        "有没有",
        "有无",
        "哪些",
        "关于",
        "相关",
        "一下",
        "什么",
        "情况",
        "谁",
        "发过",
        "发了",
        "提到",
        "说过",
        "帮查",
        "帮找",
        "我想",
        "想看",
        "相关",
        "信息",
        "相关信息",
        "相关消息",
    }

    GENERIC_SUFFIXES = (
        "相关信息",
        "相关消息",
        "相关内容",
        "的信息",
        "的消息",
        "信息",
        "消息",
        "内容",
        "情况",
    )

    def __init__(
        self,
        repo: MessageRepository,
        ai_client: AIClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repo = repo
        self.ai_client = ai_client
        self.logger = logger or logging.getLogger(__name__)

    @classmethod
    def can_handle(cls, text: str) -> bool:
        normalized = text.strip()
        return any(normalized.startswith(prefix) for prefix in cls.COMMAND_PREFIXES)

    async def handle(
        self,
        command_text: str,
        message_type: str,
        current_group_id: int | None,
        reply: Callable[[str], Awaitable[None]],
    ) -> None:
        try:
            parsed = await self._parse_request(command_text, message_type, current_group_id)
            if not parsed["question"]:
                await reply(
                    "用法示例：\n"
                    "查询 最近3天谁发过报名链接\n"
                    "帮我找 保研群 复试时间\n"
                    "看看 最近6小时有哪些通知"
                )
                return

            records = await self._collect_records(
                question=parsed["question"],
                group_ids=parsed["group_ids"],
                since=parsed["since"],
            )
            if not records:
                await reply("没有找到和你的问题相关的历史消息。")
                return

            answer = await self._answer_question(
                question=parsed["question"],
                records=records,
                since_label=parsed["since_label"],
                scope_label=parsed["scope_label"],
            )
            await reply(answer)
        except Exception:
            self.logger.exception("智能查询处理失败")
            await reply("查询失败，请稍后再试。")

    async def _parse_request(
        self,
        command_text: str,
        message_type: str,
        current_group_id: int | None,
    ) -> dict[str, Any]:
        question = command_text.strip()
        for prefix in self.COMMAND_PREFIXES:
            if question.startswith(prefix):
                question = question[len(prefix) :].strip()
                break

        since: str | None = None
        since_label = "全部历史"
        time_match = self.TIME_PATTERN.search(question)
        if time_match:
            value = int(time_match.group(1))
            unit = time_match.group(2)
            delta = timedelta(days=value) if unit == "天" else timedelta(hours=value)
            since = (datetime.now() - delta).strftime("%Y-%m-%d %H:%M:%S")
            since_label = f"最近{value}{unit}"
            question = f"{question[: time_match.start()]} {question[time_match.end() :]}"

        group_ids: list[int] | None = None
        scope_label = "全部群"

        group_match = re.search(r"([^\s]+群)\s", question)
        if group_match and message_type == "private":
            group_query = group_match.group(1).strip()
            matched_groups = await self.repo.resolve_groups(group_query)
            if len(matched_groups) == 1:
                group_ids = [int(matched_groups[0]["group_id"])]
                scope_label = str(matched_groups[0]["group_name"])
                question = question.replace(group_match.group(1), "", 1).strip()

        if message_type == "group" and current_group_id:
            group_ids = [current_group_id]
            scope_label = "当前群"
        elif group_ids:
            scope_label = scope_label

        question = re.sub(r"\s+", " ", question).strip()
        return {
            "question": question,
            "group_ids": group_ids,
            "since": since,
            "since_label": since_label,
            "scope_label": scope_label,
        }

    async def _collect_records(
        self,
        question: str,
        group_ids: list[int] | None,
        since: str | None,
    ) -> list[dict[str, Any]]:
        keywords = self._extract_keywords(question)
        merged: dict[int, dict[str, Any]] = {}

        for keyword in keywords[:6]:
            rows = await self.repo.search_messages(
                keyword=keyword,
                group_ids=group_ids,
                since=since,
                limit=80,
            )
            for row in rows:
                merged[int(row["id"])] = row

        if not merged and since:
            # 当自然语言无法稳定拆出关键词时，退回到时间范围内的最近消息供 AI 判断。
            fallback_rows = await self.repo.fetch_messages_since(since=since, group_ids=group_ids, limit=120)
            for row in fallback_rows:
                merged[int(row["id"])] = row

        if not merged:
            matched_group_ids = await self._resolve_group_ids_from_keywords(keywords, group_ids)
            if matched_group_ids:
                fallback_rows = await self.repo.fetch_recent_messages(group_ids=matched_group_ids, limit=150)
                for row in fallback_rows:
                    merged[int(row["id"])] = row

        ordered = sorted(merged.values(), key=lambda item: item["timestamp"], reverse=True)
        return ordered[:120]

    async def _answer_question(
        self,
        question: str,
        records: list[dict[str, Any]],
        since_label: str,
        scope_label: str,
    ) -> str:
        lines = []
        for record in sorted(records, key=lambda item: item["timestamp"]):
            lines.append(
                f"[{record['timestamp']}] [{record['group_name']}] [{record['nickname']}] "
                f"[{record['msg_type']}] {record['content']}"
            )

        prompt = (
            f"请根据下面的群聊历史，回答用户的问题。\n"
            f"查询范围：{scope_label}\n"
            f"时间范围：{since_label}\n"
            f"用户问题：{question}\n\n"
            "要求：\n"
            "1. 只基于提供的聊天记录回答，不要编造。\n"
            "2. 先直接回答结论，再列出关键消息证据。\n"
            "3. 如果用户在找链接、文件、通知、截止时间、某个人的发言，要优先提取这些信息。\n"
            "4. 如果证据不足，请明确说“现有记录不足以确认”。\n"
            "5. 回答保持简洁，使用中文。\n\n"
            f"聊天记录：\n{chr(10).join(lines)}"
        )
        return await self.ai_client.chat(prompt)

    def _extract_keywords(self, question: str) -> list[str]:
        phrases = re.findall(r"[\"“](.*?)[\"”]", question)
        normalized = re.sub(r"[\"“”'，。？！、：:（）()]", " ", question)
        tokens: list[str] = []

        for phrase in phrases:
            phrase = phrase.strip()
            if phrase:
                tokens.append(phrase)

        for token in self.CJK_TOKEN_PATTERN.findall(normalized):
            cleaned = token.strip()
            if len(cleaned) < 2:
                continue
            tokens.extend(self._normalize_token(cleaned))

        deduped: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            deduped.append(token)

        return deduped

    async def _resolve_group_ids_from_keywords(
        self,
        keywords: list[str],
        scoped_group_ids: list[int] | None,
    ) -> list[int]:
        matched_ids: list[int] = []
        for keyword in keywords[:4]:
            groups = await self.repo.resolve_groups(keyword)
            for group in groups:
                group_id = int(group["group_id"])
                if scoped_group_ids and group_id not in scoped_group_ids:
                    continue
                if group_id not in matched_ids:
                    matched_ids.append(group_id)
        return matched_ids

    def _normalize_token(self, token: str) -> list[str]:
        candidates = [token]
        for suffix in self.GENERIC_SUFFIXES:
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                candidates.append(token[: -len(suffix)])

        normalized: list[str] = []
        for item in candidates:
            cleaned = item.strip()
            if len(cleaned) < 2:
                continue
            if cleaned in self.STOP_WORDS:
                continue
            normalized.append(cleaned)
        return normalized
