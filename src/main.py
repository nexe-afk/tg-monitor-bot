"""程序入口：初始化配置、日志、数据库、各组件并启动 bot。"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from telegram.ext import Application

from .ai_router import AIRouter
from .bot import TgMonitorBot
from .database import Database
from .feishu_push import FeishuNotifier
from .keyword_match import KeywordMatcher

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DEFAULT_ENV_FILE = BASE_DIR / ".env"

logger = logging.getLogger("tg_monitor")


# ---------- 日志 ----------


def setup_logging(level: str = "INFO", log_dir: str = "./logs", name: str = "bot") -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(numeric_level)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path / f"{name}.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # python-telegram-bot 自带日志降噪
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------- 配置加载 ----------


def resolve_env_placeholder(value: Any) -> Any:
    """递归替换字符串中的 ${ENV_VAR} 占位符。"""
    if isinstance(value, str):
        result = value
        while "${" in result:
            start = result.find("${")
            end = result.find("}", start)
            if end == -1:
                break
            var_name = result[start + 2 : end]
            env_value = os.getenv(var_name, "")
            if not env_value:
                logger.warning("环境变量 %s 未设置，已替换为空", var_name)
            result = result[:start] + env_value + result[end + 1 :]
        return result
    if isinstance(value, dict):
        return {k: resolve_env_placeholder(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_placeholder(v) for v in value]
    return value


def load_config(file_name: str) -> dict:
    path = CONFIG_DIR / file_name
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return resolve_env_placeholder(raw)


# ---------- 主流程 ----------


async def bootstrap(settings: dict) -> None:
    bot_cfg = settings.get("bot", {})
    db_cfg = settings.get("database", {}) or {}
    feishu_cfg = settings.get("feishu", {}) or {}

    token = bot_cfg.get("token", "")
    if not token:
        raise RuntimeError("缺少 bot.token，请在 .env 中配置 TG_BOT_TOKEN")

    # 组件
    db = Database(db_cfg.get("path", "./data/messages.db"))
    await db.init()

    router = AIRouter(CONFIG_DIR / "ai_agents.yaml")
    router.load_config()

    matcher = KeywordMatcher(CONFIG_DIR / "keyword_rules.yaml")
    matcher.load_config()

    feishu = FeishuNotifier(
        enabled=bool(feishu_cfg.get("enabled", False)),
        webhook_url=feishu_cfg.get("webhook_url", "") or "",
        app_id=feishu_cfg.get("app_id", "") or "",
        app_secret=feishu_cfg.get("app_secret", "") or "",
        chat_id=feishu_cfg.get("chat_id", "") or "",
        push_ai_reply=bool(feishu_cfg.get("push_ai_reply", False)),
    )

    bot = TgMonitorBot(settings, db, router, matcher, feishu)

    app = Application.builder().token(token).build()
    app.add_handler(bot.build_handler())

    # SIGHUP -> 热重载所有配置；SIGINT/SIGTERM -> 优雅退出
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _reload_all() -> None:
        router.reload_config()
        matcher.reload_config()

    def _stop() -> None:
        logger.info("收到停止信号，正在退出...")
        stop_event.set()

    def _register_signal(sig, handler) -> None:
        try:
            loop.add_signal_handler(sig, handler)
        except (NotImplementedError, RuntimeError):
            pass  # Windows 等平台不支持部分信号

    _register_signal(signal.SIGHUP, _reload_all)
    _register_signal(signal.SIGINT, _stop)
    _register_signal(signal.SIGTERM, _stop)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info(
        "Bot 已启动并开始监听: 群=%s 私聊=%s 飞书=%s",
        "全部" if not bot.monitor_groups else bot.monitor_groups,
        bot.monitor_private,
        "启用" if feishu.usable else "未启用",
    )

    try:
        await stop_event.wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await db.close()
        logger.info("Bot 已退出")


def main() -> None:
    load_dotenv(DEFAULT_ENV_FILE)

    try:
        settings = load_config("settings.yaml")
    except FileNotFoundError as exc:
        raise SystemExit(f"找不到配置文件: {exc}") from exc

    logging_cfg = settings.get("logging", {}) or {}
    setup_logging(
        level=logging_cfg.get("level", "INFO"),
        log_dir=logging_cfg.get("dir", "./logs"),
    )

    try:
        asyncio.run(bootstrap(settings))
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出")
    except Exception:
        logger.exception("程序异常退出")


if __name__ == "__main__":
    main()
