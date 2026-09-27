# Apanel

Apanel 是一个面向 A 股的技术指标监控与提醒工作台。它把行情数据、指标计算和状态识别放在后端，把可操作的监控界面放在前端，帮助用户发现值得关注的变化。

Apanel 不预测价格、不提供买卖建议，也不执行自动交易。当前仓库提供可运行的认证、行情同步、指标/状态持久化、动态监控表、提醒与飞书通知，以及完整的 Web 工作台。

## 当前能力

- 管理员初始化、邀请注册、登录、刷新和退出登录。
- FastAPI 后端提供证券查询、动态 Watch Table、指标/状态历史、提醒规则、通知记录和用户设置 API，统一使用 JSON 成功/错误响应。
- 可插拔指标注册表内置 MA、Projected MA、RSI、KDJ、BOLL、MACD；历史计算提供线性 series path，并把结果按参数与 `qfq`/`none` 口径持久化。
- 状态引擎内置交叉、方向、带宽变化和突破状态；状态按参数 identity 与复权口径保存，可直接作为 Alert 条件。
- 独立 Market Data Hub 以 eltdx 为主 Provider、AKShare 为证券主数据 fallback，统一负责证券元数据、批量 Quote、日线、分红与已发生交易日校验。
- 新增监控股票或 Alert-only 股票会触发历史 bootstrap；日终流水线负责增量行情、分红、指标、状态、Edge Trigger 和通知 outbox。
- Alert VALUE 规则绑定具体参数、scalar field 和 adjustment；STATE 规则绑定具体 `state_code` 和参数。Edge Trigger 只在 RESET → ACTIVE 时产生新通知。
- 飞书 Webhook 只允许官方 HTTPS host/path，API 不回显明文，并使用 AES-GCM 写入独立 `user_secrets`。
- Next.js 工作台包括登录、邀请注册、动态股票监控表、指标/状态详情、提醒规则、通知中心和用户设置。
- Docker Compose 编排 PostgreSQL、Redis、迁移、Market Data Hub、证券主数据 bootstrap、Backend、Celery worker/Beat、Frontend 和 Nginx。

## 架构组件

| 组件 | 作用 | Compose 网络中的端口 |
| --- | --- | --- |
| `nginx` | 对外入口；转发 Web、前端健康检查和后端 API | 宿主机 `${HTTP_PORT}`，默认 `8080` |
| `frontend` | Next.js 页面、浏览器认证会话和 `/api/health` | `3000`（仅内部暴露） |
| `backend` | FastAPI 公共 API：认证、证券、Watch、Settings、指标/状态、Alert、Notification | `8000`（仅内部暴露） |
| `market-data-hub` | provider 适配、批量行情同步、分红、日历校验与市场数据持久化 | `8001`（仅内部暴露） |
| `migrate` | 启动前执行 Alembic 数据库迁移 | 一次性容器 |
| `security-bootstrap` | 首次/手工同步证券主数据；失败可独立重试 | 一次性容器 |
| `worker` | Celery worker；执行历史 bootstrap、盘中 Quote、EOD、提醒和通知恢复 | 无 HTTP 端口 |
| `scheduler` | Celery Beat；按交易时段调度 Quote/EOD/通知恢复 | 无 HTTP 端口 |
| `postgres` | PostgreSQL 16，认证和行情数据存储 | 仅内部网络 |
| `redis` | 健康检查客户端、Celery broker/result backend | 仅内部网络 |

## 技术栈

- Python 3.12、FastAPI、Pydantic Settings、SQLAlchemy 2、Alembic、asyncpg。
- PostgreSQL 16、Redis 7、Celery 5。
- Next.js 16.3.6、React 18.3.1、TypeScript 5.8.3、Tailwind CSS 3.4.17。
- Nginx 1.27 作为同源反向代理。
- 后端密码使用 Argon2id；访问令牌使用 JWT，刷新令牌使用 HttpOnly Cookie；用户飞书 Webhook 使用 AES-GCM 应用层加密。

## 快速启动

需要 Docker Engine 和 Docker Compose。先创建本地环境文件，并替换其中的凭据：

```bash
cp .env.example .env
docker compose up --build
```

首次启动会等待 PostgreSQL 和 Redis 就绪，运行 `alembic upgrade head`，再启动 Market Data Hub、证券主数据 bootstrap、Backend、Celery worker/Beat、Frontend 和 Nginx。默认访问地址：

- Web：<http://localhost:8080>
- Nginx 存活检查：<http://localhost:8080/health>
- 前端进程检查：<http://localhost:8080/api/health>
- 后端健康检查：<http://localhost:8080/api/v1/health>

Compose 只把 Nginx 的 `${HTTP_PORT}` 发布到宿主机；后端和行情服务的 `8000`/`8001` 只在 Compose 网络中可见。

### 为行情服务配置出站代理

如果行情 provider 需要经由代理访问外网，只在 `.env` 中设置行情服务专用变量：

```dotenv
MARKET_DATA_HTTP_PROXY=http://host.docker.internal:your-proxy-port
MARKET_DATA_HTTPS_PROXY=http://host.docker.internal:your-proxy-port
MARKET_DATA_ALL_PROXY=
MARKET_DATA_NO_PROXY=
```

这些变量只注入 `market-data-hub`；默认留空时不会启用代理。Compose 会自动把 `localhost`、`127.0.0.1`、`postgres`、`redis`、`backend` 和 `market-data-hub` 加入 `NO_PROXY`，保证内部请求不经过外部代理。`host.docker.internal` 使用跨 Linux 的 `host-gateway` 映射；宿主代理必须监听 Docker 可达接口，不能只监听宿主机的 `127.0.0.1`。代理 URL 可能包含敏感凭据，请只保存在被 Git 忽略的 `.env` 中，应用日志不会打印这些值。

## 初始化管理员

迁移完成且后端容器健康后，创建第一个管理员：

```bash
docker compose exec backend python -m app.cli bootstrap-admin \
  --username admin --email admin@example.com
```

命令会交互式读取并确认至少 8 个字符的密码。第一个管理员只能创建一次；管理员登录后可调用 `POST /api/v1/auth/invitations` 创建邀请，再让受邀用户使用 `/register#token=<token>` 完成注册。

## 验证当前仓库

本仓库已经把主要回归检查固化到 `.github/workflows/ci.yml`。本地可按模块执行：

```bash
# Backend
cd backend && python -m pytest && ruff check .

# Market Data Hub
cd market-data-hub && python -m pytest && ruff check .

# Frontend
cd frontend && npm test && npm run typecheck && npm run lint && npm run build

# Compose / migration
docker compose config -q
docker compose run --rm migrate
```

CI 不访问真实 TDX/AKShare 网络；真实 provider 连通性和数据授权属于 staging/部署前检查。

## 常用命令

```bash
docker compose up --build -d       # 构建并后台启动
docker compose ps                  # 查看服务状态
docker compose logs -f backend     # 跟踪 API 日志
docker compose logs -f worker      # 跟踪 bootstrap/EOD/通知任务
docker compose logs -f market-data-hub
docker compose down                # 停止服务，保留命名卷
```

本地 Python 与前端校验命令见 [开发指南](docs/development.md)。删除数据库和 Redis 数据需要显式使用 `docker compose down -v`，请确认数据不再需要后再执行。

## 目录结构

```text
backend/                 FastAPI、认证、Watch/Alert/Settings、Alembic、指标/状态与 Celery 任务
  app/api/               HTTP 路由与依赖
  app/services/          应用服务
  app/repositories/      数据访问边界
  app/indicators/        指标输入、结果和注册表
  app/states/            状态定义、注册表和识别引擎
  alembic/                数据库迁移
market-data-hub/          独立行情数据中枢与 Provider 适配器（当前 Compose 使用）
frontend/                Next.js 工作台
nginx/                   同源反向代理配置
compose.yaml             本地服务编排
.env.example             Compose 环境变量模板
```

## 文档

- [架构](docs/architecture.md)：服务边界、请求流和持久化边界。
- [开始使用](docs/getting-started.md)：从启动到管理员邀请注册的完整流程。
- [开发指南](docs/development.md)：本地依赖、测试、代码检查和迁移命令。
- [配置参考](docs/configuration.md)：Compose 与各服务环境变量。
- [API 参考](docs/api.md)：后端、前端和行情服务端点。
- [安全说明](docs/security.md)：凭据、认证会话和内部接口安全边界。
- [行情数据](docs/market-data.md)：provider、同步、存储和复权口径。
- [指标与状态](docs/indicators-and-states.md)：指标输入输出和状态识别语义。

## 当前限制

- v1 的外部行情主源固定为 eltdx，AKShare 只作为证券主数据 fallback。`electkismet/eltdx` 当前公开条款包含非商业/研究用途限制；商业或付费部署前必须完成数据源授权评审，必要时通过既有 Provider interface 替换底层数据源。
- 飞书是当前唯一实现的通知出口。Edge 状态与 PENDING outbox 会在同一数据库事务中提交，但第三方 Webhook 不提供端到端幂等键，因此无法宣称网络层面的绝对 exactly-once。
- 仓库内置的 A 股年度交易日 seed 需要随交易所年度休市通知更新；缺少年度数据或官方预期与实际交易校验冲突时任务会 fail closed，而不是回退到“周一到周五”。
- Apanel 仍不预测股票价格、不提供买卖建议，也不执行自动交易。
