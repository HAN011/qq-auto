from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ai_client import AIClient
from config import AppConfig
from db import MessageRepository
from handlers.cross_group_handler import CrossGroupHandler
from handlers.monitor_handler import MonitorHandler
from handlers.search_handler import SearchHandler
from handlers.smart_query_handler import SmartQueryHandler
from handlers.summary_handler import SummaryHandler
from napcat_client import NapCatClient


class MessageHandler:
    """统一处理 NapCat 上报事件。"""

    URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)

    def __init__(
        self,
        config: AppConfig,
        repo: MessageRepository,
        client: NapCatClient,
        ai_client: AIClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.repo = repo
        self.client = client
        self.logger = logger or logging.getLogger(__name__)

        self.search_handler = SearchHandler(repo=repo, logger=self.logger)
        self.smart_query_handler = SmartQueryHandler(repo=repo, ai_client=ai_client, logger=self.logger)
        self.summary_handler = SummaryHandler(
            repo=repo,
            ai_client=ai_client,
            summary_target_qq=config.summary_target_qq,
            default_lookback_hours=config.summary_lookback_hours,
            send_private=self.client.send_private_msg,
            logger=self.logger,
        )
        self.monitor_handler = MonitorHandler(
            summary_target_qq=config.summary_target_qq,
            send_private=self.client.send_private_msg,
            logger=self.logger,
        )
        self.cross_group_handler = CrossGroupHandler(repo=repo, ai_client=ai_client, logger=self.logger)

    async def handle_event(self, payload: dict[str, Any]) -> None:
        try:
            post_type = payload.get("post_type")
            if post_type == "message":
                await self._handle_message(payload)
            elif post_type == "notice":
                await self._handle_notice(payload)
        except Exception:
            self.logger.exception("处理事件失败: %s", payload)

    async def _handle_message(self, payload: dict[str, Any]) -> None:
        message_type = payload.get("message_type")
        user_id = int(payload.get("user_id", 0))
        if self.config.bot_qq and user_id == self.config.bot_qq:
            return

        parsed = self._parse_message(payload.get("message", []))
        plain_text = parsed["plain_text"].strip()
        record = await self._build_message_record(payload, parsed)
        group_id = record["group_id"] or None
        group_name = record["group_name"]

        if message_type == "group" and self._is_watched_group(group_id):
            await self.repo.save_message(record)
            await self.monitor_handler.handle(record, parsed["flags"])

        if not plain_text:
            return

        if plain_text.startswith("搜索 "):
            await self._dispatch_search(message_type, group_id, user_id, plain_text)
            return

        if self.smart_query_handler.can_handle(plain_text):
            await self._dispatch_smart_query(message_type, group_id, user_id, plain_text)
            return

        if plain_text.startswith("总结"):
            await self._dispatch_summary(message_type, group_id, group_name, user_id, plain_text)
            return

        if message_type == "private" and plain_text.startswith("汇总 "):
            await self.cross_group_handler.handle(
                plain_text,
                reply=lambda text: self.client.send_private_msg(user_id, text),
            )

    async def _handle_notice(self, payload: dict[str, Any]) -> None:
        group_id = int(payload.get("group_id", 0) or 0)
        if not self._is_watched_group(group_id):
            return

        group_name = await self.client.get_group_name(group_id)
        user_id = int(payload.get("user_id", 0) or 0)
        operator_id = int(payload.get("operator_id", 0) or 0)
        content = self._build_notice_content(payload)

        record = {
            "group_id": group_id,
            "group_name": group_name,
            "user_id": user_id or operator_id,
            "nickname": f"系统通知({operator_id or user_id or '未知'})",
            "content": content,
            "msg_type": "notice",
            "raw_json": payload,
            "timestamp": self._format_timestamp(payload.get("time")),
        }
        await self.repo.save_message(record)

    async def _dispatch_search(
        self,
        message_type: str,
        group_id: int | None,
        user_id: int,
        command_text: str,
    ) -> None:
        if message_type == "group" and group_id is not None:
            await self.search_handler.handle(
                command_text,
                reply=lambda text: self.client.send_group_msg(group_id, text),
            )
            return

        await self.search_handler.handle(
            command_text,
            reply=lambda text: self.client.send_private_msg(user_id, text),
        )

    async def _dispatch_summary(
        self,
        message_type: str,
        group_id: int | None,
        group_name: str,
        user_id: int,
        command_text: str,
    ) -> None:
        if message_type == "group":
            if not self._is_watched_group(group_id):
                await self.client.send_group_msg(group_id or 0, "当前群未配置监听，无法生成本群摘要。")
                return

            await self.summary_handler.handle_manual(
                command_text,
                source={
                    "message_type": "group",
                    "group_id": group_id,
                    "group_name": group_name,
                    "user_id": user_id,
                },
                reply=lambda text: self.client.send_group_msg(group_id or 0, text),
            )
            return

        await self.summary_handler.handle_manual(
            command_text,
            source={"message_type": "private", "group_id": None, "group_name": "全部监听群", "user_id": user_id},
            reply=lambda text: self.client.send_private_msg(user_id, text),
        )

    async def _dispatch_smart_query(
        self,
        message_type: str,
        group_id: int | None,
        user_id: int,
        command_text: str,
    ) -> None:
        if message_type == "group" and group_id is not None:
            await self.smart_query_handler.handle(
                command_text=command_text,
                message_type=message_type,
                current_group_id=group_id,
                reply=lambda text: self.client.send_group_msg(group_id, text),
            )
            return

        await self.smart_query_handler.handle(
            command_text=command_text,
            message_type=message_type,
            current_group_id=group_id,
            reply=lambda text: self.client.send_private_msg(user_id, text),
        )

    async def ingest_group_history_message(self, payload: dict[str, Any]) -> bool:
        if payload.get("message_type") != "group":
            return False

        group_id = int(payload.get("group_id", 0) or 0)
        if not self._is_watched_group(group_id):
            return False

        parsed = self._parse_message(payload.get("message", []))
        record = await self._build_message_record(payload, parsed)
        inserted = await self.repo.save_message(record)
        return inserted > 0

    def _is_watched_group(self, group_id: int | None) -> bool:
        if not group_id:
            return False
        return self.config.watch_all_groups or group_id in self.config.watch_groups

    async def _build_message_record(self, payload: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
        message_type = payload.get("message_type")
        user_id = int(payload.get("user_id", 0))
        group_id = int(payload.get("group_id", 0)) if message_type == "group" else 0
        group_name = payload.get("group_name") or ("私聊" if not group_id else "")

        if group_id and not group_name:
            group_name = await self.client.get_group_name(group_id)

        sender = payload.get("sender") or {}
        nickname = sender.get("card") or sender.get("nickname") or str(user_id)
        timestamp = self._format_timestamp(payload.get("time"))

        return {
            "group_id": group_id,
            "group_name": str(group_name or group_id or "私聊"),
            "message_id": str(payload.get("message_id")) if payload.get("message_id") is not None else None,
            "user_id": user_id,
            "nickname": nickname,
            "content": parsed["content"] or "[空消息]",
            "msg_type": parsed["msg_type"],
            "raw_json": payload,
            "timestamp": timestamp,
        }

    def _parse_message(self, message: Any) -> dict[str, Any]:
        if isinstance(message, str):
            has_url = bool(self.URL_PATTERN.search(message))
            return {
                "plain_text": message,
                "content": message,
                "msg_type": "url" if has_url else "text",
                "flags": {
                    "has_url": has_url,
                    "has_at_all": False,
                    "has_image": False,
                    "has_file": False,
                },
            }

        plain_parts: list[str] = []
        content_parts: list[str] = []
        has_url = False
        has_at_all = False
        has_image = False
        has_file = False
        has_at = False

        for segment in message or []:
            seg_type = segment.get("type")
            data = segment.get("data") or {}

            if seg_type == "text":
                text = data.get("text", "")
                plain_parts.append(text)
                content_parts.append(text)
                if self.URL_PATTERN.search(text):
                    has_url = True
            elif seg_type == "image":
                has_image = True
                file_name = data.get("file") or data.get("url") or "图片"
                content_parts.append(f"[图片:{file_name}]")
            elif seg_type == "file":
                has_file = True
                file_name = data.get("name") or data.get("file") or "文件"
                content_parts.append(f"[文件:{file_name}]")
            elif seg_type == "at":
                qq = str(data.get("qq", ""))
                if qq == "all":
                    has_at_all = True
                    has_at = True
                    plain_parts.append("@全体成员")
                    content_parts.append("[@全体成员]")
                else:
                    has_at = True
                    plain_parts.append(f"@{qq}")
                    content_parts.append(f"[@{qq}]")
            elif seg_type == "share":
                has_url = True
                title = data.get("title") or "分享"
                url = data.get("url") or ""
                content_parts.append(f"[分享]{title} {url}".strip())
            else:
                content_parts.append(f"[{seg_type}]")

        plain_text = "".join(plain_parts).strip()
        content = " ".join(part for part in content_parts if part).strip()

        if has_at:
            msg_type = "at"
        elif has_file:
            msg_type = "file"
        elif has_image:
            msg_type = "image"
        elif has_url:
            msg_type = "url"
        else:
            msg_type = "text"

        return {
            "plain_text": plain_text,
            "content": content,
            "msg_type": msg_type,
            "flags": {
                "has_url": has_url,
                "has_at_all": has_at_all,
                "has_image": has_image,
                "has_file": has_file,
            },
        }

    @staticmethod
    def _build_notice_content(payload: dict[str, Any]) -> str:
        notice_type = payload.get("notice_type", "notice")
        sub_type = payload.get("sub_type", "")
        operator_id = payload.get("operator_id")
        user_id = payload.get("user_id")

        parts = [f"[系统通知] {notice_type}"]
        if sub_type:
            parts.append(f"子类型: {sub_type}")
        if operator_id:
            parts.append(f"操作人: {operator_id}")
        if user_id:
            parts.append(f"目标用户: {user_id}")
        return " | ".join(parts)

    @staticmethod
    def _format_timestamp(event_time: Any) -> str:
        if isinstance(event_time, (int, float)):
            return datetime.fromtimestamp(event_time).strftime("%Y-%m-%d %H:%M:%S")
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
