"""关键词匹配引擎。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from .models import KeywordRule, keyword_rule_from_dict

logger = logging.getLogger(__name__)

_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


class KeywordMatcher:
    """加载 keyword_rules.yaml，对消息内容做关键词匹配。"""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.rules: list[KeywordRule] = []

    def load_config(self) -> None:
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        raw_rules = data.get("rules", []) or []
        self.rules = [keyword_rule_from_dict(r) for r in raw_rules]
        logger.info("关键词规则已加载: %d 条", len(self.rules))

    def reload_config(self) -> None:
        """SIGHUP 信号触发热重载。"""
        try:
            self.load_config()
            logger.info("关键词规则已热重载")
        except Exception:
            logger.exception("关键词规则热重载失败，保留旧规则")

    def match(self, content: str) -> list[KeywordRule]:
        """匹配消息内容，返回按优先级（high 优先）排序的命中规则列表。"""
        if not content or not self.rules:
            return []
        content = content.strip()
        if not content:
            return []
        hits = [r for r in self.rules if r.match(content)]
        hits.sort(key=lambda r: _PRIORITY_ORDER.get(r.priority, 1))
        return hits