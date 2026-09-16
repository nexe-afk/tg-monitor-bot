#!/usr/bin/env python3
"""扫码登录 Telegram（设备授权二维码，无需手机号/2FA）。

原理: Telethon 底层 MTProto 的 ExportLoginToken → 已登录手机扫码
      → ImportLoginToken 轮询授权结果。token 过期自动重发二维码。

用法: python scripts/qr_login.py
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.auth import ExportLoginTokenRequest, ImportLoginTokenRequest
from telethon.tl.types.auth import LoginToken, LoginTokenMigrateTo, LoginTokenSuccess

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qr_login")

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_FILE = BASE_DIR / ".tg_session"

TOTAL_TIMEOUT = 300   # 总超时 5 分钟
TOKEN_TTL = 60        # 单个二维码有效期，过期重新生成


def render_qr(data: str) -> None:
    import qrcode

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    for row in matrix:
        print("".join("██" if c else "  " for c in row))
    print(f"\n扫码链接: {data}")


async def try_login(client: TelegramClient, token_bytes: bytes):
    """轮询 ImportLoginToken，成功返回 True，token 过期返回 False。"""
    deadline = time.time() + TOKEN_TTL
    while time.time() < deadline:
        try:
            res = await client(ImportLoginTokenRequest(token_bytes))
        except Exception as exc:
            msg = str(exc)
            if "expired" in msg or "invalid" in msg.lower():
                logger.warning("二维码过期，重新生成...")
                return False
            logger.debug("ImportLoginToken: %s", msg)
            await asyncio.sleep(1)
            continue

        if isinstance(res, LoginTokenSuccess):
            logger.info("扫码授权成功！")
            return True
        if isinstance(res, LoginTokenMigrateTo):
            logger.info("迁移到 DC %s", res.dc_id)
            await client._switch_dc(res.dc_id)
            continue
        if isinstance(res, LoginToken):
            token_bytes = res.token
            continue
    return False


async def main() -> None:
    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        logger.info("已登录: %s", me.username or me.first_name)
        return

    print("=" * 60)
    print(" 用【手机 Telegram App】扫下方二维码授权登录")
    print(" (已登录的 Telegram Desktop 右上角 设置→设备→扫码 也行)")
    print("=" * 60)

    start = time.time()
    while time.time() < start + TOTAL_TIMEOUT:
        # 1. 获取/刷新二维码
        res = await client(ExportLoginTokenRequest(API_ID, API_HASH, []))
        if isinstance(res, LoginTokenMigrateTo):
            logger.info("迁移到 DC %s", res.dc_id)
            await client._switch_dc(res.dc_id)
            continue
        if isinstance(res, LoginTokenSuccess):
            logger.info("已授权！")
            return

        token_bytes = res.token
        tg_url = "tg://login?token=" + base64.urlsafe_b64encode(res.token).rstrip(b"=").decode()
        print("\n" + "─" * 60)
        print(f" [{time.strftime('%H:%M:%S')}] 新二维码（{TOKEN_TTL}s 内有效，扫完稍等）")
        print("─" * 60)
        render_qr(tg_url)

        # 2. 轮询扫码结果
        if await try_login(client, token_bytes):
            me = await client.get_me()
            logger.info("已登录: %s (%s)", me.username or me.first_name, me.id)
            print("登录完成 ✅ session 已保存")
            return
        logger.warning("二维码过期，生成新二维码...")

    print("总超时（5 分钟），请重跑脚本")
    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已取消")
