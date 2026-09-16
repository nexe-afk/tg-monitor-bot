#!/usr/bin/env python3
"""新号一次性登录：请求验证码一次 → 等用户写 .auth_code → 登录（含2FA）→ 保存 session。

只请求一次验证码，绝不重复。session 保存在 .tg_user.session，后续永久复用。
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SESSION = str(BASE_DIR / ".tg_user")
AUTH_CODE = BASE_DIR / ".auth_code"

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE = os.getenv("TG_PHONE", "")
PASSWORD = os.getenv("TG_2FA_PASSWORD", "")  # 用户提供的 2FA 密码


async def login():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ session 已有效: {me.first_name} @{me.username} id={me.id} —— 无需验证码")
        return

    print(f"① 请求验证码（仅一次） phone={PHONE} ...")
    sent = await client.send_code_request(PHONE)
    print(f"   phone_code_hash={sent.phone_code_hash}")
    print("② 等待你把验证码写入 .auth_code 文件（240 秒超时）...")

    deadline = time.time() + 240
    code = None
    while time.time() < deadline:
        if AUTH_CODE.exists():
            code = AUTH_CODE.read_text().strip()
            if code and len(code) >= 5:
                AUTH_CODE.unlink(missing_ok=True)
                break
        await asyncio.sleep(2)

    if not code:
        print("❌ 等待验证码超时")
        return

    print(f"③ 验证码: {code} —— 立即登录...")
    try:
        await client.sign_in(phone=PHONE, code=code, phone_code_hash=sent.phone_code_hash)
    except SessionPasswordNeededError:
        print("   需要 2FA，用密码登录...")
        await client.sign_in(password=PASSWORD)
    except Exception as e:
        print(f"❌ 登录失败: {type(e).__name__}: {e}")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"✅ 登录成功! {me.first_name} @{me.username} id={me.id}")
    print(f"   session 已保存: {SESSION}.session —— 之后永远复用")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(login())