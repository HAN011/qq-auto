from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from config import AppConfig
from handlers.message_handler import MessageHandler
from napcat_client import NapCatClient


class HistorySyncService:
    """负责启动回补和断线重连后的窗口补录。"""

    DISCONNECT_WINDOW_BUFFER_SECONDS = 10

    def __init__(
        self,
        config: AppConfig,
        client: NapCatClient,
        message_handler: MessageHandler,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.message_handler = message_handler
        self.logger = logger or logging.getLogger(__name__)

    async def handle_connection_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "connected":
            return

        if not event.get("is_reconnect"):
            return

        disconnected_at = event.get("disconnected_at")
        connected_at = event.get("connected_at")
        if not isinstance(disconnected_at, datetime) or not isinstance(connected_at, datetime):
            return

        await self.sync_disconnect_window(disconnected_at=disconnected_at, reconnected_at=connected_at)

    async def sync_recent_history(self) -> None:
        if not self._history_sync_ready():
            return

        groups = await self._resolve_target_groups()
        if not groups:
            self.logger.info("没有可回补的群，跳过启动历史回补")
            return

        self.logger.info(
            "开始回补群历史消息: 群数量=%s, 每群最多=%s",
            len(groups),
            self.config.history_sync_count,
        )

        total_inserted = 0
        for index, group in enumerate(groups, start=1):
            group_id = int(group["group_id"])
            group_name = str(group["group_name"])
            try:
                messages = await self.client.get_group_msg_history(group_id, self.config.history_sync_count)
                inserted = await self._ingest_messages(messages, group_id, group_name)
                total_inserted += inserted
                self.logger.info(
                    "历史回补进度 %s/%s: [%s] 获取=%s, 新增=%s",
                    index,
                    len(groups),
                    group_name,
                    len(messages),
                    inserted,
                )
            except Exception:
                self.logger.exception("历史回补失败: [%s](%s)", group_name, group_id)

            await asyncio.sleep(0.2)

        self.logger.info("启动历史回补完成，本次新增消息 %s 条", total_inserted)

    async def sync_disconnect_window(self, disconnected_at: datetime, reconnected_at: datetime) -> None:
        if not self._history_sync_ready():
            return

        groups = await self._resolve_target_groups()
        if not groups:
            self.logger.info("没有可补录的群，跳过断线窗口补录")
            return

        window_start_ts = disconnected_at.timestamp() - self.DISCONNECT_WINDOW_BUFFER_SECONDS
        window_end_ts = reconnected_at.timestamp()

        self.logger.info(
            "开始补录断线窗口消息: 断线开始=%s, 重连完成=%s, 群数量=%s, 每群最多=%s",
            disconnected_at.strftime("%Y-%m-%d %H:%M:%S"),
            reconnected_at.strftime("%Y-%m-%d %H:%M:%S"),
            len(groups),
            self.config.history_sync_count,
        )

        total_inserted = 0
        for index, group in enumerate(groups, start=1):
            group_id = int(group["group_id"])
            group_name = str(group["group_name"])
            try:
                messages = await self.client.get_group_msg_history(group_id, self.config.history_sync_count)
                window_messages = self._filter_messages_by_time(messages, window_start_ts, window_end_ts)
                inserted = await self._ingest_messages(window_messages, group_id, group_name)
                total_inserted += inserted

                if messages:
                    oldest_ts = min(self._extract_message_timestamp(item) for item in messages)
                    if oldest_ts > window_start_ts:
                        self.logger.warning(
                            "断线窗口补录可能未覆盖到更早消息: [%s] 最早取回时间=%s, 断线开始=%s",
                            group_name,
                            datetime.fromtimestamp(oldest_ts).strftime("%Y-%m-%d %H:%M:%S"),
                            disconnected_at.strftime("%Y-%m-%d %H:%M:%S"),
                        )

                self.logger.info(
                    "断线补录进度 %s/%s: [%s] 获取=%s, 窗口命中=%s, 新增=%s",
                    index,
                    len(groups),
                    group_name,
                    len(messages),
                    len(window_messages),
                    inserted,
                )
            except Exception:
                self.logger.exception("断线窗口补录失败: [%s](%s)", group_name, group_id)

            await asyncio.sleep(0.2)

        self.logger.info("断线窗口补录完成，本次新增消息 %s 条", total_inserted)

    async def _ingest_messages(self, messages: list[dict[str, Any]], group_id: int, group_name: str) -> int:
        inserted = 0
        for payload in messages:
            payload.setdefault("group_id", group_id)
            payload.setdefault("group_name", group_name)
            if await self.message_handler.ingest_group_history_message(payload):
                inserted += 1
        return inserted

    def _filter_messages_by_time(
        self,
        messages: list[dict[str, Any]],
        start_ts: float,
        end_ts: float,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for payload in messages:
            message_ts = self._extract_message_timestamp(payload)
            if start_ts <= message_ts <= end_ts:
                filtered.append(payload)
        return filtered

    @staticmethod
    def _extract_message_timestamp(payload: dict[str, Any]) -> float:
        raw_time = payload.get("time")
        if isinstance(raw_time, (int, float)):
            return float(raw_time)
        try:
            return float(raw_time)
        except (TypeError, ValueError):
            return 0.0

    async def _resolve_target_groups(self) -> list[dict[str, str | int]]:
        if self.config.watch_all_groups:
            groups = await self.client.get_group_list()
            return [
                {"group_id": int(item["group_id"]), "group_name": str(item.get("group_name") or item["group_id"])}
                for item in groups
            ]

        groups: list[dict[str, str | int]] = []
        for group_id in self.config.watch_groups:
            group_name = await self.client.get_group_name(group_id)
            groups.append({"group_id": int(group_id), "group_name": group_name})
        return groups

    def _history_sync_ready(self) -> bool:
        if not self.config.history_sync_enabled:
            self.logger.info("历史回补已关闭")
            return False

        if self.config.history_sync_count <= 0:
            self.logger.info("HISTORY_SYNC_COUNT=0，跳过历史回补")
            return False

        return True
