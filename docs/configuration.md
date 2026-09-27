# 配置参考

Compose 从仓库根目录的 `.env` 读取变量。`.env.example` 是本地模板，`.env` 被 Git 忽略；不要提交真实凭据。

## Compose 入口变量

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | `apanel` | Compose 项目名，影响容器和命名卷前缀 |
| `APP_ENV` | 必填；模板为 `development` | 后端、行情服务、迁移和调度器的运行环境 |
| `HTTP_PORT` | `8080` | Nginx 宿主机端口，映射到容器 `80` |
| `NODE_ENV` | 必填；模板为 `production` | 前端容器运行环境 |

Compose 对 `APP_ENV`、`POSTGRES_PASSWORD`、`JWT_SECRET_KEY`、`INTERNAL_API_TOKEN` 和 `NODE_ENV` 使用必填插值；缺少任一变量时，Compose 不会启动完整栈。`APP_SECRETS_KEY` 在模板中允许本地留空；`APP_ENV=production` 时 Backend API 会在启动校验中强制要求。Worker 在实际读取/发送用户 Webhook 时同样需要该 key，因此生产 Compose 应同时提供给 `backend` 与 `worker`。

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

## 用户密钥与飞书

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `APP_SECRETS_KEY` | 本地可空；生产 Backend/Worker 必填 | URL-safe base64 编码的 32-byte AES-GCM master key，用于加密用户 Webhook |
| `APP_SECRETS_KEY_VERSION` | `v1` | 写入 `user_secrets.key_version` 的密钥版本标识 |
| `FEISHU_WEBHOOK_ALLOWED_HOSTS` | `open.feishu.cn,open.larksuite.com` | 飞书/Lark Webhook hostname allowlist |

Compose 只把 `APP_SECRETS_KEY*` 注入 `backend` 与 `worker`。`scheduler`、`frontend`、`market-data-hub` 和 `security-bootstrap` 不需要也不应获得该密钥。生产 API 启动时若缺少 master key 会失败。

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

## Market Data Hub

| 变量 | 默认值/要求 | 作用 |
| --- | --- | --- |
| `ELTDX_HOSTS` | 空 | 可选的逗号分隔/JSON host:port 列表；空值使用 eltdx 内置主站集合与 ranking |
| `ELTDX_TIMEOUT_SECONDS` | `5` | eltdx 7709 请求/连接基础超时 |
| `ELTDX_PROBE_HOSTS` | `true` | 启动时是否对候选主站做延迟探测与排序 |
| `ELTDX_HEARTBEAT_INTERVAL_SECONDS` | `30` | eltdx 连接心跳间隔 |
| `ELTDX_BAR_PAGE_SIZE` | `800` | 日线自动分页单页数量，范围 `1..800` |
| `ELTDX_BAR_MAX_PAGES` | `64` | 极长历史 fallback 的最大页数；正常 EOD 不使用固定全量分页 |
| `ELTDX_QUOTE_BATCH_SIZE` | `50` | 单次 eltdx 批量快照证券数 |
| `MARKET_DATA_SYNC_CONCURRENCY` | `4` | 无原生 batch capability 的 Hub 同步有界并发数 |
| `MARKET_DATA_SYNC_MAX_SYMBOLS_PER_REQUEST` | `100` | Hub 单次同步请求允许的最大证券数/内部 batch 上限 |
| `SECURITY_MASTER_FALLBACK_PROVIDER` | `akshare` | Security Master fallback；`none`/`disabled` 可关闭 |
| `AKSHARE_SECURITY_TIMEOUT_SECONDS` | `90` | AKShare 股票 + ETF 完整批次总超时 |
| `AKSHARE_MIN_STOCK_COUNT` | `1000` | AKShare 股票源最低完整批次数 |
| `AKSHARE_MIN_ETF_COUNT` | `1` | AKShare ETF 源最低完整批次数 |
| `INTERNAL_API_TOKEN` | Compose 必填；生产环境必填 | 保护 Hub 内部同步写接口 |

第一版 Provider 集合固定为 `eltdx` + `akshare`。Backend 不配置或选择外部 Provider；Provider routing 全部位于 Hub 内。

`ELTDX_POOL_SIZE`、`ELTDX_SERVER_COUNT`、`ELTDX_CONNECTIONS_PER_SERVER` 也是 Hub Settings 支持的可选高级参数，但 Compose 默认不注入；需要调优时再显式增加环境映射。

### 行情服务出站代理

行情服务需要访问外部行情 provider 时，可以在根目录 `.env` 中为它单独配置代理：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `MARKET_DATA_HTTP_PROXY` | 空 | 注入行情服务的 `HTTP_PROXY` |
| `MARKET_DATA_HTTPS_PROXY` | 空 | 注入行情服务的 `HTTPS_PROXY` |
| `MARKET_DATA_ALL_PROXY` | 空 | 注入行情服务的 `ALL_PROXY` |
| `MARKET_DATA_NO_PROXY` | 空 | 额外的 `NO_PROXY` 主机；Compose 会始终追加 `localhost`、`127.0.0.1`、`postgres`、`redis`、`backend` 和 `market-data-hub` |

这四个变量只会映射到 `market-data-hub`，不会传给 PostgreSQL、Redis、backend 或其他容器。默认空值不会启用代理。若代理运行在 Docker 宿主机，Compose 会为行情服务添加跨 Linux 可用的 `host.docker.internal:host-gateway`；宿主代理必须监听 Docker 可达的接口（不能只监听宿主机的 `127.0.0.1`）。

代理 URL 可能包含凭据。`.env` 不纳入版本库，应用日志不会打印这些值；不要把真实代理 URL、用户名或密码写入文档、命令行历史或共享的 `docker compose config` 输出。

行情服务还识别 `INTERNAL_SYNC_TOKEN` 和 `MARKET_DATA_INTERNAL_TOKEN` 作为 `INTERNAL_API_TOKEN` 的别名。生产环境若缺少内部 token，服务启动校验会失败。

## Scheduler 与交易时段

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `QUOTE_REFRESH_INTERVAL_MINUTES` | `5` | 候选交易时段内的 Quote 刷新步长，限制为 5–10 分钟 |
| `EOD_PIPELINE_HOUR` | `15` | EOD 任务候选小时（Asia/Shanghai） |
| `EOD_PIPELINE_MINUTE` | `20` | EOD 任务候选分钟 |
| `SCHEDULER_LOCK_TTL_SECONDS` | `3600` | Redis 分布式任务锁 TTL |
| `SCHEDULER_RETRY_MAX_ATTEMPTS` | `3` | Celery 任务最大重试次数 |
| `SCHEDULER_RETRY_BACKOFF_SECONDS` | `60` | 指数退避基数 |

Beat 只在 09:30–11:30、13:00–15:00 的候选区间投递 Quote 任务，并在 15:00 额外刷新一次；worker 仍会检查持久化 `trading_calendar`，因此周一到周五 cron 不是最终交易日判断。EOD 同样先过交易日历和 Hub actual validation；未知/冲突状态 fail closed。

### 任务队列

Compose 的 Celery worker 监听 `default`、`market-data`、`pipeline` 队列。Beat 的职责只是按候选时间投递任务：Quote 使用 `market-data`，EOD 使用 `pipeline`，pending notification recovery 使用 `default`。真正的交易日判断、锁、重试和业务执行都在 worker 内完成。

## Backend 行情同步与分析 bootstrap

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `MARKET_DATA_SYNC_BATCH_SIZE` | `50` | Backend/Worker 调 Hub 时的证券 chunk 大小 |
| `MARKET_DATA_QUOTE_TIMEOUT_SECONDS` | `15` | Quote 批次请求超时 |
| `MARKET_DATA_DAILY_TIMEOUT_SECONDS` | `60` | 日线/分红增量请求超时 |
| `MARKET_DATA_BOOTSTRAP_TIMEOUT_SECONDS` | `120` | 单证券历史 bootstrap 请求超时 |
| `ANALYSIS_BOOTSTRAP_MIN_BARS` | `400` | 首次监控的最小历史 bar 预算；实际值还会取指标需求上界 |
| `ENABLE_NONE_ANALYSIS` | `true` | 是否同时物化 `none` 指标/状态序列；qfq 始终支持 |

新增股票、启用提醒或新增参数需求会 best-effort enqueue `bootstrap_security_data`；Redis 锁按 symbol 去重。EOD 正常路径只同步目标交易日并物化最新 snapshot/state；若 Hub 报告 qfq revision 变化，则从 `changed_from` 重建受影响历史。

## 证券主数据 bootstrap

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `SECURITY_BOOTSTRAP_RETRY_INTERVAL_SECONDS` | `30` | Compose 中 `security-bootstrap` 在可重试失败之间等待的秒数；必须为正数 |
| `SECURITY_BOOTSTRAP_TIMEOUT_SECONDS` | `180` | Compose 中 `security-bootstrap` 每次行情服务请求的总预算；覆盖 eltdx 证券代码表获取、AKShare 完整批次总超时（默认 `90`）、worker 收尾（最多 `5`）及少量 HTTP 开销 |

Compose 会创建一个 `security-bootstrap` 一次性任务。它等待 `market-data-hub` 健康后，使用 `INTERNAL_API_TOKEN` 执行 `bootstrap-securities --retry-until-success`；网络/超时、HTTP `408`/`429`/`5xx`、HTTP 200 错误 envelope 中稳定的 `PROVIDER_TIMEOUT`/`PROVIDER_UNAVAILABLE`（包括同步摘要 item 的 `error.code`）和空同步结果会按间隔持续重试，协议错误、其他 `4xx`、业务/鉴权/验证失败、非 provider 的部分失败和意外错误会立即以非零退出。该任务设置 `restart: "no"`，不阻塞 backend 启动；需要手工重跑时执行 `docker compose run --rm security-bootstrap`。上述两个变量只影响该一次性任务，不会传给 frontend。

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
APP_SECRETS_KEY=<urlsafe-base64-32-byte-key>
APP_SECRETS_KEY_VERSION=v1
REFRESH_COOKIE_SECURE=true
```

生产部署还应通过 HTTPS 终止层保护浏览器会话，并限制 PostgreSQL、Redis 和行情服务内部端口的网络访问。
