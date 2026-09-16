"""飞书消息推送。

两条通道，按可用性自动选择：
1. App API（方案 B 主通道）: app_id/app_secret -> tenant_access_token -> im/v1/messages
2. Webhook（群机器人兜底）: POST bot/v2/hook/<token>
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import aiohttp

from .models import MessageRecord

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15
_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuNotifier:
    def __init__(
        self,
        enabled: bool = False,
        webhook_url: str = "",
        app_id: str = "",
        app_secret: str = "",
        chat_id: str = "",
        push_ai_reply: bool = False,
    ):
        self.enabled = enabled
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self.push_ai_reply = push_ai_reply
        self._token: Optional[str] = None

    # ---------- 配置更新 / 热重载 ----------

    def update_settings(
        self,
        enabled: Optional[bool] = None,
        webhook_url: Optional[str] = None,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        if webhook_url is not None:
            self.webhook_url = webhook_url
        if app_id is not None:
            self.app_id = app_id
            self._token = None
        if app_secret is not None:
            self.app_secret = app_secret
            self._token = None
        if chat_id is not None:
            self.chat_id = chat_id
        logger.info(
            "飞书配置已更新: enabled=%s webhook=%s app=%s",
            self.enabled,
            bool(self.webhook_url),
            bool(self.app_id and self.app_secret),
        )

    @property
    def usable(self) -> bool:
        """任一通道可用即视为可用：Webhook 或 App API。"""
        if not self.enabled:
            return False
        return bool(self.webhook_url) or bool(self.app_id and self.app_secret and self.chat_id)

    @property
    def api_ready(self) -> bool:
        return bool(self.app_id and self.app_secret and self.chat_id)

    # ---------- 对外接口 ----------

    async def push_message(self, record: MessageRecord) -> bool:
        """推送一条 TG 消息到飞书。"""
        if not self.usable:
            logger.debug("飞书未启用或未配置，跳过推送 chat=%s", record.chat_id)
            return False
        title = "TG 新消息"
        text = record.to_feishu_text()
        ok = await self.send_markdown(title, text)
        if ok:
            logger.info("已推送消息到飞书 msg_id=%s", record.id)
        return ok

    async def push_alert(self, title: str, content: str) -> bool:
        """推送告警（关键词触发 feishu_alert）。"""
        if not self.usable:
            return False
        return await self.send_markdown(f"[告警] {title}", content)

    async def push_ai_reply(self, record: MessageRecord, reply: str) -> bool:
        """推送 AI 回复到飞书。"""
        if not self.usable or not self.push_ai_reply:
            return False
        markdown = (
            record.to_feishu_text()
            + "\n\n---\n"
            + f"**AI 回复**:\n{reply}"
        )
        return await self.send_markdown("TG AI 回复", markdown)

    # ---------- 发送实现 ----------

    async def send_text(self, text: str) -> bool:
        """发送纯文本消息。"""
        if not self.usable:
            return False
        return await self._send_text(text)

    async def send_markdown(self, title: str, content: str) -> bool:
        """发送富文本（interactive card）消息，支持 Markdown。"""
        if not self.usable:
            return False
        card = self._build_card(title, content)
        return await self._send_card(card)

    async def send_card(self, title: str, elements: list[dict], template: str = "blue") -> bool:
        """发送自定义卡片。elements 为飞书卡片元素列表。"""
        if not self.usable:
            return False
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "elements": elements,
        }
        return await self._send_card(card)

    # ---------- 通道分发 ----------

    async def _send_text(self, text: str) -> bool:
        if self.api_ready:
            payload = {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
            return await self._post_api(payload)
        payload = {"msg_type": "text", "content": {"text": text}}
        return await self._post_webhook(payload)

    async def _send_card(self, card: dict) -> bool:
        if self.api_ready:
            payload = {
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            }
            return await self._post_api(payload)
        payload = {"msg_type": "interactive", "card": card}
        return await self._post_webhook(payload)

    @staticmethod
    def _build_card(title: str, content: str) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [{"tag": "markdown", "content": content}],
        }

    async def _post_webhook(self, payload: dict) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
                ) as resp:
                    body = await resp.json(content_type=None)
                    code = body.get("code", -1) if isinstance(body, dict) else -1
                    if resp.status == 200 and code in (0, None):
                        return True
                    logger.warning("飞书 webhook 返回异常: HTTP %s body=%s", resp.status, body)
                    return False
        except Exception:
            logger.exception("飞书 webhook 推送失败")
            return False

    async def _post_api(self, payload: dict) -> bool:
        token = await self._get_tenant_access_token()
        if not token:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_MSG_URL}?receive_id_type=chat_id",
                    json={"receive_id": self.chat_id, **payload},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status == 200 and body.get("code") == 0:
                        return True
                    logger.warning("飞书 API 推送异常: HTTP %s body=%s", resp.status, body)
                    return False
        except Exception:
            logger.exception("飞书 API 推送失败")
            return False

    # ---------- App API 鉴权 ----------

    async def _get_tenant_access_token(self) -> Optional[str]:
        """获取 tenant_access_token（2 小时有效，进程内缓存）。"""
        if not self.app_id or not self.app_secret:
            logger.warning("飞书 App 未配置 app_id/app_secret")
            return None
        if self._token:
            return self._token
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _TOKEN_URL,
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
                ) as resp:
                    body = await resp.json(content_type=None)
                    token = body.get("tenant_access_token") if resp.status == 200 else None
                    if token:
                        self._token = token
                    else:
                        logger.warning("飞书换取 token 失败: HTTP %s body=%s", resp.status, body)
                    return token
        except Exception:
            logger.exception("飞书换取 tenant_access_token 失败")
            return None