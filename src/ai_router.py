"""AI 路由分发：多后端支持 + 统一接口 + 配置热重载。

支持的 provider:
- openai / openai_compatible : OpenAI 兼容 chat/completions（覆盖 GPT、DeepSeek、Ollama 等）
- anthropic                    : Anthropic Messages API（Claude）
- google                       : Google Gemini generateContent

统一入口: AIRouter.route_message(record, force_agent=None)
调用失败自动捕获并记录日志，返回 None 表示无回复。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import aiohttp
import yaml

from .models import (
    AgentConfig,
    MessageRecord,
    RoutingRule,
    agent_from_dict,
    routing_rule_from_dict,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_TIMEOUT_SECONDS = 60
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: Any) -> Any:
    """递归替换字符串中的 ${ENV_VAR} 占位符为环境变量值（未设置则替换为空）。"""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


class AIRouter:
    """AI 路由核心类。"""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.agents: dict[str, AgentConfig] = {}
        self.rules: list[RoutingRule] = []
        self.default_agent: str = ""
        self._sem = asyncio.Semaphore(4)  # 限制并发调用，避免打爆限流

    # ---------- 配置加载 / 热重载 ----------

    def load_config(self) -> None:
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        data = _resolve_env(raw)
        raw_agents = data.get("agents", []) or []
        self.agents = {a.name: a for a in (agent_from_dict(x) for x in raw_agents)}
        routing = data.get("routing", {}) or {}
        self.default_agent = routing.get("default_agent", "") or ""
        self.rules = [
            routing_rule_from_dict(r) for r in (routing.get("rules", []) or [])
        ]
        enabled = [n for n, a in self.agents.items() if a.enabled]
        logger.info(
            "AI 配置已加载: %d 个 agent(%s), %d 条路由规则, 默认=%s",
            len(self.agents),
            enabled or "无",
            len(self.rules),
            self.default_agent or "-",
        )

    def reload_config(self) -> None:
        """SIGHUP 信号触发热重载。"""
        try:
            self.load_config()
            logger.info("AI 配置已热重载")
        except Exception:
            logger.exception("AI 配置热重载失败，保留旧配置")

    # ---------- 路由 ----------

    async def route_message(
        self,
        record: MessageRecord,
        force_agent: Optional[str] = None,
    ) -> Optional[str]:
        """路由并调用 AI，返回回复文本；无可用 agent 或调用失败返回 None。"""
        agent = self._pick_agent(record, force_agent)
        if agent is None:
            logger.info("无可用 AI agent，跳过消息 chat=%s type=%s", record.chat_id, record.message_type)
            return None
        try:
            async with self._sem:
                return await self._call(agent, record.content)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI 调用失败 agent=%s provider=%s", agent.name, agent.provider)
            return None

    def _pick_agent(
        self, record: MessageRecord, force_agent: Optional[str] = None
    ) -> Optional[AgentConfig]:
        """选择最优可用 agent：force > 路由规则 > 默认，最后兜底任意已启用 agent。"""
        candidates: list[str] = []
        if force_agent:
            candidates.append(force_agent)
        else:
            for rule in self.rules:
                if rule.match(record):
                    candidates.append(rule.agent)
                    break
            if self.default_agent:
                candidates.append(self.default_agent)

        for name in candidates:
            agent = self.agents.get(name)
            if agent and agent.is_ready:
                return agent

        fallback = self._first_ready_agent()
        if fallback:
            logger.warning("规则指定 agent 不可用，兜底到 %s", fallback.name)
        return fallback

    def _first_ready_agent(self) -> Optional[AgentConfig]:
        for agent in self.agents.values():
            if agent.is_ready:
                return agent
        return None

    # ---------- 各 provider 实现 ----------

    async def _call(self, agent: AgentConfig, content: str) -> str:
        if agent.provider == "anthropic":
            return await self._call_anthropic(agent, content)
        if agent.provider == "google":
            return await self._call_gemini(agent, content)
        return await self._call_openai_compat(agent, content)

    async def _call_openai_compat(self, agent: AgentConfig, content: str) -> str:
        """OpenAI 兼容接口（openai / deepseek / ollama 等）。"""
        base = (agent.api_base or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        payload: dict[str, Any] = {
            "model": agent.model,
            "messages": [
                {"role": "system", "content": "你是一个 Telegram 消息监控助手的回复引擎，用简洁中文回答用户问题。"},
                {"role": "user", "content": content},
            ],
            "max_tokens": agent.max_tokens,
            "temperature": agent.temperature,
        }
        headers = {"Content-Type": "application/json"}
        if agent.api_key:
            headers["Authorization"] = f"Bearer {agent.api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    raise RuntimeError(f"openai-compat HTTP {resp.status}: {body}")
                return str(body["choices"][0]["message"]["content"])

    async def _call_anthropic(self, agent: AgentConfig, content: str) -> str:
        """Anthropic Messages API（Claude）。"""
        base = (agent.api_base or "https://api.anthropic.com").rstrip("/")
        url = f"{base}/v1/messages"
        payload: dict[str, Any] = {
            "model": agent.model,
            "max_tokens": agent.max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": agent.api_key or "",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    raise RuntimeError(f"anthropic HTTP {resp.status}: {body}")
                parts = body.get("content", []) or []
                return "".join(p.get("text", "") for p in parts if p.get("type") == "text")

    async def _call_gemini(self, agent: AgentConfig, content: str) -> str:
        """Google Gemini generateContent API。"""
        base = (agent.api_base or _GEMINI_API).rstrip("/")
        model = agent.model or "gemini-pro"
        url = f"{base}/models/{model}:generateContent"
        params = {"key": agent.api_key or ""}
        payload = {
            "contents": [{"parts": [{"text": content}]}],
            "generationConfig": {
                "maxOutputTokens": agent.max_tokens,
                "temperature": agent.temperature,
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                params=params,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    raise RuntimeError(f"gemini HTTP {resp.status}: {body}")
                candidates = body.get("candidates", []) or []
                if not candidates:
                    return ""
                parts = candidates[0].get("content", {}).get("parts", []) or []
                return "".join(p.get("text", "") for p in parts)