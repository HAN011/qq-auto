from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return "(未设置)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}***{value[-keep:]}"


def _parse_int(value: str | None, field_name: str, required: bool = True) -> int | None:
    if value is None or not value.strip():
        if required:
            raise ValueError(f"配置项 {field_name} 未设置")
        return None

    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"配置项 {field_name} 必须是整数") from exc


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "是", "开启"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否", "关闭"}:
        return False
    raise ValueError(f"布尔配置项值非法: {value}")


def _parse_watch_groups(value: str | None) -> tuple[list[int], bool]:
    if not value:
        raise ValueError("配置项 WATCH_GROUPS 未设置")

    normalized = value.strip().lower()
    if normalized in {"all", "*", "全部"}:
        return [], True

    group_ids: list[int] = []
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            group_ids.append(int(text))
        except ValueError as exc:
            raise ValueError(f"WATCH_GROUPS 中存在非法群号: {text}") from exc

    if not group_ids:
        raise ValueError("WATCH_GROUPS 至少需要配置一个群号")
    return group_ids, False


@dataclass(slots=True)
class AppConfig:
    napcat_ws_url: str
    napcat_access_token: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    bot_qq: int | None
    watch_groups: list[int]
    watch_all_groups: bool
    history_sync_enabled: bool
    history_sync_count: int
    summary_target_qq: int
    summary_cron: str
    summary_lookback_hours: int
    log_level: str
    database_path: Path
    logs_dir: Path

    @property
    def masked_summary(self) -> dict[str, str]:
        watch_groups_display = "all" if self.watch_all_groups else ", ".join(str(group_id) for group_id in self.watch_groups)
        return {
            "NAPCAT_WS_URL": self.napcat_ws_url,
            "NAPCAT_ACCESS_TOKEN": _mask_secret(self.napcat_access_token),
            "DEEPSEEK_API_KEY": _mask_secret(self.deepseek_api_key),
            "DEEPSEEK_BASE_URL": self.deepseek_base_url,
            "DEEPSEEK_MODEL": self.deepseek_model,
            "BOT_QQ": str(self.bot_qq or "(未设置)"),
            "WATCH_GROUPS": watch_groups_display,
            "HISTORY_SYNC_ENABLED": str(self.history_sync_enabled),
            "HISTORY_SYNC_COUNT": str(self.history_sync_count),
            "SUMMARY_TARGET_QQ": str(self.summary_target_qq),
            "SUMMARY_CRON": self.summary_cron,
            "SUMMARY_LOOKBACK_HOURS": str(self.summary_lookback_hours),
            "LOG_LEVEL": self.log_level,
            "DATABASE_PATH": str(self.database_path),
            "LOG_FILE": str(self.logs_dir / "bot.log"),
        }


def load_config() -> AppConfig:
    load_dotenv(ENV_PATH, override=False)

    napcat_ws_url = os.getenv("NAPCAT_WS_URL", "").strip()
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    napcat_access_token = os.getenv("NAPCAT_ACCESS_TOKEN", "").strip()
    summary_cron = os.getenv("SUMMARY_CRON", "0 22 * * *").strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    if not napcat_ws_url:
        raise ValueError("配置项 NAPCAT_WS_URL 未设置")
    if not deepseek_api_key:
        raise ValueError("配置项 DEEPSEEK_API_KEY 未设置")

    bot_qq = _parse_int(os.getenv("BOT_QQ"), "BOT_QQ", required=False)
    summary_target_qq = _parse_int(os.getenv("SUMMARY_TARGET_QQ"), "SUMMARY_TARGET_QQ", required=True)
    watch_groups, watch_all_groups = _parse_watch_groups(os.getenv("WATCH_GROUPS"))
    history_sync_enabled = _parse_bool(os.getenv("HISTORY_SYNC_ENABLED"), default=True)

    summary_lookback_hours_raw = os.getenv("SUMMARY_LOOKBACK_HOURS", "24").strip()
    try:
        summary_lookback_hours = max(1, int(summary_lookback_hours_raw))
    except ValueError as exc:
        raise ValueError("配置项 SUMMARY_LOOKBACK_HOURS 必须是正整数") from exc

    history_sync_count_raw = os.getenv("HISTORY_SYNC_COUNT", "1000").strip()
    try:
        history_sync_count = max(0, int(history_sync_count_raw))
    except ValueError as exc:
        raise ValueError("配置项 HISTORY_SYNC_COUNT 必须是非负整数") from exc

    database_path = BASE_DIR / "data" / "bot.db"
    logs_dir = BASE_DIR / "logs"

    return AppConfig(
        napcat_ws_url=napcat_ws_url,
        napcat_access_token=napcat_access_token,
        deepseek_api_key=deepseek_api_key,
        deepseek_base_url=deepseek_base_url,
        deepseek_model=deepseek_model,
        bot_qq=bot_qq,
        watch_groups=watch_groups,
        watch_all_groups=watch_all_groups,
        history_sync_enabled=history_sync_enabled,
        history_sync_count=history_sync_count,
        summary_target_qq=summary_target_qq,
        summary_cron=summary_cron,
        summary_lookback_hours=summary_lookback_hours,
        log_level=log_level,
        database_path=database_path,
        logs_dir=logs_dir,
    )


def setup_logging(log_level: str, logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "bot.log"

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logger = logging.getLogger("qq_bot")
    logger.info("日志系统初始化完成，级别: %s", log_level)
    return logger
