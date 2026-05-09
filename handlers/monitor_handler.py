from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable


class MonitorHandler:
    """实时监控重要消息并私聊提醒。"""

    IMPORTANT_KEYWORDS = ("通知", "公告", "截止", "报名", "提交")

    def __init__(
        self,
        summary_target_qq: int,
        send_private: Callable[[int, str], Awaitable[None]],
        logger: logging.Logger | None = None,
    ) -> None:
        self.summary_target_qq = summary_target_qq
        self.send_private = send_private
        self.logger = logger or logging.getLogger(__name__)

    async def handle(self, message: dict, flags: dict[str, bool]) -> None:
        try:
            reasons: list[str] = []
            content = message["content"]

            if flags.get("has_url"):
                reasons.append("链接")
            if any(keyword in content for keyword in self.IMPORTANT_KEYWORDS):
                reasons.append("关键词")
            if flags.get("has_at_all"):
                reasons.append("@全体成员")
            if flags.get("has_file"):
                reasons.append("文件")
            if not reasons:
                return

            alert_text = (
                f"⚠️ [{message['group_name']}] 发现重要消息\n"
                f"类型：{' / '.join(reasons)}\n"
                f"发送人：{message['nickname']}\n"
                f"内容：{self._shorten(content, 180)}\n"
                f"时间：{message['timestamp']}"
            )
            await self.send_private(self.summary_target_qq, alert_text)
        except Exception:
            self.logger.exception("监控提醒处理失败")

    @staticmethod
    def _shorten(text: str, max_length: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_length:
            return compact
        return f"{compact[:max_length]}..."
