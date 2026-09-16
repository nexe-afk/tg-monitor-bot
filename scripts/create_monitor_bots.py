#!/usr/bin/env python3
"""用已登录的 Kaolf 号（.tg_user.session）批量创建 10 个监听 bot。"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SESSION = str(BASE_DIR / ".tg_user")  # Kaolf 号，已登录
BOTFATHER = "botfather"
BOTS = [
    {"name": f"监听机器人{i+1:02d}", "username": f"tgmon_{i+1:02d}_bot", "agent": f"monitor_{i+1:02d}"}
    for i in range(10)
]
TOKEN_RE = re.compile(r"(\d{6,10}:[A-Za-z0-9_-]{30,45})")
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")


async def last_reply(client) -> str:
    msgs = await client.get_messages(BOTFATHER, limit=1)
    return (msgs[0].message or "") if msgs else ""


async def create_one(client, name: str, username: str) -> str | None:
    """创建单个 bot，返回 token；username 被占用则自动加后缀重试。"""
    for attempt in range(5):
        try:
            await client.send_message(BOTFATHER, "/newbot")
            await asyncio.sleep(3)
            txt = await last_reply(client)
            if "name" not in txt.lower():
                print(f"   ! /newbot 无响应: {txt[:60]}")
                await asyncio.sleep(10)
                continue

            await client.send_message(BOTFATHER, name)
            await asyncio.sleep(3)
            txt = await last_reply(client)
            if "username" not in txt.lower():
                print(f"   ! 名字未接受: {txt[:60]}")
                await asyncio.sleep(10)
                continue

            await client.send_message(BOTFATHER, username)
            await asyncio.sleep(3)
            txt = await last_reply(client)
            m = TOKEN_RE.search(txt)
            if m:
                return m.group(1)
            if "already taken" in txt.lower():
                suffix = f"{int(time.time()) % 10000}"
                new_uname = f"tgmon_{suffix}_bot"
                print(f"   ⚠️ {username} 被占用，重试 @{new_uname}")
                username = new_uname
                continue
            print(f"   ? 未识别回复: {txt[:80]}")
        except Exception as e:
            print(f"   ! 异常: {type(e).__name__}: {e}")
        await asyncio.sleep(8)
    return None


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("❌ session 未授权"); return
    me = await client.get_me()
    print(f"✅ 登录: {me.first_name} @{me.username} id={me.id}\n")

    results = []
    for i, bot in enumerate(BOTS):
        print(f"[{i+1}/10] {bot['name']} @{bot['username']} ...")
        token = await create_one(client, bot["name"], bot["username"])
        results.append({**bot, "token": token or "FAILED"})
        print(f"   {'✅ ' + token if token else '❌ 未拿到 token'}")
        await asyncio.sleep(35)  # bot 之间限流

    await client.disconnect()
    out_path = BASE_DIR / "config" / "tg_bots.yaml"
    existing: dict = {}
    if out_path.exists():
        existing = yaml.safe_load(out_path.read_text()) or {}
    existing["bots"] = results
    out_path.write_text(yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\n✅ 已保存 {len(results)} 个 bot 到 {out_path}")
    for b in results:
        print(f"{b['agent']:12s} {b['username']:20s} {b['token']}")


if __name__ == "__main__":
    asyncio.run(main())
