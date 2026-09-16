#!/usr/bin/env python3
"""自动重试读取 BotFather bot token：解析限流剩余时间，等待解除后读取。

用法:
  python scripts/auto_read_tokens.py [--max-attempts N]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.tl.types import Message

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SESSION = str(BASE_DIR / ".tg_user")
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TOKEN_RE = re.compile(r"(\d{6,10}:[A-Za-z0-9_-]{30,45})")
RATELIMIT_RE = re.compile(r"try again in (\d+) seconds", re.I)
LOG_FILE = BASE_DIR / "logs" / "auto_read_tokens.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("auto_read")


def _wait(retry_secs: int) -> None:
    # 分批 sleep，允许 Ctrl+C 中断
    logger.info("等待 %.1f 分钟（限流解除）...", retry_secs / 60)
    while retry_secs > 0:
        chunk = min(60, retry_secs)
        time.sleep(chunk)
        retry_secs -= chunk


async def wait_new_message(client, last_id: int, timeout: float = 12.0) -> Message | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages("botfather", limit=1)
        if msgs and msgs[0].id > last_id:
            return msgs[0]
        await asyncio.sleep(1.5)
    return None


async def click_by_text(client, msg: Message, text: str, last_id: int):
    target = None
    for row in msg.buttons or []:
        for btn in row:
            if text in (btn.text or ""):
                target = btn
                break
        if target:
            break
    if target is None:
        return None
    try:
        await target.click()
    except errors.DataInvalidError:
        logger.warning("按钮点击被拒（DataInvalid），可能限流")
        return None
    return await wait_new_message(client, last_id)


async def read_once(client) -> tuple[list, int | None]:
    """读取一次；返回 (results, 限流剩余秒数或 None)。"""
    await client.send_message("botfather", "/mybots")
    list_msg = await wait_new_message(client, 0, timeout=10)
    if list_msg is None or not list_msg.buttons:
        txt = (list_msg.message if list_msg else "") or ""
        m = RATELIMIT_RE.search(txt)
        if m:
            return [], int(m.group(1))
        logger.error("无法获取 /mybots 列表: %s", txt[:120])
        return [], None

    bot_btns = [
        btn
        for row in list_msg.buttons or []
        for btn in row
        if (btn.data or b"").startswith(b"bots/") and b"/tokn" not in (btn.data or b"")
    ]
    logger.info("列表共 %d 个 bot", len(bot_btns))

    results = []
    last_id = list_msg.id
    cur = list_msg
    for btn in bot_btns:
        uname = (btn.text or "").lstrip("@")
        logger.info("处理 @%s ...", uname)
        # 进入管理页
        nxt = await click_by_text(client, cur, btn.text, last_id)
        if nxt is None:
            msgs = await client.get_messages("botfather", limit=1)
            txt = (msgs[0].message if msgs else "") or ""
            m = RATELIMIT_RE.search(txt)
            if m:
                return results, int(m.group(1))
            logger.warning("进入管理页失败，跳过 @%s", uname)
            continue
        cur, last_id = nxt, nxt.id
        # 点 API Token
        tmsg = await click_by_text(client, cur, "API Token", last_id)
        if tmsg is None:
            logger.warning("点 API Token 失败，跳过 @%s", uname)
            continue
        cur, last_id = tmsg, tmsg.id
        mt = TOKEN_RE.search(tmsg.message or "")
        if mt:
            results.append({"username": uname, "token": mt.group(1)})
            logger.info("✅ @%s token 已读取", uname)
        else:
            logger.warning("@%s 回复无 token: %s", uname, (tmsg.message or "")[:80])
        # 返回列表
        back = await click_by_text(client, cur, "Back to Bot List", last_id)
        if back is not None:
            cur, last_id = back, back.id
        await asyncio.sleep(30)
    return results, None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-attempts", type=int, default=50)
    args = parser.parse_args()

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        logger.error("session 未授权")
        return
    me = await client.get_me()
    logger.info("登录: %s id=%s @ %s", me.first_name, me.id, datetime.now(timezone.utc).isoformat())

    for attempt in range(1, args.max_attempts + 1):
        logger.info("===== 第 %d 次尝试 =====", attempt)
        try:
            results, retry = await read_once(client)
        except errors.FloodWaitError as e:
            retry = int(e.seconds)
            results = []
            logger.warning("FloodWait %d 秒", retry)
        except Exception as e:
            logger.exception("读取异常")
            retry = 1800
            results = []

        if results:
            out = BASE_DIR / "config" / "tg_bots.yaml"
            existing = {}
            if out.exists():
                existing = yaml.safe_load(out.read_text()) or {}
            existing["bots"] = results
            out.write_text(yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8")
            logger.info("✅ 已保存 %d 个 bot token 到 %s", len(results), out)
            await client.disconnect()
            return

        if retry is None:
            logger.info("未发现限流信息，30 分钟后再试（避免高频试探刷新限流）")
            retry = 1800
        # 每两次尝试之间至少等 30 分钟，避免再次触发 BotFather 限流
        await client.disconnect()
        _wait(max(retry, 1800))
        await client.connect()

    logger.error("达到最大尝试次数，仍未成功")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())