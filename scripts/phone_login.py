#!/usr/bin/env python3
"""用手机号 + 平台取码完成 Telethon 登录（新号无2FA）。"""
from __future__ import annotations

import asyncio
import time
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SESSION = str(BASE_DIR / ".tg_monitor")
AUTH_CODE = BASE_DIR / ".auth_code"

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE = os.getenv("TG_PHONE", "")
PHONE_CODE_HASH = os.getenv("PHONE_CODE_HASH", "")  # 本次验证码请求的 hash


async def login():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"已登录: {me.first_name} @{me.username}")
        return

    print(f"phone_code_hash: {PHONE_CODE_HASH}")
    print(f"等待 .auth_code 文件写入验证码（180s超时）...")

    deadline = time.time() + 180
    code = None
    while time.time() < deadline:
        if AUTH_CODE.exists():
            code = AUTH_CODE.read_text().strip()
            if code and len(code) >= 5:
                AUTH_CODE.unlink(missing_ok=True)
                break
        await asyncio.sleep(2)

    if not code:
        print("等待超时")
        return

    print(f"验证码: {code}")
    try:
        await client.sign_in(phone=PHONE, code=code, phone_code_hash=PHONE_CODE_HASH)
        me = await client.get_me()
        print(f"✅ 登录成功! {me.first_name} @{me.username} id={me.id}")
    except SessionPasswordNeededError:
        print("❌ 需要2FA密码（该账号设置了两步验证）")
    except Exception as e:
        print(f"❌ 登录失败: {e}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(login())