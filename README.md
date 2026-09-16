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

### 多 bot 一键启动（10 个监听机器人）

`config/tg_bots.yaml` 存放各监听 bot 的 token（由 `scripts/auto_read_tokens.py` 从 BotFather 自动读取），执行：

```bash
python scripts/start_all_bots.py                # 启动全部
python scripts/start_all_bots.py --bot tgmon_01_bot   # 只启动指定一个
python scripts/start_all_bots.py --dry-run      # 预览启动计划
```

每个 bot 独立子进程运行，共享 `settings.yaml`，日志按 bot 名分开（`logs/tgmon_01_bot.log`）。`Ctrl+C` 优雅终止全部。

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

---

## 监听面板（listener_panel）

独立 Web 面板，管理关键词监听 + 进群邀请动作链，通过 `start.py` 一键启动。

### 功能

- **关键词管理**：添加 / 启用 / 停用 / 删除监听关键词
- **进群邀请**：配置目标群链接，命中后私信发送者自动进群
- **命中记录**：实时查看命中日志，含私信 / 通知 / 报备状态
- **多群监听**：同时监听极搜群 + 多个互动群
- **动作链**：私信邀请 → 通知主管 @zhuguan_bot → 报备 @wyyu39433

### 启动

```bash
cd /Volumes/PortableSSD/源码/任务3
source .venv/bin/activate
python start.py              # 前台运行
# 或
nohup python start.py > logs/listener_panel.log 2>&1 &   # 后台运行
```

面板地址：**http://127.0.0.1:8790**

### 使用说明

📄 **飞书文档**：[TG 监听面板使用说明](https://jcn16nd0x6pf.feishu.cn/docx/VNi5d0g6Yo03x0xAuFrcC2wbndb)

### 监听群列表

| 群 ID | 群名 |
|---|---|
| -1003744936498 | 极搜群 @jisou88868 |
| 1358827239 | 赚钱项目交流社区 |
| 2213630238 | 华人出海赚钱项目交流群 |
| 2169942996 | 赚钱兼职副业项目交流群 |
| 2038739886 | 泰国华人圈 |
| 3808996051 | 李逍遥的朋友圈 |
| 3789674164 | 云浮肇庆江门狼队 |

### 目录结构

```
listener_panel/
├── __init__.py       # MONITOR_RUNNING 标记
├── app.py            # Flask Web 应用 + REST API
├── db.py             # SQLite 数据库（同步 WAL）
├── monitor.py        # Telethon 监听器 + 动作链
├── config.yaml       # 配置文件
└── templates/
    └── index.html    # Web 面板前端
```
