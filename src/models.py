"""数据模型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class MessageRecord:
    """一条待处理/已存储的 Telegram 消息。

    对应 messages 表的字段，另附若干不入库的运行时字段（chat_title 等）。
    """

    chat_id: int
    chat_type: str
    message_type: str
    content: str
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    user_id: Optional[int] = None
    username: Optional[str] = None
    media_file_id: Optional[str] = None
    ai_response: Optional[str] = None
    feishu_sent: bool = False
    id: Optional[int] = None

    # 运行时字段（不入库）：用于飞书推送展示
    chat_title: Optional[str] = None

    def to_feishu_text(self) -> str:
        """渲染成飞书可读的文本摘要。"""
        chat = self.chat_title or str(self.chat_id)
        user = self.username or str(self.user_id or "未知")
        lines = [
            f"**群组**: {chat} ({self.chat_id})",
            f"**用户**: {user}",
            f"**类型**: {self.message_type}",
            f"**时间**: {self.timestamp}",
            "",
            self.content or "（无文本内容）",
        ]
        return "\n".join(lines)


@dataclass
class AgentConfig:
    """单个 AI Agent 的配置。"""

    name: str
    enabled: bool = True
    provider: str = "openai_compatible"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model: str = ""
    max_tokens: int = 2000
    temperature: float = 0.7
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """是否具备调用条件：启用且已配置 api_key（或该 provider 不需要 key）。"""
        if not self.enabled:
            return False
        key_required = self.provider in {
            "openai",
            "openai_compatible",
            "anthropic",
            "google",
        }
        if key_required and not self.api_key:
            return False
        return True


@dataclass
class RoutingRule:
    """消息路由规则：按配置顺序匹配，命中即使用对应 agent。"""

    name: str
    agent: str
    chat_type: Optional[str] = None
    chat_ids: list[int] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def match(self, record: MessageRecord) -> bool:
        if self.chat_type and record.chat_type != self.chat_type:
            return False
        if self.chat_ids and record.chat_id not in self.chat_ids:
            return False
        if self.keywords:
            lowered = record.content.lower()
            if not any(k.lower() in lowered for k in self.keywords):
                return False
        return True


@dataclass
class KeywordRule:
    """关键词触发规则。"""

    name: str
    keywords: list[str]
    action: str = "ai_reply"
    agent: Optional[str] = None
    priority: str = "normal"
    response_template: Optional[str] = None

    def match(self, content: str) -> bool:
        lowered = content.lower()
        return any(k.lower() in lowered for k in self.keywords)


def agent_from_dict(data: dict[str, Any]) -> AgentConfig:
    """从 YAML 字典构造 AgentConfig。"""
    extra = {k: v for k, v in data.items() if k not in _AGENT_FIELDS}
    return AgentConfig(
        name=data.get("name", ""),
        enabled=bool(data.get("enabled", True)),
        provider=data.get("provider", "openai_compatible"),
        api_base=data.get("api_base"),
        api_key=data.get("api_key"),
        model=data.get("model", ""),
        max_tokens=int(data.get("max_tokens", 2000)),
        temperature=float(data.get("temperature", 0.7)),
        extra=extra,
    )


_AGENT_FIELDS = {
    "name",
    "enabled",
    "provider",
    "api_base",
    "api_key",
    "model",
    "max_tokens",
    "temperature",
}


def routing_rule_from_dict(data: dict[str, Any]) -> RoutingRule:
    match = data.get("match", {}) or {}
    return RoutingRule(
        name=data.get("name", ""),
        agent=data.get("agent", ""),
        chat_type=match.get("chat_type"),
        chat_ids=list(match.get("chat_ids", []) or []),
        keywords=list(match.get("keywords", []) or []),
    )


def keyword_rule_from_dict(data: dict[str, Any]) -> KeywordRule:
    return KeywordRule(
        name=data.get("name", ""),
        keywords=list(data.get("keywords", []) or []),
        action=data.get("action", "ai_reply"),
        agent=data.get("agent"),
        priority=data.get("priority", "normal"),
        response_template=data.get("response_template"),
    )
