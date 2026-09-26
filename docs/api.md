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

行情服务也返回 `success`、`data` 和 `error`。一般请求错误时 `data` 为 `null`；健康检查降级和部分同步失败会保留 `data`，分别返回依赖状态或逐项结果。后端请求校验错误还会带 `error.details`，其中只包含字段位置、错误类型和消息。

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

## 告警通知 API

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

## 行情服务 API

以下路径由 `market-data-service` 直接提供，不经过 Nginx，也没有 `/api/v1` 前缀。它们默认只在 Compose 网络内可访问。

### `GET /health`

探测行情服务的 PostgreSQL 和 Redis。全部健康时返回 `200`，任一依赖异常时返回 `503`，数据状态为 `healthy` 或 `degraded`。

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

请求体：

```json
{
  "symbols": ["600519", "000001"],
  "start": "2026-09-01",
  "end": "2026-09-24",
  "adjustment": "none"
}
```

`symbols` 至少包含一个值；`start` 不得晚于 `end`。服务会规范化代码、调用 provider、校验返回记录并按证券隔离持久化失败。`data` 返回 `operation`、`total`、`succeeded`、`failed`、`ok` 和逐证券 `items`；部分失败时响应包的 `success` 为 `false`，错误码为 `PARTIAL_SYNC_FAILURE`。

### `POST /internal/sync/securities`

使用同样的内部 token，从 provider 拉取证券元数据并以一个事务 upsert 到
`securities`。证券主数据同步是整批原子操作：provider 返回空批次、记录校验失败或
持久化失败时，响应不会报告任何成功项，也不会留下部分写入；只有整批成功才会报告
逐证券成功项。响应是 `operation: "security"` 的同步摘要。匿名调用返回 `401`；服务未配置
token 返回 `503`。

## 版本与限制

- `/api/v1` 是后端当前实际挂载的 API 路径；行情服务不使用该前缀。
- 通知历史和失败/遗留投递恢复端点见“告警通知 API”；告警规则由 `/alerts` 端点管理。
- 行情读取只读持久化结果；报价写入由 provider/repository 边界提供，但当前没有公开的报价同步 HTTP 路由。
