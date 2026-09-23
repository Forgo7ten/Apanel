# 配置参考

Compose 从仓库根目录的 `.env` 读取变量。`.env.example` 是本地模板，`.env` 被 Git 忽略；不要提交真实凭据。

## Compose 入口变量

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | `apanel` | Compose 项目名，影响容器和命名卷前缀 |
| `APP_ENV` | 必填；模板为 `development` | 后端、行情服务、迁移和调度器的运行环境 |
| `HTTP_PORT` | `8080` | Nginx 宿主机端口，映射到容器 `80` |
| `NODE_ENV` | 必填；模板为 `production` | 前端容器运行环境 |

Compose 对 `APP_ENV`、`POSTGRES_PASSWORD`、`JWT_SECRET_KEY`、`INTERNAL_API_TOKEN` 和 `NODE_ENV` 使用必填插值；缺少任一变量时，Compose 不会启动完整栈。

## PostgreSQL

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `POSTGRES_DB` | `apanel` | PostgreSQL 初始数据库名 |
| `POSTGRES_USER` | `apanel` | PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | 必填 | PostgreSQL 密码；Compose 会把它拼入服务连接 URL |
| `DATABASE_URL` | Compose 自动生成 | Python 服务使用的 SQLAlchemy async URL；直接运行服务时可手动设置 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `5` | 建立数据库连接的超时 |
| `DATABASE_COMMAND_TIMEOUT_SECONDS` | `5` | PostgreSQL 命令超时 |

Compose 的 `DATABASE_URL` 使用 `postgresql+asyncpg://...@postgres:5432/...`，因此变量中的数据库密码应使用 URL-safe 字符。生产环境会拒绝已知默认密码。

## 后端认证

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `JWT_SECRET_KEY` | 必填；生产环境至少 32 字节且不能使用已知默认值 | 签发 HS256 access JWT |
| `JWT_ISSUER` | `apanel` | JWT `iss` claim |
| `JWT_AUDIENCE` | `apanel-web` | JWT `aud` claim |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | access token 生命周期 |
| `REFRESH_TOKEN_TTL_SECONDS` | `2592000` | refresh session 生命周期 |
| `INVITATION_TTL_SECONDS` | `604800` | 邀请 token 生命周期 |
| `REFRESH_COOKIE_NAME` | `refresh_token` | 刷新 Cookie 名称 |
| `REFRESH_COOKIE_SECURE` | Compose 默认 `false` | 是否给刷新 Cookie 设置 `Secure`；生产 Compose 环境必须明确设为 `true`，直接运行服务时生产环境可不设置并使用安全默认值 |

后端也识别 `JWT_SECRET` 作为 `JWT_SECRET_KEY` 的别名，识别 `ENVIRONMENT` 作为 `APP_ENV` 的别名。Compose 使用上表中的规范变量名。

## 基础设施超时

| 变量 | 默认值 | 使用服务 |
| --- | --- | --- |
| `HEALTH_PROBE_TIMEOUT_SECONDS` | `2` | 后端和行情服务的 PostgreSQL/Redis 健康探针 |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` | `5` | 后端和行情服务 Redis 建连 |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `5` | 后端和行情服务 Redis 操作 |
| `REDIS_URL` | Compose 内部为 `redis://redis:6379/0` | Redis 健康客户端 |
| `CELERY_BROKER_URL` | Compose 内部为 `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | Compose 内部为 `redis://redis:6379/1` | Celery result backend |

直接运行后端时，`CELERY_BROKER_URL` 和 `CELERY_RESULT_BACKEND` 为空会回退到 `REDIS_URL`。`LOG_LEVEL`、`TIMEZONE`、`APP_NAME`、`APP_VERSION` 和 `DATABASE_ECHO` 也由后端 Settings 支持，默认分别为 `INFO`、`Asia/Shanghai`、`Apanel Backend`、`0.1.0` 和 `false`；Compose 当前未显式覆盖它们。

## 行情服务

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `MARKET_DATA_PROVIDER` | `tdx` | provider 注册表名称；当前只注册 `tdx` |
| `PROVIDER_TIMEOUT_SECONDS` | `5` | provider 适配器操作超时 |
| `INTERNAL_API_TOKEN` | Compose 必填；生产环境必填 | 保护两个同步写入接口的共享 token |
| `TDX_SERVERS` | `119.147.212.81:7709,101.227.73.20:7709` | 逗号分隔或 JSON 数组的 TDX host:port 列表 |
| `TDX_SERVER_LIST` | `TDX_SERVERS` 的别名 | TDX 服务器列表别名 |
| `TDX_CONNECT_TIMEOUT_SECONDS` | `5` | 单个 TDX 连接超时 |
| `TDX_RETRY_ATTEMPTS` | `1` | TDX 连接重试次数，范围 `0..10` |
| `TDX_SYMBOL_MAX_PAGES` | `100` | 证券列表最大分页数 |
| `TDX_BAR_PAGE_SIZE` | `800` | 单页日线数量，范围 `1..800` |
| `TDX_BAR_MAX_PAGES` | `64` | 日线最大分页数 |

Compose 当前只把 `MARKET_DATA_PROVIDER`、`PROVIDER_TIMEOUT_SECONDS` 和 `INTERNAL_API_TOKEN` 显式传入行情服务；TDX 细化变量在直接运行服务时生效。若需要在 Compose 中覆盖 TDX 细化参数，应在 Compose 服务环境中显式添加它们。

行情服务还识别 `INTERNAL_SYNC_TOKEN` 和 `MARKET_DATA_INTERNAL_TOKEN` 作为 `INTERNAL_API_TOKEN` 的别名。生产环境若缺少内部 token，服务启动校验会失败。

## 前端

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `/api/v1` | 浏览器请求后端 API 的基路径 |

Compose 当前不把 `NEXT_PUBLIC_API_BASE_URL` 写入前端容器，因此默认使用 Nginx 同源路径。Next.js 的 `output: standalone` 构建会在构建阶段处理公开环境变量；直接运行前端时应在构建/启动方式中明确提供该变量。

## 生产环境要点

```dotenv
APP_ENV=production
POSTGRES_PASSWORD=<unique-url-safe-password>
JWT_SECRET_KEY=<random-secret-at-least-32-bytes>
INTERNAL_API_TOKEN=<random-service-token>
REFRESH_COOKIE_SECURE=true
```

生产部署还应通过 HTTPS 终止层保护浏览器会话，并限制 PostgreSQL、Redis 和行情服务内部端口的网络访问。
