from __future__ import annotations

import asyncio
import logging

from ai_client import AIClient
from config import AppConfig, load_config, setup_logging
from db import MessageRepository
from handlers.message_handler import MessageHandler
from history_sync import HistorySyncService
from napcat_client import NapCatClient
from scheduler import BotScheduler


def _print_startup_banner(config: AppConfig) -> None:
    print("=" * 72)
    print("QQ群消息智能管家启动成功")
    print("- 当前配置摘要（敏感信息已隐藏）")
    for key, value in config.masked_summary.items():
        print(f"  {key}: {value}")
    print("=" * 72)


async def async_main() -> None:
    config = load_config()
    logger = setup_logging(config.log_level, config.logs_dir)
    _print_startup_banner(config)

    repo = MessageRepository(config.database_path)
    await repo.initialize()

    ai_client = AIClient(
        api_key=config.deepseek_api_key,
        base_url=config.deepseek_base_url,
        model=config.deepseek_model,
        logger=logger,
    )
    napcat_client = NapCatClient(
        ws_url=config.napcat_ws_url,
        access_token=config.napcat_access_token,
        logger=logger,
    )
    message_handler = MessageHandler(
        config=config,
        repo=repo,
        client=napcat_client,
        ai_client=ai_client,
        logger=logger,
    )
    history_sync_service = HistorySyncService(
        config=config,
        client=napcat_client,
        message_handler=message_handler,
        logger=logger,
    )

    scheduler = BotScheduler(logger=logger)
    scheduler.add_cron_job(
        job_func=message_handler.summary_handler.run_scheduled_summary,
        cron_expression=config.summary_cron,
        job_id="scheduled-summary",
        job_name="定时群摘要",
    )
    scheduler.start()

    napcat_client.set_event_callback(message_handler.handle_event)
    napcat_client.set_connection_callback(history_sync_service.handle_connection_event)

    client_task = asyncio.create_task(napcat_client.start(), name="napcat-client")
    try:
        await napcat_client.wait_until_connected(timeout=60)
        await history_sync_service.sync_recent_history()
        await client_task
    finally:
        logger.info("正在清理资源...")
        scheduler.shutdown()
        await napcat_client.stop()
        await asyncio.gather(client_task, return_exceptions=True)
        await ai_client.close()
        await repo.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logging.getLogger("qq_bot").info("收到退出信号，程序已停止")
    except Exception as exc:
        logging.getLogger("qq_bot").exception("程序启动失败: %s", exc)
        raise


if __name__ == "__main__":
    main()
