# TG 监控机器人 — 架构设计文档

> 版本：v1.1（对齐 src/ 实现） ｜ 作者：Team Architect ｜ 更新：2026-09-15
> 定位：在本地 macOS 上独立运行的全新 Python 实现，**不修改、不依赖**香港 97bot 生产实例。97bot 仅作为被监听的对象（Bot 被邀请进群/被私聊）。

---

## 1. 架构总览

单进程、全异步、绿色部署。消息链路：**TG 监听 → 过滤 → 落库 → 关键词触发 → AI 路由 → 回发/推送**。数据落本地 SQLite，AI 回复输出到三个出口：**TG 直接回复 / 本地记录 / 飞书推送**。

```
┌────────────────────────────────────────────────────────────────┐
│                    本地 macOS 进程 (Python 3.11+)              │
│                                                                │
│   python-telegram-bot (Application)                            │
│   ┌──────────────────────────────────────────────────┐         │
│   │  MessageHandler(filters.ALL) → handle_update     │         │
│   │  ┌─────────────┐  ┌──────────────┐  ┌─────────┐  │         │
│   │  │ _should_    │→ │ extract_     │→ │ DB      │  │         │
│   │  │ monitor     │  │ message      │  │ 落库    │  │         │
│   │  │ (群/私聊过滤)│  │ (全类型抽取) │  │ (全量)  │  │         │
│   │  └─────────────┘  └──────────────┘  └────┬────┘  │         │
│   │                                           ▼        │         │
│   │  ┌────────────────────────┐  ┌────────────────────┐ │         │
│   │  │ KeywordMatcher 关键词命中│→│ AIRouter 多后端路由 │ │         │
│   │  │ feishu_alert / ai_reply│  │ rules→default→     │ │         │
│   │  └────────────────────────┘  │ fallback           │ │         │
│   └──────────────────────────────────┬─────────────────┘         │
│                                      ▼                          │
│              ┌─────────────────────────────────────┐            │
│              │ 回复出口（execute 层）              │            │
│              │ ① reply_text 回发 TG 会话           │            │
│              │ ② update_ai_response 落库           │            │
│              │ ③ FeishuNotifier 推送/告警          │            │
│              └─────────────────────────────────────┘            │
│   配置: settings.yaml + ai_agents.yaml + keyword_rules.yaml     │
│   SIGHUP → 热重载 AI 与关键词配置（无需重启）                    │
└────────────────────────────────────────────────────────────────┘
```

**框架选型：`python-telegram-bot v20+`（异步）**
- 生态成熟，`filters.ALL` 一条 handler 覆盖全类型消息，`Message` 对象对各媒体类型的属性暴露清晰；
- `Application` + `Updater.start_polling` 开箱即用，SIGHUP 信号热重载方案简单可靠；
- 对比 aiogram：本项目链路简单（单 handler + 规则分发），不需要中间件机制，选 PTB 减少概念负担。

---

## 2. 目录结构（实际落地）

```
任务3/
├── ARCHITECTURE.md                  # 本文档
├── README.md                        # 快速上手（中文）
├── requirements.txt                 # 依赖清单
├── .env.example                     # 密钥外置（bot token、API keys）
├── config/
│   ├── settings.yaml                # 主配置：bot 监听范围/DB/飞书/日志
│   ├── ai_agents.yaml               # 5× AI Agent 定义 + 路由规则（可热重载）
│   └── keyword_rules.yaml           # 关键词触发规则（可热重载）
├── src/
│   ├── __init__.py
│   ├── main.py                      # 入口：装配 + 信号处理 + 启动轮询
│   ├── bot.py                       # TG 监听核心 + 全类型消息抽取
│   ├── models.py                    # 数据模型（MessageRecord/Agent/Rule）
│   ├── database.py                  # SQLite 持久化 + 统计查询
│   ├── ai_router.py                 # 多后端 AI 路由（热重载）
│   ├── keyword_match.py             # 关键词匹配引擎（热重载）
│   └── feishu_push.py               # 飞书推送（webhook + App API 预留）
├── db/
│   └── schema.sql                   # 演进蓝图：扩展表结构（见 §7）
├── data/                            # 运行时数据（.gitignore）
│   ├── messages.db                  # SQLite
│   └── logs/                        # 运行日志
└── .venv/                           # 虚拟环境（已安装依赖）
```

---

## 3. 模块职责

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `src/main.py` | 入口：加载 .env + settings.yaml → 建 DB → 实例化 Router/Matcher/Feishu/Bot → 挂 handler → 轮询；SIGHUP 热重载、SIGINT/TERM 优雅退出 | `bootstrap(settings)`, `main()`, `resolve_env_placeholder()` |
| `src/bot.py` | 监听核心：`_should_monitor` 过滤（私聊开关 + 群白名单）→ `extract_message` 全类型抽取 → 落库 → 关键词规则 → 默认 AI 路由 → 回发 TG → 飞书推送 | `TgMonitorBot.handle_update()`, `extract_message()`, `_describe_content()` |
| `src/models.py` | 数据模型：`MessageRecord`（消息）、`AgentConfig`（AI 后端，含 `is_ready`）、`RoutingRule`/`KeywordRule`（规则，含 `match()`）、YAML → dataclass 转换函数 | `MessageRecord.to_feishu_text()` |
| `src/database.py` | SQLite（aiosqlite 全异步 + 互斥锁）：messages 表增改查 + 统计（按天/群/用户） | `insert_message()`, `update_ai_response()`, `update_feishu_sent()`, `count_by_day/chat/user()` |
| `src/ai_router.py` | 5 后端路由：`openai` / `openai_compatible`(DeepSeek/Ollama) / `anthropic`(Claude) / `google`(Gemini)；路由链 force > rules > default > 任意可用兜底；Semaphore(4) 限流；失败返回 None 不阻塞流水线 | `route_message(record, force_agent=None)`, `_pick_agent()`, `reload_config()` |
| `src/keyword_match.py` | 关键词规则加载 + 匹配，按优先级（high>normal>low）排序返回命中列表 | `match(content) -> list[KeywordRule]`, `reload_config()` |
| `src/feishu_push.py` | 飞书推送：webhook（text/markdown/card 三种 payload）+ App API 预留（`_get_tenant_access_token` / `send_via_api`）；`usable` 判定未配置即跳过 | `push_message()`, `push_alert()`, `push_ai_reply()`, `send_markdown()`, `send_card()` |

### 一条消息的生命周期（实际流水线）

```
Telegram Update
   │
   ▼
[bot.py] _should_monitor()         群/私聊白名单过滤 ——不通过→ END
   │ 通过
   ▼
[bot.py] extract_message()         全类型抽取 (text/photo/video/doc/audio/
   │                               voice/sticker/animation/contact/...)
   ▼
[database.py] insert_message()     ★ 先落库（监控第一性：任何消息都记录）
   │
   ▼
[keyword_match.py] match() ──命中──▶ feishu_alert  → push_alert（飞书告警）
   │                                  ai_reply    → route_message(force_agent=rule.agent)
   │ 未命中
   ▼
[ai_router.py] route_message()     规则匹配(chat_type/chat_ids/keywords)
   │                               → 默认 agent → 兜底可用 agent
   ├─ 有回复 → [bot.py] _handle_ai_reply → update_ai_response + reply_text 回发 TG
   │                                    └─ push_ai_reply（可选，配置开关）
   └─ 无回复 → 静默（已落库，可查）
   │
   ▼
[feishu_push.py] push_message()    新消息推送飞书（可选）→ update_feishu_sent
```

---

## 4. 5× 子 AI 配置方案（`config/ai_agents.yaml`）

5 个 Agent 已在配置中定义，**按需 `enabled: true` 激活**；密钥统一走 `.env` 的 `${ENV_VAR}` 占位符（`main.py` 递归替换）：

| Agent | provider | 模型 | 用途建议 |
|---|---|---|---|
| `gpt4` | `openai` | gpt-4o | 默认客服/通用（默认启用） |
| `claude` | `anthropic` | claude-sonnet-4 | 代码分析/长文 |
| `deepseek` | `openai_compatible` | deepseek-chat | 中文内容/低成本 |
| `gemini` | `google` | gemini-2.0-flash | 快速响应 |
| `local` | `openai_compatible` | qwen2:7b (Ollama) | 本地私密处理 |

```yaml
# ai_agents.yaml 结构（节选）
agents:
  - name: "gpt4"
    enabled: true
    provider: "openai"               # openai | openai_compatible | anthropic | google
    api_base: "https://api.openai.com/v1"
    api_key: "${OPENAI_API_KEY}"     # 从 .env 注入
    model: "gpt-4o"
    max_tokens: 2000
    temperature: 0.7

routing:
  default_agent: "gpt4"
  rules:                              # 按顺序 first-match；match 支持 chat_type/chat_ids/keywords
    - name: "私聊 GPT"
      match: { chat_type: "private" }
      agent: "gpt4"
    - name: "关键词 Claude"
      match: { keywords: ["分析", "代码", "review"] }
      agent: "claude"
```

**路由优先级链**（`ai_router._pick_agent`）：`force_agent（关键词规则指定） > routing.rules 首条命中 > default_agent > 任意 enabled 可用 agent 兜底`。目标 agent 不可用（未启用/无 key）自动 fallback，不中断流水线。

---

## 5. 消息流转图（文字版）

```
① TG 上游                  ② 监听层                   ③ 过滤层
群/私聊消息 ──────────────▶  MessageHandler(ALL)  ───▶  _should_monitor
(text/photo/video/doc...)   │  extract_message()       │ 私聊开关/群白名单
                            │  全类型 → MessageRecord  │
                            ▼                          ▼ 通过
                     ★ insert_message 全量落库         │
                                                      ▼
⑤ 动作出口            ④ AI 路由                         │
① reply_text 回发TG ◀─  ai_router.route_message        │
② ai_response 落库      rules→default→fallback ────────┤
③ FeishuNotifier ────────────── ③.5 keyword_match ─────┘
   push_message/alert     命中 → feishu_alert 直推飞书
   (webhook 卡片)         命中 → ai_reply 强制指定 agent
```

**关键约束**
- **先落库再判断**：任何消息在关键词/AI 判断前已写入 `messages`（`bot.py:70` 在 `matcher.match` 之前），这是监控的第一性。
- **AI 失败不阻塞**：`route_message` 捕获所有异常返回 `None`，流水线继续走飞书推送，消息永远留在库里。
- **并发限流**：`AIRouter._sem = Semaphore(4)`，防打爆 API 限流。
- **热重载**：`kill -HUP <pid>` 重读 `ai_agents.yaml` / `keyword_rules.yaml`（`main.py` 注册 SIGHUP）。
- **群隐私模式**：要让 bot 收齐群内**所有**消息，须在 BotFather 对该 bot 执行 `/setprivacy → Disable`（否则只能收到命令/被提及消息）。

---

## 6. 飞书接口契约（`src/feishu_push.py`）

**现状**：Webhook 群机器人方式已实现；App API 方式已预留接口骨架，由 **feishu-research Agent** 定案后完善。

```python
class FeishuNotifier:
    # 对外业务接口（bot.py 调用）
    async def push_message(self, record: MessageRecord) -> bool    # 新消息推送
    async def push_alert(self, title: str, content: str) -> bool   # 关键词告警
    async def push_ai_reply(self, record: MessageRecord, reply: str) -> bool  # AI 回复推送
    # 底层发送（webhook 已实现）
    async def send_text(self, text: str) -> bool
    async def send_markdown(self, title: str, content: str) -> bool
    async def send_card(self, title: str, elements: list[dict], template: str = "blue") -> bool
    # 预留：App API
    async def _get_tenant_access_token(self) -> Optional[str]
    async def send_via_api(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> bool
```

- `enabled=false` 或未配 webhook 时 `usable=False`，所有推送静默跳过（不抛异常）。
- 配置热更新入口 `update_settings()` 已实现，飞书方案变更无需重启。

---

## 7. SQLite 数据库设计

### 7.1 当前实现（`src/database.py` 内嵌 schema，单表）

```sql
CREATE TABLE messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        INTEGER NOT NULL,
    chat_type      TEXT NOT NULL,          -- private|group|supergroup|channel
    user_id        INTEGER,
    username       TEXT,
    message_type   TEXT NOT NULL,          -- text|photo|video|document|...
    content        TEXT,                   -- 文本正文 / 图片 caption / [媒体占位]
    media_file_id  TEXT,                   -- TG file_id（媒体消息）
    timestamp      TEXT NOT NULL,          -- ISO 本地时间
    ai_response    TEXT,                   -- AI 回复（路由后回填）
    feishu_sent    INTEGER NOT NULL DEFAULT 0
);
-- 索引: (chat_id,timestamp) (user_id,timestamp) (timestamp)
```

### 7.2 演进蓝图（`db/schema.sql`，监控增强阶段启用）

监听量级 ≤1k msg/min 时单表够用；接入**媒体落盘、关键词命中回查、AI 成本核算、飞书控制面板**后建议分表：

| 表 | 用途 |
|---|---|
| `messages` | 主表：增加 `keyword_hit`、`details_json`(原始消息兜底审计) 字段 |
| `media_files` | 媒体登记：`file_unique_id` 唯一、`local_path`、`download_status` |
| `routing_hits` | 路由命中记录（规则调优） |
| `ai_calls` | AI 调用日志：tokens / latency / cost_usd / status（成本核算） |
| `chat_overrides` | 运行时覆盖：飞书面板写 `ai_profile/action/enabled` |
| `command_log` | 控制面板命令留痕 |
| `bot_state` | 键值状态：全局开关 / 统计快照 |

> `db/schema.sql` 已作为完整 DDL 落地，`database.py` 当前用单表 `_SCHEMA`；升级时改为启动执行 `schema.sql` 并补迁移即可（均为 `CREATE TABLE IF NOT EXISTS`，向后兼容）。

---

## 8. 核心依赖（`requirements.txt`）

```
python-telegram-bot>=20.7,<22   # TG 客户端框架（异步）
aiosqlite>=0.19.0               # SQLite 异步驱动
aiohttp>=3.9.0                  # AI 后端 & 飞书推送 HTTP 客户端
PyYAML>=6.0.1                   # 配置解析
python-dotenv>=1.0.0            # .env 密钥加载
```

> AI 调用未用 SDK，全部走 `aiohttp` 直连各 provider HTTP API（openai-compat `/chat/completions`、anthropic `/v1/messages`、gemini `generateContent`），依赖极简、无版本耦合。

---

## 9. 启动方式

```bash
cd /Volumes/PortableSSD/源码/任务3
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # 填 TG_BOT_TOKEN 等
# 按需修改 config/settings.yaml、config/ai_agents.yaml、config/keyword_rules.yaml

python -m src.main                # 启动（轮询模式）
```

- **热重载**：`kill -HUP <pid>` 重读 AI / 关键词配置
- **日志**：`data/logs/bot.log`（5MB 滚动 × 5）+ 控制台
- **统计查询**：`sqlite3 data/messages.db "SELECT ..."` 或调 `Database.count_by_day/chat/user`
- **优雅退出**：SIGINT / SIGTERM

**依赖 97bot 侧的配合动作（仅外部操作，不改配置）**：
- 将新 bot 添加到目标群（群主邀请即达）；
- BotFather 对该 bot 执行 `/setprivacy → Disable`（收全量消息的前提）。

---

## 10. 演进建议（后续迭代，供 team 排期）

1. **媒体落盘**：`extract_message` 已抽取 `media_file_id`，新增 `storage` 模块下载到 `data/media/` 并回填 `media_files` 表（图片内容 AI 分析前置条件）。
2. **飞书控制面板**：feishu-research 定案后，在 `feishu_push.py` 之上加 `panel.py`（卡片按钮 → 查看统计/切换 AI/启停监听 → 写 `chat_overrides`/`command_log`）。
3. **AI 成本核算**：各 provider 返回体含 token 用量（`usage` 字段已由 API 返回，当前未解析），接入 `ai_calls` 表记录。
4. **消息上下文**：`route_message` 目前只传单条 content，可扩展带本群最近 N 条做上下文（`recent_messages()` 已就绪）。
5. **webhook 模式**：当前轮询；如需低延迟可切 webhook（HTTPS 反代 + `Application.run_webhook`）。
