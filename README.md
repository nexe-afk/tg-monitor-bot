# TG 监控机器人

基于 Python 3.11+ 的 Telegram 消息监控机器人：全异步架构，支持群聊/私聊全消息类型监听、5 个 AI 后端智能路由、SQLite 持久化、飞书推送与关键词触发。

## 功能

- **完整消息监听**：文字 / 图片 / 视频 / 文件 / 语音 / 贴纸 / 动画 / 投票等全部消息类型
- **多 AI 路由**：OpenAI / Anthropic(Claude) / Google Gemini / DeepSeek / 本地模型，按路由规则分发，失败自动 fallback
- **消息持久化**：SQLite 存储全部消息，附带按天/按群/按用户统计
- **飞书推送**：新消息推送（Webhook 群机器人，App API 方式预留接口）
- **关键词触发**：自定义关键词规则，可触发指定 AI 回复或飞书告警
- **配置热重载**：`SIGHUP` 信号热更新 AI / 关键词配置，无需重启

## 快速开始

```bash
# 1. 安装依赖
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置
cp .env.example .env        # 填入 TG_BOT_TOKEN 等
vim config/settings.yaml    # 按需调整监听范围 / 飞书开关

# 3. 运行
python -m src.main
```

## 目录结构

```
├── config/
│   ├── settings.yaml       # 主配置（bot token / 数据库 / 飞书 / 日志）
│   ├── ai_agents.yaml      # 5 个 AI Agent + 路由规则
│   └── keyword_rules.yaml  # 关键词触发规则
├── src/
│   ├── main.py             # 入口：初始化 + 启动 + 信号处理
│   ├── bot.py              # TG 消息监听 / 类型抽取 / 处理流水线
│   ├── database.py         # SQLite 存储 + 统计
│   ├── ai_router.py        # 多后端 AI 路由分发
│   ├── feishu_push.py      # 飞书推送（webhook + 预留 API）
│   ├── keyword_match.py    # 关键词匹配引擎
│   └── models.py           # 数据模型
├── requirements.txt
└── .env.example
```

## 配置说明

### settings.yaml

| 项 | 说明 |
|---|---|
| `bot.monitor_groups` | 空数组 = 监听所有群；填入 chat_id 则白名单 |
| `bot.monitor_private` | 是否监听私聊 |
| `bot.reply_in_chat` | AI 回复是否回发到 TG 聊天 |
| `feishu.enabled` | 飞书推送总开关，配合 `webhook_url` 使用 |

### ai_agents.yaml

支持 5 种 provider：`openai` / `openai_compatible` / `anthropic` / `google`。路由规则按顺序匹配：`chat_type` → `chat_ids` → `keywords`，未命中走 `default_agent`，目标 agent 不可用时自动 fallback 到第一个已启用的 agent。

### keyword_rules.yaml

- `action: ai_reply` —— 用指定 agent 生成回复
- `action: feishu_alert` —— 直接推送飞书告警
- `priority: high > normal > low`，命中多条时按优先级执行

## 运维

- **热重载配置**：`kill -HUP <pid>`（重新读取 ai_agents.yaml / keyword_rules.yaml）
- **日志**：默认输出到 `./logs/bot.log`（5MB 滚动 × 5）
- **统计查询**：直接查询 `data/messages.db`，或调用 `Database.count_by_day/chat/user`

## 后续待完善（由其他 Agent 跟进）

- `ARCHITECTURE.md` —— 架构文档
- `FEISHU_RESEARCH.md` —— 飞书方案调研（决定 webhook 或 App API）
- 飞书 App API 方式（`FeishuNotifier._get_tenant_access_token` / `send_via_api` 已预留）
