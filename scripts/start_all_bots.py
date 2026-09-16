#!/usr/bin/env python3
"""一键启动全部监听 bot：每个 bot 一个独立子进程，共享 settings.yaml，仅 TG_BOT_TOKEN 不同。

token 来源: config/tg_bots.yaml（由 scripts/auto_read_tokens.py 自动写入）

用法:
  python scripts/start_all_bots.py                # 启动 tg_bots.yaml 里全部 bot
  python scripts/start_all_bots.py --bot tgmon_01_bot
  python scripts/start_all_bots.py --bots a,b,c   # 只启动指定几个
  python scripts/start_all_bots.py --dry-run      # 只打印计划，不真正启动
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

LOG_FILE = BASE_DIR / "logs" / "start_all_bots.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("start_all")

PYTHON = sys.executable


def load_bots() -> list[dict]:
    path = BASE_DIR / "config" / "tg_bots.yaml"
    if not path.exists():
        logger.warning("找不到 %s，请先运行 scripts/auto_read_tokens.py 读取 token", path)
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    bots = data.get("bots") or []
    return [
        {"username": (b.get("username") or "").lstrip("@"), "token": b.get("token") or ""}
        for b in bots
        if b.get("token")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 TG 监控 bot（每个 bot 独立进程）")
    parser.add_argument("--bot", help="只启动指定 bot（如 tgmon_01_bot）")
    parser.add_argument("--bots", help="启动多个 bot，逗号分隔")
    parser.add_argument("--dry-run", action="store_true", help="只打印启动计划")
    args = parser.parse_args()

    bots = load_bots()
    if not bots:
        logger.error("没有可用的 bot token（config/tg_bots.yaml 为空）")
        sys.exit(1)

    if args.bot:
        bots = [b for b in bots if b["username"] == args.bot.lstrip("@")]
    elif args.bots:
        want = {x.lstrip("@") for x in args.bots.split(",")}
        bots = [b for b in bots if b["username"] in want]

    if not bots:
        logger.error("筛选后没有匹配的 bot")
        sys.exit(1)

    logger.info("计划启动 %d 个 bot: %s", len(bots), ", ".join(b["username"] for b in bots))
    if args.dry_run:
        for b in bots:
            logger.info("  [dry-run] @%s", b["username"])
        return

    procs: dict[str, subprocess.Popen] = {}
    for b in bots:
        env = os.environ.copy()
        env["TG_BOT_TOKEN"] = b["token"]
        env["BOT_NAME"] = b["username"]  # 独立日志文件 logs/<username>.log
        proc = subprocess.Popen(
            [PYTHON, "-m", "src.main"],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs[b["username"]] = proc
        logger.info(
            "已启动 @%s (pid=%d, t=%s)",
            b["username"],
            proc.pid,
            datetime.now(timezone.utc).strftime("%H:%M:%S"),
        )

    def _stop_all(_sig=None, _frame=None) -> None:
        logger.info("收到停止信号，终止全部子进程...")
        for name, proc in procs.items():
            if proc.poll() is None:
                proc.terminate()
                logger.info("  终止 @%s (pid=%d)", name, proc.pid)
        for proc in procs.values():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        logger.info("全部已退出")

    signal.signal(signal.SIGINT, _stop_all)
    signal.signal(signal.SIGTERM, _stop_all)

    for name, proc in procs.items():
        proc.wait()
        logger.warning("@%s 进程退出, code=%s", name, proc.returncode)


if __name__ == "__main__":
    main()
