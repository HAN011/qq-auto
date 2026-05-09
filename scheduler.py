from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


class BotScheduler:
    """负责定时任务注册与启动。"""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo("Asia/Shanghai"))

    def add_cron_job(
        self,
        job_func: Callable[[], Awaitable[None]],
        cron_expression: str,
        job_id: str,
        job_name: str,
    ) -> None:
        trigger = CronTrigger.from_crontab(cron_expression, timezone=ZoneInfo("Asia/Shanghai"))
        self.scheduler.add_job(job_func, trigger=trigger, id=job_id, name=job_name, replace_existing=True)
        self.logger.info("已注册定时任务: %s -> %s", job_name, cron_expression)

    def start(self) -> None:
        self.scheduler.start()
        self.logger.info("定时任务调度器已启动")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.logger.info("定时任务调度器已关闭")
