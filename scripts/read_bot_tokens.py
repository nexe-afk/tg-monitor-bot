#!/usr/bin/env python3
"""从 BotFather 读取已创建 bot 的 token（v2：每次点击后重新获取最新消息，按钮不跨消息复用）。"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Message

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SESSION = str(BASE_DIR / ".tg_user")
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
TOKEN_RE = re.compile(r"(\d{6,10}:[A-Za-z0-9_-]{30,45})")


async def wait_new_message(client, last_id: int, timeout: float = 10.0) -> Message | None:
    """轮询等待 BotFather 出现比 last_id 更新的消息。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages("botfather", limit=1)
        if msgs and msgs[0].id > last_id:
            return msgs[0]
        await asyncio.sleep(1.2)
    return None


async def click_by_text(client, msg: Message, text: str, last_id: int) -> tuple[Message | None, Message | None]:
    """在 msg 的按钮中找 text 匹配的按钮并点击。

    返回 (点击后的最新消息, 出错返回的旧消息)。点击按钮必须用当前消息解析出的按钮对象。
    """
    target = None
    for row in msg.buttons or []:
        for btn in row:
            if (btn.text or "") == text or text in (btn.text or ""):
                target = btn
                break
        if target:
            break
    if target is None:
        return None, msg
    await target.click()
    return await wait_new_message(client, last_id), None


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("❌ session 未授权")
        return
    me = await client.get_me()
    print(f"✅ 登录: {me.first_name} @{me.username} id={me.id}")

    # 1. 打开 /mybots 列表
    await client.send_message("botfather", "/mybots")
    list_msg = await wait_new_message(client, 0, timeout=8)
    if list_msg is None or not list_msg.buttons:
        print("❌ 未找到 /mybots 列表")
        return
    print(f"列表消息 id={list_msg.id}")

    bot_btns = []
    for row in list_msg.buttons:
        for btn in row:
            if (btn.data or b"").startswith(b"bots/") and not b"/tokn" in (btn.data or b""):
                bot_btns.append((btn.text or "", btn))
    print(f"共 {len(bot_btns)} 个 bot\n")

    results = []
    last_id = list_msg.id
    for i, (uname, btn) in enumerate(bot_btns):
        uname = uname.lstrip("@")
        print(f"[{i+1}/{len(bot_btns)}] @{uname} ...", flush=True)
        try:
            # a. 点击 bot 进入管理页
            new_msg, err = await click_by_text(client, list_msg, btn.text, last_id)
            if new_msg is None:
                print(f"  ! 进入管理页失败: {err.message if err and err.message else '超时'}")
                continue
            list_msg, last_id = new_msg, new_msg.id

            # b. 点击 API Token
            token_msg, err = await click_by_text(client, list_msg, "API Token", last_id)
            if token_msg is None:
                print(f"  ! 点 API Token 失败")
                continue
            list_msg, last_id = token_msg, token_msg.id
            m = TOKEN_RE.search(token_msg.message or "")
            if m:
                results.append({"username": uname, "token": m.group(1)})
                print(f"  ✅ token 获取成功", flush=True)
            else:
                print(f"  ! 回复中无 token: {(token_msg.message or '')[:120]}")

            # c. 返回列表
            back_msg, _ = await click_by_text(client, list_msg, "Back to Bot List", last_id)
            if back_msg is not None:
                list_msg, last_id = back_msg, back_msg.id
        except Exception as e:
            print(f"  ! 异常: {type(e).__name__}: {e}")
        if i < len(bot_btns) - 1:
            await asyncio.sleep(38)

    await client.disconnect()

    out = BASE_DIR / "config" / "tg_bots.yaml"
    existing = {}
    if out.exists():
        existing = yaml.safe_load(out.read_text()) or {}
    existing["bots"] = results
    out.write_text(yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\n✅ 已保存 {len(results)} 个 bot token 到 {out}")
    for b in results:
        print(f"  @{b['username']:20s} {b['token']}")


if __name__ == "__main__":
    asyncio.run(main())
