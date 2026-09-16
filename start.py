"""监听面板总入口：同时启动 Web 面板 + Telethon 实时监听。

用法:
    python start.py                # 前台运行（Ctrl+C 退出）
    nohup python start.py > logs/listener_panel.log 2>&1 &   # 后台运行
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PANEL_DIR = BASE_DIR / "listener_panel"
load_dotenv(BASE_DIR / ".env")

# 日志：与主 bot 日志分开
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "listener_panel.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("listener_panel.start")

# 允许 secrets 出现一次在内存里，但绝不打日志
CFG: dict = {}


def load_cfg() -> dict:
    global CFG
    CFG = yaml.safe_load((PANEL_DIR / "config.yaml").read_text(encoding="utf-8")) or {}
    return CFG


def run_web() -> None:
    """在线程里跑 Flask（同步视图，无事件循环冲突）。"""
    from listener_panel.app import app, load_config
    web = load_config().get("web", {})
    host, port = web.get("host", "127.0.0.1"), web.get("port", 8790)
    logger.info("面板启动: http://%s:%s", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def run_monitor() -> None:
    """在独立事件循环里跑 Telethon 监听器。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from listener_panel.db import MonitorDB
        from listener_panel.monitor import ListenerMonitor
        from listener_panel import MONITOR_LOCK
        db_cfg = CFG.get("database", {})
        db_path = str((BASE_DIR / db_cfg.get("path", "./data/monitor.db")).resolve())
        db = MonitorDB(db_path)
        monitor = ListenerMonitor(db, CFG)

        async def _amain() -> None:
            while True:
                try:
                    await monitor.start()
                except RuntimeError as e:
                    logger.error("监听启动失败: %s（请先登录 .tg_user session）", e)
                    await asyncio.sleep(30)
                    continue
                except Exception as e:
                    logger.error("监听器异常，30s 后重连: %s", e)
                    await asyncio.sleep(30)

        try:
            loop.run_until_complete(_amain())
        finally:
            db.close()
    finally:
        loop.close()


def main() -> None:
    load_cfg()
    logger.info("=== 监听面板启动 ===")
    logger.info("目标群: %s", [c.get("name") for c in CFG.get("listen", {}).get("target_chats", [])])

    # 监听器启动线程后置 running 标记（health 接口读它）
    import listener_panel
    listener_panel.MONITOR_RUNNING = True

    t_monitor = threading.Thread(target=run_monitor, name="tg-monitor", daemon=True)
    t_web = threading.Thread(target=run_web, name="web-panel", daemon=True)
    t_monitor.start()
    t_web.start()

    stop = threading.Event()

    def _on_sig(*_):
        logger.info("收到停止信号，正在退出…")
        stop.set()

    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    try:
        while not stop.is_set():
            stop.wait(1)
    except KeyboardInterrupt:
        pass
    logger.info("已退出。")


if __name__ == "__main__":
    sys.exit(main())