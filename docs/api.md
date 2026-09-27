# API 参考

## 地址与响应包

通过 Compose 访问后端时，基地址是：

```text
http://localhost:8080/api/v1
```

后端容器内部监听 `8000`，路由仍以 `/api/v1` 开头。行情数据服务监听 `8001`，路由不带 `/api/v1`，且 Compose 不对宿主机发布该端口。

后端成功响应通常为：

```json
{
  "success": true,
  "data": {}
}
```

后端错误响应为：

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Request failed."
  }
}
```

行情数据中枢也返回 `success`、`data` 和 `error`。一般请求错误时 `data` 为 `null`；健康检查降级和部分同步失败会保留 `data`，分别返回依赖状态或逐项结果。后端请求校验错误还会带 `error.details`，其中只包含字段位置、错误类型和消息。

## 当前 API 边界

- 浏览器只访问 Nginx 暴露的 `/api/v1` 与 Next.js 页面；不会直接访问 `market-data-hub:8001`。
- 证券/行情/指标/状态读取是共享公共数据；Watch、Settings、Alert、Notification 都要求当前用户认证并做 ownership 检查。
- Hub 的同步与日历校验端点使用内部 token；读取端点依赖 Compose 私有网络隔离。
- HTTP API 不提供“立即计算指标”的写接口；计算由后台 bootstrap/EOD 负责。

## 网关与健康检查

| 方法 | 路径 | 说明 | 状态 |
| --- | --- | --- | --- |
| `GET` | `/health` | Nginx 直接返回 `ok` 文本 | `200` |
| `GET` | `/api/health` | Next.js 前端健康信息，包含 `status`、`service`、`timestamp` | `200` |
| `GET` | `/api/v1/health` | 后端探测 PostgreSQL 和 Redis | 健康 `200`，依赖异常 `503` |

后端健康数据包含 `service`、`version`、`status` 和 `dependencies`。每个依赖项包含 `healthy/unhealthy`、可选 `latency_ms` 和安全的 `detail`。

## 认证 API

### `POST /auth/invitations`

管理员创建一次邀请。

- 认证：`Authorization: Bearer <admin_access_token>`。
- 请求：`{"email":"user@example.com"}`。
- `email` 会去除首尾空白并按大小写不敏感方式保存，长度范围为 3–320。
- 成功：`201`，`data` 包含 `id`、`email`、`token`、`status` 和 `expires_at`。
- 原始 `token` 只在创建响应中返回；数据库只保存其哈希。
- 非管理员返回 `403`；未认证返回 `401`。

### `POST /auth/register`

使用邀请创建激活用户。

```json
{
  "invite_token": "<token>",
  "username": "new-user",
  "password": "a-password-at-least-8"
}
```

`username` 长度为 3–64，`password` 长度为 8–256；`email` 可选，若提供必须与邀请邮箱匹配。成功返回 `201` 和安全的用户投影：`id`、`username`、`email`、`role`、`status`。该接口不发放 token；前端注册成功后再执行登录。

常见错误：无效/过期/已消费邀请为 `400`，用户名或邮箱重复为 `409`，请求校验失败为 `422`。

### `POST /auth/login`

使用用户名或邮箱登录：

```json
{"username":"admin","password":"<password>"}
```

成功返回 `200` 和 `data.access_token`、`data.token_type`（固定为 `bearer`）及 `data.user`。同时设置刷新 Cookie。无效凭据返回 `401`；密码哈希不会出现在响应中。

### `POST /auth/refresh`

浏览器携带刷新 Cookie 调用，不需要把 refresh token 放入 JSON 或 Authorization header。默认 Cookie 名称是 `refresh_token`，路径是 `/api/v1/auth`。

成功返回新的 access token 并轮换 Cookie。旧 refresh token 被标记为已使用；检测到重放时会撤销整个 token family，并返回 `401`。

### `POST /auth/logout`

浏览器携带刷新 Cookie 调用。服务端撤销对应 token family，删除 Cookie，并返回：

```json
{"success":true,"data":{"logged_out":true}}
```

缺少或未知 Cookie 也按幂等退出处理。

### `GET /auth/me`

需要 `Authorization: Bearer <access_token>`，返回当前数据库中的激活用户。每次请求都会重新读取用户状态和角色；禁用用户不会因为 JWT 尚未过期而继续访问。

## 证券与行情 API

证券基础读取接口不要求用户登录，读取的是共享市场数据：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/securities/search?q=...` | 按代码/名称搜索已同步证券 |
| `GET` | `/securities/{symbol}` | 读取证券元数据 |
| `GET` | `/securities/{symbol}/quote` | 读取最新持久化 Quote |
| `GET` | `/securities/{symbol}/daily-bars` | 读取日线；支持 `start_date`、`end_date`、`adjust_type` |
| `GET` | `/securities/{symbol}/dividend-yield` | 返回 TTM 现金股息率及计算输入 |

`daily-bars` 的 `adjust_type` 可使用 `qfq` 或 `none`。Dividend Yield 使用过去 12 个月已落库的每股现金分红总额除以当前价格；优先使用最新 Quote，缺少 Quote 时由后端使用可用的未复权收盘价 fallback，并在响应中返回 `price_source` 与 `as_of`。

## Watch Table API

以下接口都要求当前用户 Bearer access token；所有 ownership 检查在后端完成，不接受客户端提交 `user_id`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` / `POST` | `/watch-tables` | 列出/创建监控表 |
| `GET` / `DELETE` | `/watch-tables/{table_id}` | 读取聚合详情/删除监控表 |
| `POST` / `DELETE` | `/watch-tables/{table_id}/stocks` / `.../stocks/{security_id}` | 添加/移除股票 |
| `PUT` | `/watch-tables/{table_id}/stocks/reorder` | 持久化股票顺序 |
| `GET` / `POST` | `/watch-tables/{table_id}/columns` | 列出/创建动态列 |
| `PUT` | `/watch-tables/{table_id}/columns/reorder` | 持久化动态列顺序 |
| `PUT` / `PATCH` / `DELETE` | `/columns/{column_id}` | 修改/删除动态列 |

新增股票会 best-effort enqueue `bootstrap_security_data`，无需等待未来交易日即可准备历史日线、分红、指标和状态。相同股票可以出现在多个 Watch Table；共享市场/分析数据不会因从某个表移除而删除。

动态指标列支持 `NUMBER`、`DELTA`、`COMPOSITE`、`STATUS`。`NUMBER/DELTA` 对多值指标必须通过 `parameters.field` 明确具体 scalar；`STATUS` 必须提交 `state_code`，并把该状态需要的参数一并持久化，例如：

```json
{
  "column_type": "INDICATOR",
  "indicator_type": "MA",
  "state_code": "MA_CROSS_UP",
  "view_mode": "STATUS",
  "parameters": {"short_period": 20, "long_period": 60}
}
```

Watch Table detail 是表格渲染的聚合读模型：包含证券、最新价格、按 column id 键控的 `column_values` 和当前可见 states。服务端按用户当前 `adjust_type` 设置选择 `qfq` 或 `none` 分析序列。

## Settings API

`GET /settings` 与 `PUT /settings` 需要登录。当前实际消费的设置包括：

- `adjust_type`：`qfq` 或 `none`，决定 Watch/详情默认读取的分析序列；不会修改已保存 AlertRule 的 adjustment。
- `indicator_settings.defaults` / `indicator_settings.parameters`：影响之后新建监控表的默认指标列与参数，不追溯修改已有列。
- `display_settings`：由前端工作台应用密度、状态、变化和 mini-chart 等展示偏好。
- `notification_settings.feishu_webhook`：省略表示 KEEP；字符串表示 REPLACE；显式 `null` 表示 CLEAR。

Settings 响应不会返回 Webhook 明文，只返回 `notification_settings.feishu_webhook_configured`。新写入的 Webhook 存在 `user_secrets` 加密表而不是设置 JSON。

## 指标与状态历史 API

以下接口读取已经由 bootstrap/EOD/rebase 流程物化的分析结果：

- `GET /securities/{symbol}/indicators`
- `GET /securities/{symbol}/indicators/history`
- `GET /securities/{symbol}/states`
- `GET /securities/{symbol}/states/history`

四个接口都使用查询参数 `adjust=qfq|none`（注意参数名是 `adjust`，不是 `adjust_type`），省略时默认 `qfq`。两种 adjustment 使用独立的 snapshot/state identity，不会互相覆盖。GET 接口只读持久化结果，不再隐式触发重算或数据库写入。指标/状态 history 还支持 `start`、`end`；指标 history 支持 `parameter_key`，状态 history 支持 `state_code` 与 `parameter_key`。

指标历史 item 包含 `indicator_type`、`parameter_key`、`parameters`、`adjust_type`、`values`、`previous_values` 和 `delta`。状态历史 item 还包含 `state_code`、`parameter_key`、`parameters`、`adjust_type` 与 metadata。状态 history 支持按 `state_code` / `parameter_key` 精确筛选。

## 告警通知 API

### `/alerts` 提醒规则

接口为 `GET /alerts`、`POST /alerts`、`PUT /alerts/{alert_id}`、`DELETE /alerts/{alert_id}`，均要求当前用户认证。提醒规则支持 `STATE` 与 `VALUE` 两种 condition。所有规则固定记录创建时的 adjustment/参数 identity；修改用户默认设置不会改变已有规则语义。

VALUE 规则必须最终解析到一个 scalar field，例如：

```json
{
  "security_id": 1,
  "condition_type": "VALUE",
  "indicator": "MACD",
  "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
  "field": "histogram",
  "adjust_type": "qfq",
  "operator": ">=",
  "threshold": 1.5
}
```

RSI / Projected MA 等单值指标可省略 `field`，服务端会规范化为 `value`；MA、KDJ、BOLL、MACD 等多值指标必须明确具体 field。`DIVIDEND_YIELD` 固定为 `field=value` 且 `adjust_type=none`。

STATE 规则提交 `state_id`/`state_code` 与可选参数；MA 状态可指定 `short_period` / `long_period`。服务端会规范化参数并自行生成稳定 `parameter_key`，客户端不能把 hash 当作可信输入，因此不同周期组合可以并存。Alert response 会返回规范化后的 `parameters`、`parameter_key`、`field`、`adjust_type` 与 `needs_review`。

Edge Trigger 只在 RESET → ACTIVE 时创建通知事件。状态切回 RESET 后再次进入 ACTIVE 才会重新触发。

### `GET /notifications`

需要当前用户的 Bearer access token，返回该用户自己的通知投递记录。每条记录包含
`id`、`title`、`status`（`PENDING`、`SENT` 或 `FAILED`）、`created_at`，以及可选的
安全诊断字段：

```json
{
  "id": 17,
  "title": "RSI >= 70",
  "status": "FAILED",
  "error_code": "FEISHU_ERROR",
  "error_message": "Notification provider failed.",
  "retryable": true,
  "content": {
    "stock_name": "贵州茅台",
    "stock_code": "600519",
    "indicator": "RSI",
    "current_value": 71,
    "date": "2026-09-24"
  }
}
```

`error_code`、`error_message` 和 `retryable` 可以为 `null`/缺省。错误字段只包含脱敏后的
业务诊断，不返回 Feishu webhook URL、token 或原始 transport 异常。`retryable=true` 表示
可以调用 retry 接口；FAILED 始终可重试，PENDING 只有在安全恢复窗口过期后才可重试。

### `POST /notifications/{notification_id}/retry`

需要当前用户的 Bearer access token。该接口只访问当前用户拥有的通知记录：其他用户的
记录统一返回 `404 NOTIFICATION_NOT_FOUND`，不泄露记录是否存在。

- `SENT`：幂等成功返回原记录，不会再次调用 Feishu。
- `FAILED`：重新投递；成功后变为 `SENT`，失败后保留 `FAILED` 和安全错误字段。
- 过期的 `PENDING`：按遗留/中断投递恢复。服务端使用行锁和并发保护，避免同一通知
  并发产生重复 POST。
- 首次投递在写入 `PENDING` 后也会重新取得同一通知行锁，并持锁完成 provider 请求；因此
  首次投递与遗留恢复请求之间同样不会并发 POST。
- 近期 `PENDING`：返回 `409 NOTIFICATION_RETRY_CONFLICT`，调用方应稍后再试。恢复窗口为
  5 分钟，严格大于 Feishu 5 秒 provider 超时及数据库收敛余量。
- 其他不可重试状态：返回 `409 NOTIFICATION_NOT_RETRYABLE`。

若终态提交遇到不确定的数据库失败，服务端会有限次重新加锁并使用缓存结果恢复；仍无法持久化时
返回 `503 NOTIFICATION_PERSISTENCE_FAILED`，记录保持可诊断的 `PENDING`/原终态。Feishu webhook
未提供幂等键，因此系统不能宣称绝对 exactly-once：进程在 provider 已接受请求、数据库终态尚未
确认的极端崩溃窗口中，后续人工恢复仍可能再次投递。

Feishu HTTP 响应必须是 JSON object，并明确包含数值 `code` 或 `StatusCode`，且严格等于
`0`；缺字段、非法 JSON、非 object、非零业务码都会记录为失败。provider 请求超时固定为
5 秒，错误响应不会回显 webhook 信息。

## 行情数据中枢 API

以下路径由 `market-data-hub` 直接提供，不经过 Nginx，也没有 `/api/v1` 前缀。它们默认只在 Compose 网络内可访问。

### `GET /health`

探测行情数据中枢的 PostgreSQL 和 Redis。全部健康时返回 `200`，任一依赖异常时返回 `503`，数据状态为 `healthy` 或 `degraded`。

### `GET /internal/quotes/{symbol}`

读取已持久化的最新报价快照。支持六位代码或带市场的形式，例如 `600519`、`SH.600519`、`600519.SH`。

成功的 `data` 包含：

```json
{
  "symbol": "600519",
  "price": "1800.00",
  "change": "1.20",
  "change_percent": "0.07",
  "timestamp": "2026-09-24T08:00:00Z"
}
```

实际数值由 Pydantic JSON 序列化为可精确表示的文本/数值形式；无记录为 `404`，符号格式无效为 `400`。

### `GET /internal/daily-bars/{symbol}`

读取已持久化日线：

```text
/internal/daily-bars/600519?start=2026-09-01&end=2026-09-24&adjust=none
```

查询参数：

- `start`、`end` 可选，格式为 `YYYY-MM-DD`，闭区间过滤。
- `adjust` 可选值为 `qfq` 或 `none`，默认是 `qfq`。

`data` 包含规范化的 `symbol`、`start`、`end`、`adjustment` 和 `items`；每个 item 含 `trade_date`、OHLCV、可选 `amount` 和 `adjustment`。没有数据返回 `404`，日期倒置返回 `400`。

### `POST /internal/sync/daily`

同步多个证券的日线。必须带下列任一认证头：

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

或：

```text
Authorization: Bearer <INTERNAL_API_TOKEN>
```

请求支持两种互斥模式：

```json
{"symbols":["600519","000001"],"start":"2026-09-24","end":"2026-09-24","adjustment":"qfq"}
```

或 bootstrap count 模式：

```json
{"symbols":["600519"],"lookback_bars":400,"adjustment":"qfq"}
```

`symbols` 每批最多 100 个；`start/end` 与 `lookback_bars` 不能混用。正常 EOD 使用小范围 recent count，bootstrap 使用所需历史 bar 数。`data.items` 除 fetched/persisted/error 外还可返回 `history_rebased` 与 `changed_from`，表示 corporate-action revision 导致 qfq 历史需要重新物化。

### `POST /internal/sync/quotes` / `POST /internal/sync/dividends`

两者都使用内部 token，接收 `{"symbols":[...]}`。Quote 使用 provider 原生批量快照；Dividend 使用有界并发并逐证券返回同步结果。Hub 同时保留 `/internal/sync/quote` 与 `/internal/sync/dividend` 单数别名用于兼容，Backend 使用复数规范路径。

### `POST /internal/calendar/validate`

内部 token 保护。请求 `{"trade_date":"YYYY-MM-DD"}`，使用 eltdx workday 数据校验已经发生的真实交易日，并与数据库中的官方年度预期日历合并。若 official expected 与 actual validation 冲突，状态为 `UNKNOWN`，EOD fail closed。

### `POST /internal/sync/securities`

使用同样的内部 token，从 provider 拉取证券元数据并以一个事务 upsert 到
`securities`。证券主数据同步是整批原子操作：provider 返回空批次、记录校验失败或
持久化失败时，响应不会报告任何成功项，也不会留下部分写入；只有整批成功才会报告
逐证券成功项。响应是 `operation: "security"` 的同步摘要。匿名调用返回 `401`；服务未配置
token 返回 `503`。

## 版本与限制

- `/api/v1` 是后端当前实际挂载的 API 路径；行情数据中枢不使用该前缀。
- 通知历史和失败/遗留投递恢复端点见“告警通知 API”；告警规则由 `/alerts` 端点管理。
- 行情读取只读持久化结果；Quote/Daily/Dividend 写入都通过受内部 token 保护的同步端点触发。
