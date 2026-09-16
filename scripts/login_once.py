#!/usr/bin/env python3
"""一次性验证码登录：请求验证码 → 平台取码 → 立即登录 → 保存 session。

只调用一次验证码，绝不在同一 session 上重复请求。
登录成功后 session 保存在 .tg_monitor.session，后续所有操作复用。
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SESSION = str(BASE_DIR / ".tg_monitor")

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE = os.getenv("TG_PHONE", "")

HUMKT_TOKEN = os.getenv("HUMKT_TOKEN", "")
PAGE_URL = f"https://www.humkt.com/market/orders/109788/telegram?token={HUMKT_TOKEN}"
CODE_URL = f"https://www.humkt.com/market/order/109788/telegram-code?token={HUMKT_TOKEN}"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"


class HumktSession:
    """带真实 cookie jar 的 http 会话：session cookie / XSRF 自动流转，修掉 419。"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def seed(self, name: str, value: str):
        self.jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, "www.humkt.com",
            False, False, "/", True, False, None, False, None, None, {}
        ))

    def request(self, url: str, method: str = "GET", headers: dict | None = None):
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", UA)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with self.opener.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")


def make_gc() -> str:
    """复刻前端 _gc 反爬 cookie 算法。"""
    ts = int(time.time())
    raw = f"{ts}{len(UA)}{1920}"
    h = 0
    for c in raw:
        h = ((h << 5) - h) + ord(c)
        h &= 0xFFFFFFFF
    return f"{ts}.{abs(h)}"


def get_csrf(s: HumktSession) -> str | None:
    s.seed("_gc", make_gc())
    status, html = s.request(PAGE_URL)
    m = re.search(r'csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else None


def fetch_code(s: HumktSession, csrf: str, tries: int = 8) -> str | None:
    """轮询平台取码。平台没拿到码只锁 6s，快速重试即可，不重发验证码。"""
    for i in range(tries):
        status, body = s.request(
            CODE_URL,
            method="POST",
            headers={
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
                "Referer": PAGE_URL,
            },
        )
        try:
            data = json.loads(body)
        except Exception:
            print(f"  [#{i+1}] status={status} 非 JSON: {body[:150]}")
        else:
            code = data.get("code")
            if code:
                return str(code)
            msg = data.get("message") or ""
            print(f"  [#{i+1}] {msg}")
            if data.get("error_code") == "cooldown":
                time.sleep(data.get("cooldown", 6) + 1)
                continue
        time.sleep(5)
    return None


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ session 已有效: {me.first_name} id={me.id} —— 无需验证码")
        return

    print("① 向 Telegram 发出一次验证码请求（仅一次）...")
    sent = await client.send_code_request(PHONE)
    pch = sent.phone_code_hash
    print(f"   phone_code_hash={pch} —— 验证码正推送到该账号的在线设备")

    print("② 从平台轮询取码（验证码只发一次，只取不重发）...")
    s = HumktSession()
    csrf = get_csrf(s)
    if not csrf:
        print("❌ 页面未拿到 CSRF token")
        return

    code = fetch_code(s, csrf)
    if not code:
        print("❌ 平台未返回验证码 → 该账号当前没有可接收验证码的在线设备")
        print("   （取码依赖账号的在线会话；需平台侧保持设备在线后才能取到码）")
        return

    print(f"③ 取得验证码 {code}，立即登录...")
    try:
        await client.sign_in(phone=PHONE, code=code, phone_code_hash=pch)
        me = await client.get_me()
        print(f"✅ 登录成功! {me.first_name} @{me.username} id={me.id}")
        print(f"   session 已保存: {SESSION}.session —— 之后永远复用，不再要验证码")
    except Exception as e:
        print(f"❌ 登录失败: {type(e).__name__}: {e}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
