# TG 监控机器人 × 飞书控制面板 — 集成方案研究报告

> 研究日期：2026-09-15
> 场景：独立 TG 监控机器人（Python，运行在本地 Mac）接入飞书，实现"飞书控制面板"（推送告警 + 接收控制指令 + 状态展示）。
> 本文所有结论基于飞书开放平台官方文档（open.feishu.cn）与本机 lark-cli 实测能力。

---

## 0. 用户现有飞书资产盘点

| 资产 | 值 | 用途 |
|---|---|---|
| 推送应用 App ID | `cli_aab6605cc83a9cdb` | 群消息推送（.env 里已在用） |
| 目标群 Chat ID | `oc_95a98096be3312d91b317685b2cb4de5` | 推送目标群 |
| 用户 open_id | `ou_089898ef11e1e42ca7e20911671b5455` | 用户身份 |
| 机器人 open_id | `ou_08bcbaef0ccf4346e70bf5aa4a031a29` | 被监听的机器人 |
| lark-cli 应用 | `cli_aa0f68eaa178dbc6` | 私聊监听（已有 `im.message.p2p_msg:get_as_user`、`im.message:readonly`、`im.message.send_as_user` 等权限） |

**现状**：`phone-monitor/` 已有两套基础能力——
- `feishu.sh`：App 凭证换 `tenant_access_token` → POST `im/v1/messages` 发富文本（post）到群，走的是**方案 B 的应用机器人推送**，不是 webhook。
- `feishu-listen.sh`：每 15 秒用 `lark-cli im +chat-messages-list` 轮询私聊 → 写入 `inbox.md` → Stop hook 注入 Claude 上下文。走的是**用户身份读取私聊**（不是事件订阅）。

这意味着：方案 B 的"应用机器人推送"已经打通且线上在用；缺的是**实时事件（长连接）**与**交互卡片**。

---

## 1. 方案 A：飞书群机器人 Webhook 推送

### 1.1 能力与限制

自定义机器人（群聊内添加，无需开发应用）默认提供 webhook 地址：

```
POST https://open.feishu.cn/open-apis/bot/v2/hook/<token>
```

| 能力 | 说明 |
|---|---|
| 消息类型 | 文本 `text`、富文本 `post`、图片 `image`、群名片 `share_chat`、交互卡片 |
| 文本/JSON 长度 | 建议 JSON ≤ 30 KB，序列化后 PB ≤ 100 KB |
| 频率限制 | **单租户单机器人 100 次/分钟、5 次/秒**（比应用机器人严） |
| 安全设置 | 自定义关键词、IP 白名单、签名校验（HmacSHA256） |
| 能否接收消息 | ❌ 不能，纯单向推送 |
| 能否获取用户/租户信息 | ❌ 不能 |
| 消息 @ 人 | 仅 `at` 文本标签，不能解析 @（无 open_id 上下文） |

安全签名：`timestamp + "\n" + secret` 经 HmacSHA256 计算空串签名，Base64 后随请求带上 `timestamp`、`sign` 字段（timestamp 限 1 小时内）。V1 旧版 webhook（`/bot/hook/<token>` 无 `v2`）仅支持纯文本，**必须用 V2**。

### 1.2 核心 API

无鉴权换取环节，直接 POST。

### 1.3 代码示例

```python
import time, base64, hashlib, hmac
import requests

WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/<你的token>"
SECRET = "签名密钥"  # 可选；未开启签名校验可省略

def gen_sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode()

def send_text(text: str):
    payload = {"msg_type": "text", "content": {"text": text}}
    ts = int(time.time())
    if SECRET:
        payload["timestamp"] = str(ts)
        payload["sign"] = gen_sign(ts, SECRET)
    resp = requests.post(WEBHOOK, json=payload, timeout=5)
    return resp.json()  # {"code":0} 成功

def send_interactive_card(card: dict):
    payload = {"msg_type": "interactive", "card": card}
    resp = requests.post(WEBHOOK, json=payload, timeout=5)
    return resp.json()
```

### 1.4 评分

| 维度 | 评分 | 说明 |
|---|---|---|
| 技术可行性 | 4/5 | 推送稳定可靠，但纯单向 |
| 实现复杂度 | 1/5 | 一个 curl/POST 就完事 |
| 用户体验 | 2/5 | 看得到推送，但无法回控 |
| 维护成本 | 1/5 | 无服务端、无鉴权，基本零维护 |

### 1.5 已知限制和坑

1. **不能做"控制面板"**——只能发不能收，用户的"回控 TG Bot"诉求完全无法满足。
2. 100 次/分钟限流，TG 监控高频场景（多条频道刷屏）容易触发 `11232` 限流错误；官方建议避开整点/半点推送。
3. 无 app 概念，卡片交互按钮回调无法绑定应用身份，卡片交互回调对自定义机器人的支持受限。
4. webhook 地址泄露即被滥用，虽可加 IP 白名单（本地 Mac 出口 IP 可能动态变化）。

---

## 2. 方案 B：飞书自建应用（完整 Bot）—— ⭐ 核心方案

### 2.1 能力全景

自建应用（用户已有 `cli_aab6605cc83a9cdb`）开启"机器人能力"后获得完整能力：

| 能力 | 说明 |
|---|---|
| 发送消息 | `POST /open-apis/im/v1/messages`，支持 text/post/interactive卡片/image/file/audio/video/sticker/名片 |
| 回复/编辑/撤回 | 回复 `receive`、编辑 PATCH、撤回 DELETE |
| **交互卡片** | 可点击按钮、表单（input/select/date/checkbox）、下拉选择、人员选择；点击回调 `card.action.trigger` |
| **双向通信** | 订阅 `im.message.receive_v1` 事件 = 用户发给机器人消息即回传（私聊/群 @ 均可） |
| **长连接事件** | WebSocket 长连接（SDK/lark-cli），**无需公网 IP**，本地 Mac 直接可用 |
| 私聊发送 | 用户 open_id 直发，无需用户在线 |
| 语义化权限 | 细粒度 scope（读单聊、发消息、读群消息、@ 消息…） |

### 2.2 双向通信架构（控制面板核心链路）

```
┌─────────────┐  ① 推送 (POST im/v1/messages)   ┌─────────────┐
│             │ ───────────────────────────────▶ │             │
│  TG 监控Bot  │                                │  飞书客户端    │
│ (本地Python) │ ◀─────────────────────────────── │  (用户)      │
│             │  ② 用户指令 (WebSocket 长连接事件) │             │
└─────┬───────┘                                └─────────────┘
      │  ③ 卡片点击回调 card.action.trigger（长连接接收）
      └──── 处理指令 → 调 TG Bot HTTP 接口 / 改配置 → 更新卡片或回消息
```

关键点：**收发都是同一条 WebSocket 长连接**（`lark-cli event consume` 或官方 `lark.ws.Client`），本地 Mac 无需内网穿透、无需固定公网 IP。

### 2.3 核心 API 端点

| 操作 | 端点 | 频率限制 | 所需权限（任一） |
|---|---|---|---|
| 发送消息 | `POST /open-apis/im/v1/messages?receive_id_type=chat_id\|open_id` | 1000 次/分钟、50 次/秒；同用户/同群 5 QPS | `im:message` / `im:message:send_as_bot` / `im:message:send` |
| 回复消息 | `POST /open-apis/im/v1/messages/:message_id/reply` | 同上 | 同上 |
| 更新已发卡片 | `PATCH /open-apis/im/v1/messages/:message_id`（14 天内） | 1000 次/分钟 | `im:message` / `im:message:send_as_bot` / `im:message:update` |
| 延时更新卡片 | `POST /open-apis/interactive/v1/card/update`（回调 token，30 分钟有效、最多 2 次） | — | — |
| 获取历史消息 | `GET /open-apis/im/v1/messages?container_id_type=chat&container_id=:id` | 1000 次/分钟 | `im:message:readonly` / `im:message` |
| 发送批量消息 | `POST /open-apis/im/v1/batch_messages` | 异步，50 万/天 | `im:message:send_as_bot` |
| 上传图片/文件 | `POST /open-apis/im/v1/images` / `files` | 1000 次/分钟 | `im:resource` |
| 获取机器人信息 | `GET /open-apis/bot/v3/info` | — | 无需权限 |
| 查群列表 | `GET /open-apis/im/v1/chats` | 50 次/秒 | `im:chat:readonly` 等 |

鉴权：`POST /open-apis/auth/v3/tenant_access_token/internal`（app_id + app_secret → token，2 小时有效，可缓存）。

### 2.4 事件订阅（长连接 vs Webhook）

| 维度 | 长连接（WebSocket） | Webhook 推送 |
|---|---|---|
| 是否需公网 | ❌ 不需要（本地 Mac 直接连） | ✅ 需要公网 URL/内网穿透 |
| 鉴权/解密 | SDK 内置 | 需自己验签 + AES 解密 |
| 延迟 | 实时 | 实时 |
| 适用 | 企业自建应用（我们就是） | 有服务器的场景 |
| 连接数 | 每应用最多 50 个 client；推送给随机一个 | — |

**结论：本地 Mac 场景无脑选长连接。**

长连接 Python（官方 SDK）：

```python
import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1, P2ImMessageReceiveV1Data
import json

def on_message(data: P2ImMessageReceiveV1Data) -> None:
    msg = data.event.message
    print("收到消息:", msg.message_type, msg.content)
    # msg.content 是 JSON 字符串，text 类型: {"text":"..."}
    # 拿到后回调用发送消息 API 回复，或写入自己的处理队列

cli = lark.ws.Client(
    "cli_aab6605cc83a9cdb", "<APP_SECRET>",
    event_handler={"p2_im_message_receive_v1": on_message},
)
cli.start()  # 阻塞；连接成功打印 "connected to wss://..."
```

如果不想引入 `lark_oapi`，本机 lark-cli 已内置长连接消费（**实测可用**）：

```bash
# 一次性采集：后台常驻，NDJSON 逐条输出
lark-cli event consume im.message.receive_v1 --as bot --timeout 0
# 卡片点击回调
lark-cli event consume card.action.trigger --as bot --timeout 0
```

可用事件（`lark-cli event list` 实测）：`im.message.receive_v1`（收消息）、`im.message.message_read_v1`（已读）、`im.message.reaction.created_v1`（表情）、`im.chat.member.bot.added_v1`（进群）、`card.action.trigger`（卡片交互）、`approval.instance.status_changed_v4` 等。

### 2.5 交互卡片（控制面板 UI）

卡片 JSON 2.0 结构：`config` + `header`（标题）+ `body.elements`（组件数组）。

发送示例（`msg_type=interactive`，`content` 为卡片 JSON 的序列化字符串）：

```python
import requests

def get_token(app_id: str, app_secret: str) -> str:
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret}, timeout=5)
    return r.json()["tenant_access_token"]

# 控制面板卡片：状态 + 按钮 + 表单
card = {
    "schema": "2.0",
    "config": {"update_multi": True, "style": {}},
    "header": {
        "title": {"tag": "plain_text", "content": "🎛 TG 监控控制面板"},
        "template": "blue",
    },
    "body": {
        "elements": [
            {"tag": "markdown", "content": "**状态**：🟢 运行中\n**频道**：@alert_channel\n**延迟**：1.2s"},
            {
                "tag": "action",
                "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "⏸ 暂停"},
                     "type": "danger", "value": {"action": "pause"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "▶️ 恢复"},
                     "type": "primary", "value": {"action": "resume"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "📊 刷新"},
                     "type": "default", "value": {"action": "refresh"}},
                ],
            },
            {
                "tag": "form",  # 表单：提交时回调带 form_value
                "elements": [
                    {"tag": "input", "name": "threshold",
                     "label": {"tag": "plain_text", "content": "告警阈值"},
                     "placeholder": {"tag": "plain_text", "content": "输入阈值，如 100"}},
                    {"tag": "select_static", "name": "level",
                     "label": {"tag": "plain_text", "content": "告警级别"},
                     "options": [{"text": {"tag": "plain_text", "content": "低"}, "value": "low"},
                                 {"text": {"tag": "plain_text", "content": "高"}, "value": "high"}]},
                ],
                "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "保存配置"},
                     "type": "primary", "value": {"action": "save_config"}},
                ],
            },
        ]
    },
}

resp = requests.post(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
    headers={"Authorization": f"Bearer {get_token(...)}"},
    json={"receive_id": "oc_95a98096be3312d91b317685b2cb4de5",
          "msg_type": "interactive",
          "content": json.dumps(card, ensure_ascii=False)},
)
```

**卡片交互回调**（`card.action.trigger`，长连接消费）：

```json
{
  "type": "card.action.trigger",
  "event_id": "...", "timestamp": "1726...",
  "operator_id": "ou_089898...",
  "message_id": "om_...", "chat_id": "oc_...",
  "token": "延迟更新token(30分钟/最多2次)",
  "action_tag": "button",
  "action_value": "{\"action\":\"pause\"}",   // 开发者自定义 value
  "action_name": "组件name",
  "form_value": "{\"threshold\":\"150\",\"level\":\"high\"}",  // 表单提交才有
}
```

处理按钮 → 更新卡片（两种方式任选）：

```python
# 方式一：延时更新（回调 token，无需 message_id，但 30 分钟/2 次限制）
requests.post("https://open.feishu.cn/open-apis/interactive/v1/card/update",
              headers={"Authorization": f"Bearer {token}"},
              json={"token": "回调里的token", "card": 新的完整卡片JSON})

# 方式二：PATCH message_id（14 天内，适合随时刷新状态）
requests.patch(f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
               headers={"Authorization": f"Bearer {token}"},
               json={"content": jsondumps(新卡片), "msg_type": "interactive"})
```

**注意**：卡片 1.0 结构中更新时必须带 `open_ids`（操作人）否则报 300090；卡片 JSON 2.0 不需要。

### 2.6 权限配置清单（开发者后台 + 发布版本）

- 开启**机器人能力**并发布版本（否则搜不到/无法用）。
- 权限（在开放平台"权限管理"申请，管理员审核）：
  - `im:message:send_as_bot`（应用身份发消息）
  - `im:message.p2p_msg:readonly`（收单聊消息）→ 控制指令核心
  - `im:message.group_at_msg:readonly`（收群 @ 消息）→ 群里 @ 机器人回控
  - `im:message:readonly`（读历史消息）
  - `contact:user.base:readonly`（查用户基本信息）
- 事件订阅：添加 `im.message.receive_v1`、`card.action.trigger`（回调配置开启，走长连接）。
- lark-cli 的 `cli_aa0f68...` 用户身份已具备大部分读取/发送权限（见 auth status scope）。若让它接管，建议给它加 `im:message.send_as_bot`。

### 2.7 评分

| 维度 | 评分 | 说明 |
|---|---|---|
| 技术可行性 | 5/5 | 官方一等公民能力，收发+卡片全支持 |
| 实现复杂度 | 3/5 | 事件长连接 + 卡片构建有学习曲线，但 lark-cli 可大幅缩减 |
| 用户体验 | 5/5 | 完整控制面板：按钮即点即用、表单配置、实时状态卡片 |
| 维护成本 | 2/5 | 需维护应用权限/事件订阅，但无服务器运维 |
| **综合** | **4.5/5** | **唯一能真正实现"控制面板"的方案** |

### 2.8 已知限制和坑

1. **5 QPS / 同群限制**：监控频道高频推送可能触发限流，需做简单合并/节流。
2. 卡片 JSON ≤ 30 KB，别把长日志塞卡片里。
3. 长连接事件需**回调配置**开启否则收不到（在开发者后台"事件与回调"打开，无预检）。
4. 卡片 1.0 更新必须带 `open_ids`；建议统一用 JSON 2.0。
5. 事件处理需 3 秒内返回（SDK 自动 ack `{"code":200}`；长耗时逻辑建议异步队列）。
6. 机器人需在群内且有发言权限；给用户私聊需用户可用范围内。
7. lark-cli 的 `cli_aa0f68...` 与推送的 `cli_aab660...` 是两个应用，open_id 各应用不同，混用时要换算。

---

## 3. 方案 C：飞书多维表格（Base）作为 Dashboard

### 3.1 能力

| 能力 | 说明 |
|---|---|
| 实时写记录 | `POST /open-apis/bitable/v1/apps/:app_token/tables/:table_id/records`，50 次/秒 |
| 视图/统计/仪表盘 | 多维表格原生能力（分组、汇总、图表、自动化） |
| 表单收集 | 数据表挂表单，用户填表 = 新记录（可做"填表发指令"） |
| 读取记录 | `GET .../records`，可过滤（filter）→ TG Bot 轮询读取配置 |
| 修改监控状态 | 在表格里改"开关/参数"字段 → bot 轮询 diff |
| 权限 | `base:record:create` / `bitable:app`；高级权限需对应用授权 |

### 3.2 架构设想

- TG 消息实时写入 Base（长连接事件 → 写记录）→ Base 视图/图表自动聚合，飞书端直接看面板。
- 用户在 Base 里改配置字段（如"目标频道"、"阈值"、"启用"开关）→ bot 每隔 N 秒读记录 → 应用配置。
- 用户可在表格内发起"指令"（新增一条记录，类型字段=指令）→ bot 轮询消费。

### 3.3 代码示例

```python
def add_records(token, app_token, table_id, records):
    """records: [{"fields": {...}}]"""
    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        headers={"Authorization": f"Bearer {token}"},
        json={"records": records},
    )
    return r.json()

# 例：写入一条 TG 告警
add_records(token, "bascn_xxx", "tbl_xxx", [{
    "fields": {
        "时间": 1726041600000,          # 日期字段单位 ms
        "频道": "@alert_channel",
        "消息": "疑似钓鱼链接…",
        "级别": "高",                    # 单选
        "已处理": False,                 # 复选框
    }
}])

# 读配置记录（filter 只取"配置"类型）
r = requests.get(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    "?page_size=100&filter=..."
    , headers={"Authorization": f"Bearer {token}"})
```

### 3.4 评分

| 维度 | 评分 | 说明 |
|---|---|---|
| 技术可行性 | 4/5 | 读写都稳定，dashboard 靠 Base 原生能力 |
| 实现复杂度 | 2/5 | 建表 + 3 个 API |
| 用户体验 | 3/5 | 看板漂亮，但"回控"要 bot 轮询，非实时、无按钮 |
| 维护成本 | 2/5 | 表结构/字段类型变更需谨慎；高级权限要配 |
| **综合** | **3/5** | **适合做数据归档/展示层，不适合做交互控制层** |

### 3.5 已知限制和坑

1. **不是实时控制**：配置变更依赖 bot 轮询间隔（秒级～分钟级），无法即时反馈。
2. 高级权限（advanced permissions）需在多维表格"添加文档应用"中给应用授权，否则 403 (1254302/1254304)。
3. 日期字段传毫秒时间戳；附件字段需先 `drive/v1/medias/upload_all` 上传。
4. 字段类型不可轻易变更，v1 字段删除重建会丢数据。
5. 表格内操作过多会被自动化/公式重算拖慢。

---

## 4. 方案 D：飞书审批流控制敏感操作

### 4.1 能力

用飞书审批做"二次确认"：TG Bot 触发敏感操作（如封禁用户、删除频道、抹数据）→ 创建审批实例 → 人工在飞书审批中心点同意/拒绝 → 审批任务事件回调 → bot 执行/中止。

| 能力 | 说明 |
|---|---|
| 创建审批实例 | `POST /open-apis/approval/v4/instances`，100 次/分钟 |
| 审批定义 | 需先 `POST /open-apis/approval/v4/approvals` 创建（唯一 Approval Code），定义表单控件+流程节点 |
| 三方审批 | `external_approval` + `external_instance`，外部系统把审批同步进来（免建复杂原生流程） |
| 审批事件 | `approval.instance.status_changed_v4` / `approval.task.status_changed_v4`（长连接可订阅） |
| 审批动作 | 同意 `tasks/approve`、拒绝 `tasks/reject`、转交、加签 |
| 发送审批 Bot 消息 | `POST /open-apis/approval/v4/messages` |
| 权限 | `approval:approval` / `approval:instance`（lark-cli 用户身份已有 `approval:instance:write`/`approval:instance:read`） |

### 4.2 架构设想

```
TG Bot 检测到敏感动作（如解封/删数据）
  → POST /approval/v4/instances 创建审批（审批人=用户open_id）
  → 飞书审批中心出现待办，用户点同意/拒绝
  → 长连接订阅 approval.instance.status_changed_v4 收到结果
  → 同意则下发执行，拒绝则记录
```

原生审批需要对每个场景设计"审批定义"（表单+流程），较重；简单化可用**三方审批**：先注册 external_approval 定义，再逐次 `external_instance/create` 同步实例，飞书审批中心即显示外部审批单，操作结果通过回调/事件回传。

### 4.3 代码示例（原生审批精简）

```python
# 1) 创建审批定义（一次，得到 approval_code，含表单控件定义）
r = requests.post("https://open.feishu.cn/open-apis/approval/v4/approvals",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "approval_name": "TG 敏感操作确认",
        "approval_code": "tg_sensitive_op",
        "form": {
            "form_controls": [{
                "name": "操作说明",
                "id": "desc",
                "type": "text",
                "required": True,
                "custom_id": "desc_id",
            }],
        },
        "node_list": [{
            "name": "审批节点1",
            "approver": [{"type": "user", "user_id_list": ["ou_089898ef11e1e42ca7e20911671b5455"], "user_id_type": "open_id"}],
        }],
        "visibility": {"visible_list": [{"type": "everyone"}]},
    })
approval_code = r.json()["data"]["approval_code"]

# 2) 创建审批实例（每次操作）
r = requests.post("https://open.feishu.cn/open-apis/approval/v4/instances",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "approval_code": approval_code,
        "open_id": "ou_089898ef11e1e42ca7e20911671b5455",  # 发起人
        "form": {"form_list": [{"id": "desc", "value": "解封用户 @xxx"}]},
        "node_approver_open_id_list": [["ou_089898ef11e1e42ca7e20911671b5455"]],
    })

# 3) 长连接订阅审批结果
# lark-cli event consume approval.instance.status_changed_v4 --as user
# 事件里 instance_code + status: PENDING/APPROVED/REJECTED/CANCELED
```

### 4.4 评分

| 维度 | 评分 | 说明 |
|---|---|---|
| 技术可行性 | 4/5 | 官方审批中心，可靠 |
| 实现复杂度 | 5/5 | 审批定义结构复杂（表单控件/节点/可见范围），是四方案最重的 |
| 用户体验 | 4/5 | 审批中心体验好，有移动端 |
| 维护成本 | 4/5 | 每种操作一个审批定义，改流程要重新发布 |
| **综合** | **2.5/5** | **只适合"敏感操作二次确认"，不适合做常规控制面板** |

### 4.5 已知限制和坑

1. 审批定义创建后**不可删除/停用**，设计要谨慎。
2. 审批表单控件参数体系复杂（`i18n`、控件类型、外部数据源），调通成本高。
3. 三方审批回调超时 10 秒，需快速响应。
4. 事件需**订阅**审批定义后的实例才推（approval subscription）。
5. 对单人场景（只有用户自己审批自己）有点杀鸡用牛刀；卡片按钮"确认弹窗"其实够用。

---

## 5. 方案横向对比

| | A Webhook 推送 | B 自建应用 Bot | C 多维表格 | D 审批流 |
|---|---|---|---|---|
| 推送告警 | ✅ 简单 | ✅ 完整 | ✅ | — |
| 接收指令回控 | ❌ | ✅ 实时双向 | ⚠️ 轮询 | ⚠️ 仅审批动作 |
| 交互式 UI（按钮/表单） | 部分 | ✅ 原生卡片 | ❌ | 审批表单 |
| 实时状态刷新 | ❌ | ✅ 卡片更新 | ⚠️ 秒级轮询 | — |
| 需要公网 | 否 | 否(长连接) | 否 | 否(长连接) |
| 实现成本 | 极低 | 中 | 低 | 极高 |
| 数据沉淀/统计 | 无 | 一般 | ✅ 强 | 有审批记录 |
| 适合定位 | 应急推送兜底 | **控制面板主通道** | **数据看板+归档** | **敏感操作门禁** |

---

## 6. 综合推荐

### 6.1 主方案：方案 B（自建应用 Bot）——完整控制面板

理由：
1. 用户**已有**推送应用（`cli_aab6605cc83a9cdb`）且线上在用，只需补两块：**长连接事件接收** + **交互卡片**。
2. 本地 Mac 无公网 IP 也不影响——长连接 WebSocket 让"收指令"完全落地。
3. 交互卡片天然是控制面板：状态卡片 + 按钮（暂停/恢复/刷新）+ 表单（改阈值/频道）即点即用，无需打开表格。

**建议两步走**（可以跟现有 `phone-monitor` 无缝衔接）：
- **第一步（P0，1 天内）**：用 lark-cli 长连接替代 15 秒轮询——`lark-cli event consume im.message.receive_v1 --as bot` 常驻，收到即解析写入 inbox（替换 `feishu-listen.sh` 的轮询），保留现有 Stop hook 注入链路。改造最小，收益立竿见影（实时、省资源、不再漏消息）。
- **第二步（P1）**：给推送脚本升级为交互卡片控制面板——发一张带按钮/表单的 interactive 卡片到群里，用 `card.action.trigger` 消费点击 → 调 TG Bot 控制 API → PATCH 卡片刷新状态。

### 6.2 增强组合（推荐叠加）

| 层 | 方案 | 用途 |
|---|---|---|
| 控制交互层 | **B 应用机器人 + 交互卡片** | 按钮/表单/状态刷新（主） |
| 数据展示层 | **C 多维表格** | TG 消息全量归档 + 分组统计/仪表盘（辅助，按需） |
| 敏感操作门禁 | **D 审批流** | 仅"封禁/删除/清库"类高危动作走审批二次确认（可选） |
| 应急兜底 | **A Webhook** | 原生应用机器人挂掉时的手动/脚本应急推送（可选，因成本极低） |

优先级：**B → C → D → A**。A 已在 .env 预留，D 仅在真的需要"人审"时再上。

### 6.3 落地关键清单

- [ ] 开发者后台给 `cli_aab6605cc83a9cdb` 开启机器人能力并**发布版本**
- [ ] 申请权限：`im:message:send_as_bot`、`im:message.p2p_msg:readonly`、`im:message.group_at_msg:readonly`、`im:message:readonly`
- [ ] 事件订阅添加 `im.message.receive_v1` + `card.action.trigger`，回调配置改为长连接
- [ ] 本地：`lark-cli event consume im.message.receive_v1 --as bot --timeout 0` 常驻（systemd/launchd/nohup）
- [ ] 推送脚本升级：`msg_type=interactive` 卡片；`card.action.trigger` 消费 → `PATCH /im/v1/messages/:id` 刷新
- [ ] 配置管理：把"暂停/恢复/阈值/频道"落到本地 config 文件，卡片读它、改它

### 6.4 风险与缓解

- **5 QPS 限流**：TG 监控高频刷屏 → 推送合并/去重/节流（按频道按批次聚合）。
- **卡片 30 KB 限制**：正文精简，长日志走 Base 或文件。
- **两个应用 open_id 不通用**：跨应用使用用户身份时调用 `/open-apis/contact/v3/users/<id>/convert_id` 或统一用 `cli_aa0f68...` 的 user 身份收 + `cli_aab660...` 的 bot 身份发（各自顾各自）。

---

## 附录：关键官方文档入口

- 发送消息 API：`https://open.feishu.cn/document/server-docs/im-v1/message/create`
- 接收消息事件：`https://open.feishu.cn/document/server-docs/im-v1/message/events/receive`
- 长连接接收事件：`https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case`
- 卡片回调：`https://open.feishu.cn/document/feishu-cards/card-callback-communication`
- 卡片 JSON 2.0 结构：`https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure`
- 更新消息卡片：`https://open.feishu.cn/document/server-docs/im-v1/message-card/patch`
- 自定义机器人：`https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN`
- 多维表格新增记录：`https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/bitable-v1/app-table-record/create`
- 审批创建实例：`https://open.feishu.cn/document/server-docs/approval-v4/instance/create`
- 审批概述：`https://open.feishu.cn/document/server-docs/approval-v4/approval-overview`