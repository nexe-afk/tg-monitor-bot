-- ============================================================
-- schema.sql — TG Monitor Bot 数据库演进蓝图
-- ============================================================
-- 当前阶段：messages 单表够用（≤1k msg/min），
--           由 src/database.py 的 _SCHEMA 内嵌管理。
-- 演进阶段：接入媒体落盘/关键词回查/AI成本核算/飞书面板后，
--           启用本文件中的扩展表（全部 CREATE IF NOT EXISTS，向后兼容）。
-- 升级方式：数据库初始化时执行本文件 + 补迁移脚本。
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 1. messages（主表）— 扩展字段追加
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          INTEGER NOT NULL,
    chat_type        TEXT NOT NULL,          -- private|group|supergroup|channel
    user_id          INTEGER,
    username         TEXT,
    message_type     TEXT NOT NULL,          -- text|photo|video|document|audio|voice|sticker|animation|...
    content          TEXT,                   -- 文本正文 / 图片 caption / [媒体占位]
    media_file_id    TEXT,                   -- TG file_id（媒体消息）
    timestamp        TEXT NOT NULL,          -- ISO 本地时间
    ai_response      TEXT,                   -- AI 回复（路由后回填）
    feishu_sent      INTEGER NOT NULL DEFAULT 0,
    -- ── 演进字段 ──
    keyword_hit      TEXT,                  -- 命中的关键词规则名（逗号分隔，可多条）
    details_json     TEXT                   -- 原始 TG Update JSON（兜底审计用）
);
CREATE INDEX IF NOT EXISTS idx_messages_chat      ON messages (chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_user     ON messages (user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_ts       ON messages (timestamp);
-- 演进索引：v1 表无 keyword_hit 列时静默跳过（兼容新旧表）
CREATE INDEX IF NOT EXISTS idx_messages_kw       ON messages (keyword_hit)
    WHERE keyword_hit IS NOT NULL;

-- ────────────────────────────────────────────────────────────
-- 2. media_files — 媒体下载登记
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS media_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    file_unique_id   TEXT NOT NULL UNIQUE,    -- TG file_unique_id（全局唯一）
    file_id          TEXT NOT NULL,           -- TG file_id（会过期）
    local_path       TEXT,                    -- 落盘路径（相对 media/ 目录）
    file_type        TEXT NOT NULL,           -- photo|video|document|audio|voice|sticker|animation
    file_size_bytes  INTEGER,
    download_status  TEXT NOT NULL DEFAULT 'pending', -- pending|ok|failed
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_type  ON media_files (file_type);
CREATE INDEX IF NOT EXISTS idx_media_status ON media_files (download_status);

-- ────────────────────────────────────────────────────────────
-- 3. routing_hits — 路由规则命中记录
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS routing_hits (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       INTEGER REFERENCES messages(id),
    rule_name        TEXT NOT NULL,          -- 路由规则名
    agent_name       TEXT NOT NULL,          -- 命中的 AI agent
    matched_field    TEXT NOT NULL,          -- chat_type|chat_ids|keywords|default
    latency_ms       INTEGER,               -- 选择延迟（非 AI 调用）
    hit_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routing_rule ON routing_hits (rule_name);
CREATE INDEX IF NOT EXISTS idx_routing_msg  ON routing_hits (message_id);

-- ────────────────────────────────────────────────────────────
-- 4. ai_calls — AI 调用日志（成本核算）
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       INTEGER REFERENCES messages(id),
    agent_name       TEXT NOT NULL,          -- 使用的 agent（gpt4/claude/local/...）
    provider         TEXT NOT NULL,          -- openai|anthropic|google|openai_compatible
    model            TEXT NOT NULL,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    total_tokens     INTEGER,
    latency_ms       INTEGER,               -- 端到端耗时（含网络）
    cost_usd         REAL,                  -- 估算成本（美元，可选）
    status           TEXT NOT NULL DEFAULT 'ok', -- ok|error|timeout|fallback
    error_msg        TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_agent  ON ai_calls (agent_name);
CREATE INDEX IF NOT EXISTS idx_ai_status ON ai_calls (status);
CREATE INDEX IF NOT EXISTS idx_ai_time   ON ai_calls (created_at);

-- ────────────────────────────────────────────────────────────
-- 5. chat_overrides — 运行时配置覆盖（飞书面板写入）
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_overrides (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          INTEGER NOT NULL,
    key              TEXT NOT NULL,           -- ai_profile|action|enabled|reply_in_chat|...
    value            TEXT NOT NULL,           -- JSON 字符串
    updated_at       TEXT NOT NULL,
    UNIQUE(chat_id, key)
);

-- ────────────────────────────────────────────────────────────
-- 6. command_log — 控制面板命令留痕
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS command_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    command          TEXT NOT NULL,           -- /start|/pause|/resume|/stats|...
    issued_by        TEXT NOT NULL,           -- 飞书 open_id 或 'system'
    chat_id          INTEGER,                -- 来源群（私聊为 null）
    args             TEXT,                   -- 额外参数 JSON
    status           TEXT NOT NULL DEFAULT 'ok', -- ok|rejected|failed
    executed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cmd_name ON command_log (command);

-- ────────────────────────────────────────────────────────────
-- 7. bot_state — 键值状态（全局开关 / 统计快照）
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_state (
    key              TEXT PRIMARY KEY,       -- global_enabled|last_stats_snapshot|...
    value            TEXT NOT NULL,          -- JSON 字符串
    updated_at       TEXT NOT NULL
);

-- 默认状态
INSERT OR IGNORE INTO bot_state (key, value, updated_at)
VALUES ('global_enabled', 'true', datetime('now'));
