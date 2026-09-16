"""Telethon 实时监听：监听目标群消息，命中关键词后执行动作链。

动作链（每步失败不影响后续）:
  1. 写入命中记录
  2. 私信发消息的用户进群邀请链接
  3. 通知主管机器人拉人进群
  4. 报备 @wyyu39433
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient, events

from .db import MonitorDB

BASE_DIR = Path(__file__).resolve().parent.parent  # 任务3根目录
PANEL_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger("listener_monitor")


def _reload_actions() -> dict:
    """每次命中动作前重读 config.yaml，面板上改的邀请链接即时生效。"""
    try:
        cfg = yaml.safe_load((PANEL_DIR / "config.yaml").read_text(encoding="utf-8")) or {}
        return cfg.get("actions", {}) or {}
    except Exception:
        return {}


class ListenerMonitor:
    def __init__(self, db: MonitorDB, cfg: dict):
        self.db = db
        self.cfg = cfg
        self.api_id = int(os.getenv("TG_API_ID", "0"))
        self.api_hash = os.getenv("TG_API_HASH", "")
        session = cfg.get("listen", {}).get("session", ".tg_user")
        self.session_file = str(BASE_DIR / session)
        self.client = TelegramClient(self.session_file, self.api_id, self.api_hash)

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(f"session {self.session_file} 未授权，请先运行登录脚本")
        me = await self.client.get_me()
        logger.info("监听账号: %s id=%s", me.first_name, me.id)

        @self.client.on(events.NewMessage())
        async def handler(event):
            await self._on_message(event)

        await self.client.run_until_disconnected()

    # ---------- 消息处理 ----------

    async def _on_message(self, event) -> None:
        if not event.chat or event.chat_id <= 0:
            return  # 只看群/频道，忽略私聊（私聊命中另有用途时可放开）

        chat_ids = {c["id"] for c in self.cfg.get("listen", {}).get("target_chats", [])}
        if chat_ids and event.chat_id not in chat_ids:
            return

        text = (event.raw_text or "").strip()
        if not text:
            return

        keywords = self.db.active_keywords()
        if not keywords:
            return
        hit = next((k for k in keywords if k.lower() in text.lower()), None)
        if hit is None:
            return

        sender = await event.get_sender()
        sender_name = ""
        if sender is not None:
            sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", "") or str(sender.id)

        chat_name = ""
        try:
            chat_name = event.chat.title or ""
        except Exception:
            pass

        hit_id = self.db.add_hit(
            event.chat_id, chat_name, sender.id if sender else 0,
            sender_name, text, hit,
        )
        logger.info("命中 #%d: 群=%s 用户=%s 词=%s", hit_id, chat_name, sender_name, hit)

        await self._action_invite(hit_id, event, sender, chat_name, sender_name, text, hit)
        await self._action_notify_zhuguan(hit_id, chat_name, sender_name, text, hit)
        await self._action_report(hit_id, chat_name, sender_name, text, hit)

    async def _action_invite(self, hit_id, event, sender, chat_name, sender_name, text, hit) -> None:
        act = _reload_actions()
        if not act.get("send_invite_to_sender"):
            return
        invite_link = act.get("invite_link", "") or ""
        if not invite_link or sender is None or sender.id <= 0:
            return
        msg = (act.get("invite_text", "") or "").format(
            keyword=hit, invite_link=invite_link,
        )
        try:
            await self.client.send_message(sender, msg)
            self.db.mark_hit(hit_id, invited=True)
            logger.info("  ✅ 已私信 %s 进群链接", sender_name)
        except Exception as e:
            logger.warning("  私信进群链接失败: %s", e)

    async def _action_notify_zhuguan(self, hit_id, chat_name, sender_name, text, hit) -> None:
        act = _reload_actions()
        if not act.get("notify_zhuguan"):
            return
        zid = act.get("zhuguan_id")
        if not zid:
            return
        msg = (act.get("zhuguan_text", "") or "").format(
            chat_name=chat_name, sender=sender_name, text=text, keyword=hit,
        )
        try:
            await self.client.send_message(zid, msg)
            self.db.mark_hit(hit_id, notified=True)
            logger.info("  ✅ 已通知主管机器人拉人")
        except Exception as e:
            logger.warning("  通知主管失败: %s", e)

    async def _action_report(self, hit_id, chat_name, sender_name, text, hit) -> None:
        act = _reload_actions()
        if not act.get("report_enabled"):
            return
        rid = self.cfg.get("listen", {}).get("me_report_id")
        if not rid:
            return
        msg = (act.get("report_text", "") or "").format(
            chat_name=chat_name, sender=sender_name, sender_id="", text=text, keyword=hit,
        )
        try:
            await self.client.send_message(rid, msg)
            self.db.mark_hit(hit_id, reported=True)
            logger.info("  ✅ 已报备 %s", rid)
        except Exception as e:
            logger.warning("  报备失败: %s", e)
