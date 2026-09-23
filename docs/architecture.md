# 架构

Apanel 由同一 Compose 网络中的 Web 入口、前端、后端、行情服务和基础设施组成。对外只暴露 Nginx；其他 HTTP 服务使用 Compose 的内部网络。

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

market-data-service :8001
  ├─► TDX provider
  ├─► PostgreSQL（共享行情表）
  └─► Redis（健康探针客户端）

scheduler ──► Celery Beat ──► Redis broker/result backend
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
| `market-data-service` | 读取 provider、同步和查询行情 | PostgreSQL、Redis 健康且迁移成功 |
| `backend` | 认证 API、健康 API 和应用内核 | PostgreSQL、Redis、行情服务健康且迁移成功 |
| `scheduler` | 运行 `celery beat` | PostgreSQL、Redis、行情服务和后端健康 |
| `frontend` | 运行 Next.js standalone server | 后端健康 |
| `nginx` | 发布 `${HTTP_PORT:-8080}`，聚合前后端 | 后端和前端健康 |

后端和行情服务的容器镜像都基于 Python 3.12；前端镜像使用 Node 20 Alpine。`backend/app/tasks/worker.py` 提供 Celery worker 导入目标，但 Compose 当前只启动 Beat，没有 worker 服务。

## 后端模块边界

后端按以下边界组织：

- `app/api` 只负责 HTTP 输入输出、认证依赖和错误映射。
- `app/services` 承载认证、健康检查等应用服务。
- `app/repositories` 负责 PostgreSQL/Redis 探针和数据访问。
- `app/indicators` 提供与 API/ORM 无关的指标计算及插件注册表。
- `app/states` 提供状态定义、状态注册表和边缘触发识别引擎。
- `app/providers` 提供外部数据/通知适配边界；当前行情 provider 实现在独立行情服务中。
- `app/tasks` 提供 Celery 应用、Beat 和 worker 导入目标。

应用通过 FastAPI lifespan 创建异步 SQLAlchemy engine、请求级 session factory、Redis client 和健康服务，并在进程退出时释放资源。健康探针分别检查 PostgreSQL 和 Redis；任一依赖失败时返回 `503` 和 `degraded` 状态，而不是隐藏另一个依赖的结果。

## 行情服务边界

行情服务独立创建自己的数据库 engine、Redis client、provider 和 repository。provider 只返回经过 domain 校验的 `Security`、`Quote`、`DailyBar`、`Dividend` 对象；同步服务再将这些对象批量 upsert 到共享 PostgreSQL。

行情服务 HTTP 路由没有 `/api/v1` 前缀：

- `/health` 是服务健康检查。
- `/internal/quotes/{symbol}` 和 `/internal/daily-bars/{symbol}` 是内部读取接口。
- `/internal/sync/daily` 和 `/internal/sync/securities` 是受内部 token 保护的写入/同步接口。

Compose 不把 `8001` 发布到宿主机，Nginx 也不代理这些路径；它们只对同一 Docker 网络内的调用方可见。

## 数据库边界

迁移由 `backend/alembic` 统一管理。当前数据库表分为两组：

- 认证：`users`、`invitations`、`refresh_sessions`。
- 行情：`securities`、`daily_bars`、`quote_snapshots`、`dividend_events`。

行情表通过 `security_id` 关联 `securities`。日线唯一键是证券、交易日和复权类型；报价唯一键是证券和 UTC 时间戳；分红事件唯一键是证券和事件日期。repository 使用 PostgreSQL `ON CONFLICT` upsert，重复同步不会重复创建相同业务记录。

指标结果和状态结果当前不依赖 SQLAlchemy，也没有对应的持久化表。它们由调用方以快照/结果对象传递；要形成长期提醒记录，还需要在应用层保存结果和调用状态引擎。

## 错误与配置边界

后端公共 API 使用 `success/data` 成功包和 `success/error` 错误包；行情服务使用相同字段，一般请求错误时返回 `data: null`，但健康检查降级和部分同步失败会保留诊断数据。provider、数据库和参数错误在 API 层映射为稳定的错误码，不把底层连接细节直接返回给客户端。

`APP_ENV=production` 时，后端拒绝已知默认 PostgreSQL 密码、过短或已知默认 JWT 密钥以及显式关闭的安全刷新 Cookie；行情服务还要求 `INTERNAL_API_TOKEN`。这些校验在应用启动或迁移/调度进程读取配置时执行。
