#!/usr/bin/env python3
"""批量创建 Telegram Bot（通过 BotFather）。

流程:
  1. 用 api_id/api_hash 登录你的 TG 账号（首次需验证码，写入 .auth_code 文件）
  2. 自动化 BotFather /newbot: 显示名 -> username -> 领取 token
  3. 保存 token 到 config/tg_bots.yaml，session 缓存到 .tg_session

用法:
  python scripts/create_bots.py --phone +86123... [--interval 35]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, UsernameNotOccupiedError

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("create_bots")

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_FILE = BASE_DIR / ".tg_session"
AUTH_CODE_FILE = BASE_DIR / ".auth_code"
BOTFATHER = "botfather"  # @BotFather

# 5 个 bot: 对应 5 个 AI agent（gpt4/claude/deepseek/gemini/local）
BOTS = [
    {"name": "GPT-4 监控助手", "username": "tgmon_gpt4_bot", "agent": "gpt4"},
    {"name": "Claude 监控助手", "username": "tgmon_claude_bot", "agent": "claude"},
    {"name": "DeepSeek 监控助手", "username": "tgmon_deepseek_bot", "agent": "deepseek"},
    {"name": "Gemini 监控助手", "username": "tgmon_gemini_bot", "agent": "gemini"},
    {"name": "本地模型助手", "username": "tgmon_local_bot", "agent": "local"},
]

TOKEN_RE = re.compile(r"(\d{6,10}:[A-Za-z0-9_-]{30,45})")


async def wait_code(client: TelegramClient) -> str:
    """轮询等待用户把验证码写入 .auth_code 文件。"""
    logger.info("验证码已发送到你的 Telegram 设备，等待写入 .auth_code ...")
    deadline = time.time() + 180
    while time.time() < deadline:
        if AUTH_CODE_FILE.exists():
            code = AUTH_CODE_FILE.read_text().strip()
            if code:
                AUTH_CODE_FILE.unlink(missing_ok=True)
                return code
        await asyncio.sleep(2)
    raise TimeoutError("等待验证码超时（180s）")


async def login(client: TelegramClient, phone: str) -> None:
    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        try:
            code = await wait_code(client)
        except TimeoutError:
            # 兼容: 直接交互式读 stdin
            code = input("输入验证码: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            pwd = os.getenv("TG_2FA_PASSWORD", "")
            if not pwd:
                pwd = input("两步验证密码: ").strip()
            await client.sign_in(password=pwd)
        logger.info("登录成功，session 已缓存到 %s", SESSION_FILE)
    else:
        logger.info("已使用缓存 session")


async def create_bot(
    client: TelegramClient, name: str, username: str, interval: int
) -> str | None:
    """向 BotFather 发一条 /newbot 交互，返回 token。"""
    await client.send_message(BOTFATHER, "/newbot")
    await asyncio.sleep(interval / 2)

    # 读取 BotFather 最新回复（每次发送后等一会再取）
    async def last_reply_text() -> str:
        msgs = await client.get_messages(BOTFATHER, limit=1)
        return msgs[0].message or "" if msgs else ""

    # 第 1 步：发显示名
    await client.send_message(BOTFATHER, name)
    await asyncio.sleep(interval)
    text = await last_reply_text()
    logger.debug("BotFather: %s", text[:120])

    # 第 2 步：发 username；若被占用则追加随机后缀重试
    final_username = username
    for attempt in range(4):
        await client.send_message(BOTFATHER, final_username)
        await asyncio.sleep(interval)
        text = await last_reply_text()
        m = TOKEN_RE.search(text)
        if m:
            return m.group(1)
        if "already taken" in text.lower() or "is already" in text.lower():
            suffix = f"_{int(time.time()) % 10000}"
            final_username = f"{username.rsplit('.', 1)[0]}{suffix}.bot" if False else f"{username[:-4]}{suffix}_bot"
            logger.warning("username %s 被占用，重试为 %s", username, final_username)
            # BotFather 需要重新开始或继续
            await client.send_message(BOTFATHER, "/cancel")
            await asyncio.sleep(2)
            await client.send_message(BOTFATHER, "/newbot")
            await asyncio.sleep(interval)
            await client.send_message(BOTFATHER, name)
            await asyncio.sleep(interval)
        else:
            logger.warning("未在回复中找到 token: %s", text[:200])
            return None
    return None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True, help="TG 手机号（+86 开头）")
    parser.add_argument("--interval", type=int, default=35, help="BotFather 交互间隔秒（防限流）")
    parser.add_argument("--only-username", help="仅创建指定 username 的 bot（测试用）")
    args = parser.parse_args()

    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)
    await login(client, args.phone)

    targets = BOTS
    if args.only_username:
        targets = [b for b in BOTS if b["username"] == args.only_username]

    results = []
    for i, bot in enumerate(targets):
        logger.info("[%d/%d] 创建 %s (@%s) ...", i + 1, len(targets), bot["name"], bot["username"])
        token = await create_bot(client, bot["name"], bot["username"], args.interval)
        if token:
            results.append({**bot, "token": token})
            logger.info("  ✅ %s -> %s", bot["username"], token)
        else:
            logger.error("  ❌ %s 创建失败", bot["username"])
        if i < len(targets) - 1:
            await asyncio.sleep(args.interval)

    await client.disconnect()

    # 落盘 config/tg_bots.yaml（保留旧结果再合并）
    out_path = BASE_DIR / "config" / "tg_bots.yaml"
    existing: dict = {}
    if out_path.exists():
        existing = yaml.safe_load(out_path.read_text()) or {}
    existing["bots"] = results
    out_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    logger.info("已保存 %d 个 bot 到 %s", len(results), out_path)
    for b in results:
        print(f"{b['agent']:10s} {b['username']:24s} {b['token']}")


if __name__ == "__main__":
    asyncio.run(main())
