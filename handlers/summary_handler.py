from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from ai_client import AIClient
from db import MessageRepository


class SummaryHandler:
    """处理手动摘要与定时摘要。"""

    DOMAIN_RULES = {
        "保研": {
            "keywords": {
                "保研",
                "推免",
                "预推免",
                "九推",
                "夏令营",
                "入营",
                "优营",
                "直博",
                "导师",
                "套磁",
                "简历",
                "机试",
                "面试",
                "考核",
                "报名",
                "截止",
                "offer",
                "夏令营报名",
            },
            "focus_prompt": (
                "如果内容与保研相关，请额外重点提取：\n"
                "1. 夏令营、预推免、九推、直博等项目机会。\n"
                "2. 学校、学院、专业、导师、项目名称。\n"
                "3. 简历、套磁、推荐信、成绩单、材料准备要求。\n"
                "4. 机试、面试、考核形式与经验。\n"
                "5. 报名开始/截止、入营、优营、通知发布时间。\n"
                "6. 重要文件、表格、通知链接和待办动作。\n"
                "7. 若有人给出经验建议，要浓缩成可执行建议。"
            ),
            "sections_prompt": (
                "除通用结构外，如果识别出保研主题，请优先补充这些小节；没有内容写“暂无”：\n"
                "🏕️ 夏令营 / 预推免 / 九推\n"
                "- 条目\n"
                "📄 简历 / 套磁 / 材料\n"
                "- 条目\n"
                "🧪 机试 / 面试 / 考核\n"
                "- 条目\n"
                "⏰ 时间节点 / 截止事项\n"
                "- 条目"
            ),
        }
    }

    GENERIC_OUTPUT = (
        "输出格式：\n"
        "📌 话题一\n"
        "- 要点\n"
        "📌 话题二\n"
        "- 要点\n"
        "📎 重要文件与链接\n"
        "- 条目\n"
        "👀 值得关注\n"
        "- 条目"
    )

    def __init__(
        self,
        repo: MessageRepository,
        ai_client: AIClient,
        summary_target_qq: int,
        default_lookback_hours: int,
        send_private: Callable[[int, str], Awaitable[None]],
        logger: logging.Logger | None = None,
    ) -> None:
        self.repo = repo
        self.ai_client = ai_client
        self.summary_target_qq = summary_target_qq
        self.default_lookback_hours = default_lookback_hours
        self.send_private = send_private
        self.logger = logger or logging.getLogger(__name__)

    async def handle_manual(
        self,
        command_text: str,
        source: dict[str, Any],
        reply: Callable[[str], Awaitable[None]],
    ) -> None:
        try:
            hours = self._parse_hours(command_text)
            topic = self._parse_topic(command_text)
            group_ids = None
            scope_label = "全部监听群"

            if source["message_type"] == "group":
                group_ids = [source["group_id"]]
                scope_label = source["group_name"]

            if topic:
                scope_label = f"{scope_label} / 主题：{topic}"

            summary_text = await self._build_summary(
                hours=hours,
                group_ids=group_ids,
                scope_label=scope_label,
                topic=topic,
            )
            await reply(summary_text)

            sender_user_id = source.get("user_id")
            if not (source["message_type"] == "private" and sender_user_id == self.summary_target_qq):
                push_text = (
                    f"【手动群摘要】\n"
                    f"范围：{scope_label}\n"
                    f"时间：最近 {hours} 小时\n\n"
                    f"{summary_text}"
                )
                await self.send_private(self.summary_target_qq, push_text)
        except Exception:
            self.logger.exception("手动摘要处理失败")
            await reply("摘要生成失败，请稍后再试。")

    async def run_scheduled_summary(self) -> None:
        try:
            summary_text = await self._build_summary(
                hours=self.default_lookback_hours,
                group_ids=None,
                scope_label="全部监听群",
                topic=None,
            )
            message = (
                f"【定时群摘要】\n"
                f"范围：全部监听群\n"
                f"时间：最近 {self.default_lookback_hours} 小时\n\n"
                f"{summary_text}"
            )
            await self.send_private(self.summary_target_qq, message)
        except Exception:
            self.logger.exception("定时摘要执行失败")

    async def _build_summary(
        self,
        hours: int,
        group_ids: list[int] | None,
        scope_label: str,
        topic: str | None,
    ) -> str:
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        records = await self.repo.fetch_messages_since(since=since, group_ids=group_ids)

        filtered_records = [record for record in records if not self._is_noise(record["content"], record["msg_type"])]
        if topic:
            topic_records = self._filter_records_by_topic(filtered_records, topic)
            if topic_records:
                filtered_records = topic_records

        if not filtered_records:
            if topic:
                return f"最近 {hours} 小时内没有找到与“{topic}”相关的可摘要消息。"
            return f"最近 {hours} 小时内没有可摘要的重要消息。"

        ai_input = self._format_records(filtered_records)
        domain_names = self._detect_domains(filtered_records, topic)
        prompt = self._build_prompt(
            scope_label=scope_label,
            hours=hours,
            ai_input=ai_input,
            topic=topic,
            domain_names=domain_names,
        )
        return await self.ai_client.chat(prompt)

    def _build_prompt(
        self,
        scope_label: str,
        hours: int,
        ai_input: str,
        topic: str | None,
        domain_names: list[str],
    ) -> str:
        title = f"请总结以下 {scope_label} 在最近 {hours} 小时内的聊天记录。"
        if topic:
            title = f"请重点总结以下 {scope_label} 在最近 {hours} 小时内与“{topic}”相关的聊天记录。"

        prompt_parts = [
            title,
            "要求：",
            "1. 按话题分组，不要把无关闲聊塞进重点信息。",
            "2. 优先提取通知、文件、链接、时间节点、待办事项、结论和经验建议。",
            "3. 过滤无意义的表情包、水消息、“哈哈”、“666”等闲聊。",
            "4. 如果多个人重复同一件事，请去重后合并表达。",
            "5. 若信息不完整，请明确指出“现有记录不足以确认”。",
            "6. 输出尽量结构化、简洁、可执行。",
            "",
            self.GENERIC_OUTPUT,
        ]

        for domain_name in domain_names:
            domain_rule = self.DOMAIN_RULES.get(domain_name)
            if not domain_rule:
                continue
            prompt_parts.extend(["", domain_rule["focus_prompt"], "", domain_rule["sections_prompt"]])

        prompt_parts.extend(["", f"聊天记录：\n{ai_input}"])
        return "\n".join(prompt_parts)

    @staticmethod
    def _parse_hours(command_text: str) -> int:
        match = re.search(r"最近\s*(\d+)\s*小时", command_text)
        if not match:
            return 6
        return max(1, int(match.group(1)))

    def _parse_topic(self, command_text: str) -> str | None:
        body = re.sub(r"^总结\s*", "", command_text.strip(), count=1).strip()
        body = re.sub(r"最近\s*\d+\s*小时", "", body).strip()
        body = re.sub(r"\s+", " ", body).strip()
        return body or None

    @staticmethod
    def _format_records(records: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for record in records:
            lines.append(
                f"[{record['timestamp']}] [{record['group_name']}] [{record['msg_type']}] "
                f"{record['nickname']}: {record['content']}"
            )
        return "\n".join(lines)

    def _filter_records_by_topic(self, records: list[dict[str, Any]], topic: str) -> list[dict[str, Any]]:
        keywords = self._split_topic_keywords(topic)
        if not keywords:
            return records

        filtered: list[dict[str, Any]] = []
        for record in records:
            haystack = f"{record['group_name']} {record['content']}".lower()
            if any(keyword.lower() in haystack for keyword in keywords):
                filtered.append(record)
        return filtered

    def _detect_domains(self, records: list[dict[str, Any]], topic: str | None) -> list[str]:
        text = " ".join(
            f"{record['group_name']} {record['content']}"
            for record in records[:200]
        )
        if topic:
            text = f"{topic} {text}"

        detected: list[str] = []
        lowered = text.lower()
        for domain_name, domain_rule in self.DOMAIN_RULES.items():
            score = sum(1 for keyword in domain_rule["keywords"] if keyword.lower() in lowered)
            if score >= 2 or (topic and domain_name in topic):
                detected.append(domain_name)
        return detected

    @staticmethod
    def _split_topic_keywords(topic: str) -> list[str]:
        normalized = re.sub(r"[，。！？、:：()（）]", " ", topic)
        candidates = [part.strip() for part in normalized.split() if part.strip()]
        return [candidate for candidate in candidates if len(candidate) >= 2]

    @staticmethod
    def _is_noise(content: str, msg_type: str) -> bool:
        if msg_type in {"file", "url", "notice", "at"}:
            return False

        compact = content.strip().lower()
        if not compact:
            return True

        noise_samples = {
            "哈",
            "哈哈",
            "哈哈哈",
            "666",
            "收到",
            "ok",
            "okk",
            "好的",
            "1",
            "111",
            "[表情]",
        }
        return compact in noise_samples
