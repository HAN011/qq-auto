from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import websockets
from websockets import WebSocketClientProtocol


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
ConnectionCallback = Callable[[dict[str, Any]], Awaitable[None]]


class NapCatClient:
    """负责与 NapCat OneBot v11 WebSocket 建立长连接。"""

    def __init__(
        self,
        ws_url: str,
        access_token: str = "",
        heartbeat_interval: int = 20,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.heartbeat_interval = heartbeat_interval
        self.logger = logger or logging.getLogger(__name__)

        self._ws: WebSocketClientProtocol | None = None
        self._running = False
        self._event_callback: EventCallback | None = None
        self._connection_callback: ConnectionCallback | None = None
        self._connected_event = asyncio.Event()
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._group_name_cache: dict[int, str] = {}
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._lifecycle_tasks: set[asyncio.Task[None]] = set()
        self._has_connected_once = False
        self._last_disconnect_at: datetime | None = None

    def set_event_callback(self, callback: EventCallback) -> None:
        self._event_callback = callback

    def set_connection_callback(self, callback: ConnectionCallback) -> None:
        self._connection_callback = callback

    async def start(self) -> None:
        self._running = True
        retry_delay = 1

        while self._running:
            was_connected = False
            try:
                headers = {}
                if self.access_token:
                    headers["Authorization"] = f"Bearer {self.access_token}"

                self.logger.info("正在连接 NapCat WebSocket: %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url,
                    extra_headers=headers,
                    ping_interval=None,
                    open_timeout=20,
                    close_timeout=10,
                    max_size=8 * 1024 * 1024,
                ) as websocket:
                    self._ws = websocket
                    self._connected_event.set()
                    retry_delay = 1
                    was_connected = True
                    self.logger.info("NapCat WebSocket 已连接")

                    connected_at = datetime.now()
                    self._notify_connection_event(
                        {
                            "type": "connected",
                            "connected_at": connected_at,
                            "is_reconnect": self._has_connected_once,
                            "disconnected_at": self._last_disconnect_at,
                        }
                    )
                    self._has_connected_once = True

                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="napcat-heartbeat")
                    await self._receive_loop(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("NapCat 连接异常，将在 %s 秒后重试: %s", retry_delay, exc)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            finally:
                if was_connected and self._running:
                    self._last_disconnect_at = datetime.now()
                    self._notify_connection_event(
                        {
                            "type": "disconnected",
                            "disconnected_at": self._last_disconnect_at,
                        }
                    )
                self._connected_event.clear()
                await self._cleanup_connection()

    async def stop(self) -> None:
        self._running = False
        self._connected_event.clear()

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None

        if self._ws is not None:
            await self._ws.close()
            self._ws = None

        self._fail_pending(RuntimeError("NapCat 客户端已停止"))

    async def send_private_msg(self, user_id: int, message: str) -> None:
        for chunk in self._split_outbound_message(message):
            await self.call_api("send_private_msg", {"user_id": int(user_id), "message": chunk})

    async def send_group_msg(self, group_id: int, message: str) -> None:
        for chunk in self._split_outbound_message(message):
            await self.call_api("send_group_msg", {"group_id": int(group_id), "message": chunk})

    async def get_group_list(self, no_cache: bool = False) -> list[dict[str, Any]]:
        response = await self.call_api("get_group_list", {"no_cache": no_cache}, timeout=30)
        data = response.get("data") or []
        for item in data:
            group_id = item.get("group_id")
            group_name = item.get("group_name")
            if group_id is not None and group_name:
                self._group_name_cache[int(group_id)] = str(group_name)
        return data

    async def get_group_msg_history(
        self,
        group_id: int,
        count: int,
        message_seq: int | str | None = None,
    ) -> list[dict[str, Any]]:
        response = await self.call_api(
            "get_group_msg_history",
            {
                "group_id": str(group_id),
                "count": int(count),
                "message_seq": str(message_seq or 0),
            },
            timeout=90,
        )
        data = response.get("data") or {}
        return data.get("messages") or []

    async def get_group_name(self, group_id: int) -> str:
        if group_id in self._group_name_cache:
            return self._group_name_cache[group_id]

        try:
            response = await self.call_api("get_group_info", {"group_id": int(group_id), "no_cache": False})
            data = response.get("data") or {}
            group_name = str(data.get("group_name") or group_id)
            self._group_name_cache[group_id] = group_name
            return group_name
        except Exception:
            self.logger.exception("获取群名失败，回退为群号: %s", group_id)
            return str(group_id)

    async def wait_until_connected(self, timeout: float | None = None) -> None:
        if timeout is None:
            await self._connected_event.wait()
            return
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: int = 15,
    ) -> dict[str, Any]:
        await self._connected_event.wait()
        if self._ws is None:
            raise RuntimeError("NapCat WebSocket 尚未连接")

        echo = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_requests[echo] = future

        payload = {"action": action, "params": params or {}, "echo": echo}
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_requests.pop(echo, None)

        if response.get("status") != "ok":
            raise RuntimeError(
                f"OneBot API 调用失败: action={action}, retcode={response.get('retcode')}, wording={response.get('wording')}"
            )
        return response

    async def _receive_loop(self, websocket: WebSocketClientProtocol) -> None:
        async for raw_message in websocket:
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                self.logger.warning("收到无法解析的消息: %s", raw_message)
                continue

            echo = payload.get("echo")
            if echo and echo in self._pending_requests:
                future = self._pending_requests[echo]
                if not future.done():
                    future.set_result(payload)
                continue

            if self._event_callback is not None:
                task = asyncio.create_task(self._run_event_callback(payload), name="napcat-event-callback")
                self._event_tasks.add(task)
                task.add_done_callback(self._event_tasks.discard)

    async def _heartbeat_loop(self) -> None:
        while self._running and self._ws is not None:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                if self._ws is None:
                    return
                pong_waiter = await self._ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=10)
                self.logger.debug("NapCat 心跳正常")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("NapCat 心跳异常，准备重连: %s", exc)
                if self._ws is not None:
                    await self._ws.close()
                return

    async def _cleanup_connection(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None

        if self._lifecycle_tasks:
            tasks = list(self._lifecycle_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._lifecycle_tasks.clear()

        if self._event_tasks:
            tasks = list(self._event_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._event_tasks.clear()

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                self.logger.debug("关闭 WebSocket 时忽略异常", exc_info=True)
            self._ws = None

        self._fail_pending(ConnectionError("NapCat 连接已断开"))

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(error)
        self._pending_requests.clear()

    async def _run_event_callback(self, payload: dict[str, Any]) -> None:
        try:
            await self._event_callback(payload)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("事件回调处理失败")

    async def _run_connection_callback(self, payload: dict[str, Any]) -> None:
        try:
            await self._connection_callback(payload)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("连接状态回调处理失败")

    def _notify_connection_event(self, payload: dict[str, Any]) -> None:
        if self._connection_callback is None:
            return

        task = asyncio.create_task(self._run_connection_callback(payload), name="napcat-connection-callback")
        self._lifecycle_tasks.add(task)
        task.add_done_callback(self._lifecycle_tasks.discard)

    @staticmethod
    def _split_outbound_message(message: str, max_length: int = 1400) -> list[str]:
        text = message.strip()
        if not text:
            return ["(空消息)"]
        if len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.splitlines():
            if not line:
                line = " "
            candidate = line if not current else f"{current}\n{line}"
            if len(candidate) <= max_length:
                current = candidate
                continue

            if current:
                chunks.append(current)
                current = ""

            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            current = line

        if current:
            chunks.append(current)
        return chunks
