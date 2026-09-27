# 架构

Apanel 由同一 Compose 网络中的 Web 入口、前端、后端、行情数据中枢和基础设施组成。对外只暴露 Nginx；其他 HTTP 服务使用 Compose 的内部网络。

## 请求与数据流

```text
浏览器
  │
  ▼
Nginx :80  ───────────────► frontend :3000（页面与 /api/health）
  │
  └───────────────────────► backend :8000（/api/v1）
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
             PostgreSQL                    Redis

market-data-hub :8001
  ├─► eltdx / TDX（Security、Quote、Daily、Dividend、Workday）
  ├─► AKShare（Security Master fallback only）
  ├─► PostgreSQL（共享行情事实与日历校验）
  └─► Redis（健康探针客户端）

scheduler ──► Celery Beat ──► Redis broker/result backend ──► worker
                                                        ├─► market-data-hub
                                                        └─► PostgreSQL
```

Nginx 的路由规则如下：

- `/health` 由 Nginx 直接返回 `ok`，不访问上游。
- `/api/health` 转发到 Next.js 的前端健康路由。
- `/api/` 转发到后端；后端实际 API 前缀是 `/api/v1`。
- 其他路径转发到前端。

## Compose 服务

| 服务 | 当前职责 | 启动条件 |
| --- | --- | --- |
| `postgres` | PostgreSQL 16，挂载 `postgres_data` | 自带 `pg_isready` 检查 |
| `redis` | Redis 7，开启 AOF，挂载 `redis_data` | `redis-cli ping` 成功 |
| `migrate` | 在后端镜像中执行 `alembic upgrade head` | PostgreSQL 健康 |
| `market-data-hub` | Provider routing、行情/分红同步、日历校验和共享行情持久化 | PostgreSQL、Redis 健康且迁移成功 |
| `security-bootstrap` | 一次性同步证券主数据，provider 暂时不可用时独立重试 | `market-data-hub` 健康 |
| `backend` | 认证、证券、Watch、Settings、指标/状态、Alert、Notification API | PostgreSQL、Redis、行情数据中枢健康且迁移成功 |
| `worker` | 执行 bootstrap、行情增量同步、EOD、提醒评估和通知恢复 | PostgreSQL、Redis、行情数据中枢健康且迁移成功 |
| `scheduler` | 运行 `celery beat`，按 A 股交易时段触发任务 | PostgreSQL、Redis、行情数据中枢和后端健康 |
| `frontend` | 运行 Next.js standalone server | 后端健康 |
| `nginx` | 发布 `${HTTP_PORT:-8080}`，聚合前后端 | 后端和前端健康 |

后端和行情数据中枢的容器镜像都基于 Python 3.12；前端镜像使用 Node current Alpine，并随 Node.js Current/latest 更新。Compose 同时启动 Celery worker 与 Beat；Beat 只负责产生任务，实际任务由 worker 执行。

## 后端模块边界

后端按以下边界组织：

- `app/api` 只负责 HTTP 输入输出、认证依赖和错误映射。
- `app/services` 承载认证、Watch 聚合、指标/状态物化、Alert observation、Notification outbox、Settings/Secret、bootstrap 与交易日历等应用服务。
- `app/repositories` 负责 PostgreSQL 数据访问、锁定/查询边界与 Redis 健康探针；业务判断不放进 repository。
- `app/indicators` 提供与 API/ORM 无关的指标计算及插件注册表。
- `app/states` 提供状态定义、状态注册表和边缘触发识别引擎。
- `app/providers` 提供外部数据/通知适配边界；当前行情 provider 实现在独立行情数据中枢中。
- `app/tasks` 提供 Celery 应用、Beat 和 worker 导入目标。

应用通过 FastAPI lifespan 创建异步 SQLAlchemy engine、请求级 session factory、Redis client 和健康服务，并在进程退出时释放资源。健康探针分别检查 PostgreSQL 和 Redis；任一依赖失败时返回 `503` 和 `degraded` 状态，而不是隐藏另一个依赖的结果。

## 行情数据中枢边界

行情数据中枢独立创建自己的数据库 engine、Redis client、provider 和 repository。provider 只返回经过 domain 校验的 `Security`、`Quote`、`DailyBar`、`Dividend` 对象；同步服务再将这些对象批量 upsert 到共享 PostgreSQL。

行情数据中枢 HTTP 路由没有 `/api/v1` 前缀：

- `/health` 是服务健康检查。
- `/internal/quotes/{symbol}`、`/internal/daily-bars/{symbol}`、`/internal/dividends/{symbol}` 是内部读取接口。
- `/internal/sync/securities`、`/internal/sync/quotes`、`/internal/sync/daily`、`/internal/sync/dividends` 是受内部 token 保护的同步接口。
- `/internal/calendar/validate` 用 eltdx 已发生 workday 数据校验交易日，并与持久化的年度预期日历合并。

Compose 不把 `8001` 发布到宿主机，Nginx 也不代理这些路径；它们只对同一 Docker 网络内的调用方可见。

## 运行时数据链路

### 新证券 bootstrap

```text
新增 Watch 股票 / 新增需要该证券的 Alert
        ↓
best-effort enqueue bootstrap_security_data
        ↓
Redis symbol lock 去重
        ↓
Hub 同步足够历史 DailyBar（qfq；可选 none）+ Dividend
        ↓
IndicatorService 历史物化
        ↓
StateService 历史物化
```

bootstrap 只建立 observation 基础，不回放历史通知。EOD 是 enqueue 失败时的恢复路径。

### 盘中 Quote

Beat 只在 09:30–11:30、13:00–15:00 的候选时段产生任务；worker 还会检查 `trading_calendar`。实际监控 universe 是 Watch 股票与 enabled Alert-only 股票的并集。Backend 按 chunk 调 Hub，Hub 使用 eltdx 原生批量 snapshots。

### 日终 EOD

```text
TradingCalendar guard / Hub actual validation
        ↓
minimum-history readiness（不足则同步 bootstrap，不评估历史 Alert）
        ↓
Daily sync(qfq，按配置可同时 none)
        ↓
Dividend sync
        ↓
target-date readiness
        ↓
最新 IndicatorSnapshot / corporate-action rebase
        ↓
IndicatorState
        ↓
Alert Edge evaluation + PENDING Notification（同事务）
        ↓
notification dispatcher → Feishu
```

停牌/无目标日 bar 的证券不会拿上一交易日 observation 冒充当天数据；交易日日历缺失或 expected/actual 冲突时 EOD fail closed。

## 读取与写入职责

HTTP 读取路径与后台物化路径明确分离：

- `GET /securities/{symbol}/indicators*`、`states*`、Watch detail 只读取已经持久化的数据，不通过页面访问补算历史。
- 新增 Watch 股票、创建/启用需要新参数的 Alert，以及修改已有指标/状态列目标，都会通过可注入的 `SecurityBootstrapScheduler` seam 请求 `bootstrap_security_data`；业务 Service 不直接依赖 Celery task。
- EOD 在增量同步前重新计算当前 Watch/Alert 指标需求与最小历史根数；若 API-side enqueue 曾失败或历史深度不足，会复用共享 bootstrap 自愈，且 bootstrap 不执行 Alert evaluation。
- 盘中 Quote、EOD、Dividend refresh、qfq rebase 与 pending notification recovery 都由 Celery worker 执行。
- `market-data-hub` 是唯一允许直接接触 eltdx/AKShare 的服务；Backend/Worker 只能走 Hub 内部 HTTP contract。

这种分离避免 GET 请求产生隐藏写操作，也让 bootstrap/EOD/重试可以独立观测和恢复。

## 数据库边界

迁移由 `backend/alembic` 统一管理。当前数据库表包括：

- 认证：`users`、`invitations`、`refresh_sessions`。
- 行情：`securities`、`daily_bars`、`quote_snapshots`、`dividend_events`、`market_data_adjustment_state`、`trading_calendar`。
- 分析：`indicator_snapshots`、`state_definitions`、`indicator_states`。
- 工作台：`watch_tables`、`watch_table_symbols`、`table_columns`、`user_settings`、`user_secrets`。
- 提醒：`alert_rules`、`alert_instances`、`notifications`。

行情表通过 `security_id` 关联 `securities`。日线唯一键是证券、交易日和复权类型；报价唯一键是证券和 UTC 时间戳；分红事件唯一键是证券和事件日期。repository 使用 PostgreSQL `ON CONFLICT` upsert，重复同步不会重复创建相同业务记录。

指标和状态由 Backend 统一物化到共享表，并按 `adjust_type`、参数 identity 和交易日隔离。EOD 正常路径只物化最新交易日；历史 bootstrap 或 qfq corporate-action revision 变化时才回建受影响历史。提醒规则读取精确的 indicator parameter/field 或 state parameter identity。Edge 状态和 PENDING notification outbox 在同一事务中提交，之后由通知阶段/恢复任务投递。

## 错误与配置边界

后端公共 API 使用 `success/data` 成功包和 `success/error` 错误包；行情数据中枢使用相同字段，一般请求错误时返回 `data: null`，但健康检查降级和部分同步失败会保留诊断数据。provider、数据库和参数错误在 API 层映射为稳定的错误码，不把底层连接细节直接返回给客户端。

`APP_ENV=production` 时，Backend API 会校验 PostgreSQL 密码、JWT、Secure Cookie 与 `APP_SECRETS_KEY`；Worker/CLI 只校验自身真正需要的凭据。Market Data Hub 生产环境要求 `INTERNAL_API_TOKEN`。不同进程使用最小化配置校验，避免把 JWT 或用户密钥错误地要求给 migration/scheduler 等不消费它们的进程。
