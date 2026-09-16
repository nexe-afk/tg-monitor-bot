"""Telegram 机器人核心逻辑（python-telegram-bot v20+ 异步模式）。

消息处理流水线:
  收到消息 -> 过滤(群/私聊白名单) -> 落库 -> 关键词规则(告警/AI)
          -> 默认 AI 路由 -> 保存 AI 回复 -> 回发 TG -> 飞书推送
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Message, Update
from telegram.ext import ContextTypes, MessageHandler, filters

from .ai_router import AIRouter
from .database import Database
from .feishu_push import FeishuNotifier
from .keyword_match import KeywordMatcher
from .models import MessageRecord

logger = logging.getLogger(__name__)

# 需要 key 的 provider；无 key 时对应 agent 视为不可用
_KEY_PROVIDERS = {"openai", "openai_compatible", "anthropic", "google"}


class TgMonitorBot:
    """TG 消息监听与处理核心。"""

    def __init__(
        self,
        config: dict,
        db: Database,
        router: AIRouter,
        matcher: KeywordMatcher,
        feishu: FeishuNotifier,
    ):
        self.config = config
        self.db = db
        self.router = router
        self.matcher = matcher
        self.feishu = feishu

        bot_cfg = config.get("bot", {}) or {}
        self.monitor_groups: list[int] = list(bot_cfg.get("monitor_groups", []) or [])
        self.monitor_private: bool = bool(bot_cfg.get("monitor_private", True))
        self.reply_in_chat: bool = bool(bot_cfg.get("reply_in_chat", True))
        self.push_ai_reply: bool = bool(
            (config.get("feishu", {}) or {}).get("push_ai_reply", False)
        )

    # ---------- handler 注册 ----------

    def build_handler(self) -> MessageHandler:
        """构建覆盖所有消息类型的 MessageHandler。"""
        return MessageHandler(filters.ALL, self.handle_update)

    # ---------- 主处理流程 ----------

    async def handle_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.chat is None:
            return
        if not self._should_monitor(message):
            return

        record = extract_message(message)
        try:
            msg_id = await self.db.insert_message(record)
        except Exception:
            logger.exception("消息落库失败 chat=%s", record.chat_id)
            return
        record.id = msg_id

        # 1. 关键词规则处理（告警 / 指定 AI 回复）
        matched_rules = self.matcher.match(record.content)
        reply: Optional[str] = None
        if matched_rules:
            for rule in matched_rules:
                if rule.action == "feishu_alert":
                    await self._handle_alert(record, rule.name)
                elif rule.action == "ai_reply":
                    if reply is None:
                        reply = await self.router.route_message(record, force_agent=rule.agent)
                        record.ai_response = reply
                elif rule.action == "baobei":
                    await self._handle_baobei(record)

        # 2. 无关键词命中 -> 默认 AI 路由
        if reply is None and not matched_rules:
            reply = await self.router.route_message(record)
            record.ai_response = reply

        # 3. 保存 AI 回复并回发 TG
        if reply:
            await self._handle_ai_reply(record, message)

        # 4. 飞书推送新消息
        pushed = await self.feishu.push_message(record)
        if pushed:
            await self.db.update_feishu_sent(msg_id, True)

        # 5. 推送 AI 回复（可选）
        if reply and self.push_ai_reply and self.feishu.usable:
            await self.feishu.push_ai_reply(record, reply)

    async def _handle_alert(self, record: MessageRecord, rule_name: str) -> None:
        content = (
            f"{record.to_feishu_text()}\n\n触发规则: {rule_name}"
        )
        ok = await self.feishu.push_alert(rule_name, content)
        logger.info("关键词告警 %s -> 飞书 %s", rule_name, "成功" if ok else "跳过/失败")

    async def _handle_baobei(self, record: MessageRecord) -> None:
        """记录报备。触发词：报备 / 打卡 / 签到 / checkin。"""
        try:
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            await self.db.insert_baobei(
                chat_id=record.chat_id,
                user_id=record.user_id,
                username=record.username,
                content=record.content or "",
                timestamp=ts,
            )
            logger.info("报备已记录 chat=%s user=%s", record.chat_id, record.username)
        except Exception:
            logger.exception("报备记录失败")

    async def _handle_ai_reply(self, record: MessageRecord, message: Message) -> None:
        try:
            await self.db.update_ai_response(record.id, record.ai_response or "")
        except Exception:
            logger.exception("AI 回复落库失败 msg_id=%s", record.id)

        if self.reply_in_chat and record.ai_response:
            try:
                await message.reply_text(record.ai_response, disable_web_page_preview=True)
                logger.info("已回发 AI 回复 chat=%s msg_id=%s", record.chat_id, record.id)
            except Exception:
                logger.exception("AI 回复回发 TG 失败 chat=%s", record.chat_id)

    # ---------- 过滤 ----------

    def _should_monitor(self, message: Message) -> bool:
        chat = message.chat
        if chat.type == "private":
            return self.monitor_private
        if chat.type in {"group", "supergroup", "channel"}:
            if not self.monitor_groups:
                return True  # 空列表 = 监听所有群
            return chat.id in self.monitor_groups
        return False


# ---------- 消息抽取 ----------


def extract_message(message: Message) -> MessageRecord:
    """从 telegram Message 提取为 MessageRecord（覆盖所有常见消息类型）。"""
    from_user = message.from_user
    user_id = from_user.id if from_user else None
    username = None
    if from_user:
        username = from_user.username or from_user.full_name or None

    message_type, content, media_file_id = _describe_content(message)

    return MessageRecord(
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        user_id=user_id,
        username=username,
        message_type=message_type,
        content=content,
        media_file_id=media_file_id,
        chat_title=getattr(message.chat, "title", None),
    )


def _describe_content(message: Message) -> tuple[str, str, Optional[str]]:
    """返回 (message_type, content, media_file_id)。"""
    # 纯文本
    if message.text is not None:
        return "text", message.text, None

    # 带 caption 的媒体：类型取媒体类型，内容取 caption
    if message.photo:
        file = message.photo[-1]
        return "photo", message.caption or "[图片]", file.file_id
    if message.video:
        return "video", message.caption or "[视频]", message.video.file_id
    if message.document:
        name = message.document.file_name or ""
        return "document", message.caption or f"[文件: {name}]", message.document.file_id
    if message.audio:
        title = message.audio.title or message.audio.file_name or ""
        return "audio", message.caption or f"[音频: {title}]", message.audio.file_id
    if message.voice:
        return "voice", message.caption or "[语音]", message.voice.file_id
    if message.video_note:
        return "video_note", message.caption or "[视频消息]", message.video_note.file_id
    if message.sticker:
        emoji = message.sticker.emoji or ""
        return "sticker", f"[贴纸: {emoji}]", message.sticker.file_id
    if message.animation:
        return "animation", message.caption or "[GIF 动画]", message.animation.file_id
    if message.contact:
        name = message.contact.first_name or ""
        return "contact", f"[联系人: {name}]", None
    if message.location:
        return "location", "[位置]", None
    if message.poll:
        question = message.poll.question or ""
        return "poll", f"[投票: {question}]", None
    if message.dice:
        return "dice", f"[骰子: {message.dice.value}]", None
    if message.game:
        return "game", f"[游戏: {message.game.title}]", None
    if message.venue:
        return "venue", "[地点]", None
    return "unknown", "[未知消息类型]", None