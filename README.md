# Apanel

Apanel 是一个面向 A 股的技术指标监控与提醒工作台。它把行情数据、指标计算和状态识别放在后端，把可操作的监控界面放在前端，帮助用户发现值得关注的变化。

Apanel 不预测价格、不提供买卖建议，也不执行自动交易。当前仓库提供可运行的认证、行情数据服务、指标与状态计算内核，以及工作台界面骨架。

## 当前能力

- 管理员初始化、邀请注册、登录、刷新和退出登录。
- FastAPI 后端健康检查，统一 JSON 成功/错误响应。
- 可插拔的指标注册表，内置 MA、Projected MA、RSI、KDJ、BOLL 和 MACD。
- 可复用的状态注册表与状态引擎，内置交叉、方向、带宽变化和突破状态。
- 独立的行情数据服务，提供 TDX provider、证券元数据、日线、报价快照和分红事件的持久化边界。
- Next.js 工作台，包括登录、邀请注册、股票监控、通知和设置页面。
- Docker Compose 编排 PostgreSQL、Redis、迁移、后端、行情服务、调度器、前端和 Nginx。

## 架构组件

| 组件 | 作用 | Compose 网络中的端口 |
| --- | --- | --- |
| `nginx` | 对外入口；转发 Web、前端健康检查和后端 API | 宿主机 `${HTTP_PORT}`，默认 `8080` |
| `frontend` | Next.js 页面、浏览器认证会话和 `/api/health` | `3000`（仅内部暴露） |
| `backend` | FastAPI 认证、健康检查、指标与状态内核 | `8000`（仅内部暴露） |
| `market-data-service` | provider 适配、行情读取与同步、市场数据持久化 | `8001`（仅内部暴露） |
| `migrate` | 启动前执行 Alembic 数据库迁移 | 一次性容器 |
| `scheduler` | Celery Beat 进程入口 | 无 HTTP 端口 |
| `postgres` | PostgreSQL 16，认证和行情数据存储 | 仅内部网络 |
| `redis` | 健康检查客户端、Celery broker/result backend | 仅内部网络 |

## 技术栈

- Python 3.12、FastAPI、Pydantic Settings、SQLAlchemy 2、Alembic、asyncpg。
- PostgreSQL 16、Redis 7、Celery 5。
- Next.js 16.3.6、React 18.3.1、TypeScript 5.8.3、Tailwind CSS 3.4.17。
- Nginx 1.27 作为同源反向代理。
- 后端密码使用 Argon2id；访问令牌使用 JWT，刷新令牌使用 HttpOnly Cookie。

## 快速启动

需要 Docker Engine 和 Docker Compose。先创建本地环境文件，并替换其中的凭据：

```bash
cp .env.example .env
docker compose up --build
```

首次启动会等待 PostgreSQL 和 Redis 就绪，运行 `alembic upgrade head`，然后按依赖顺序启动后端、行情服务、调度器、前端和 Nginx。默认访问地址：

- Web：<http://localhost:8080>
- Nginx 存活检查：<http://localhost:8080/health>
- 前端进程检查：<http://localhost:8080/api/health>
- 后端健康检查：<http://localhost:8080/api/v1/health>

Compose 只把 Nginx 的 `${HTTP_PORT}` 发布到宿主机；后端和行情服务的 `8000`/`8001` 只在 Compose 网络中可见。

## 初始化管理员

迁移完成且后端容器健康后，创建第一个管理员：

```bash
docker compose exec backend python -m app.cli bootstrap-admin \
  --username admin --email admin@example.com
```

命令会交互式读取并确认至少 8 个字符的密码。第一个管理员只能创建一次；管理员登录后可调用 `POST /api/v1/auth/invitations` 创建邀请，再让受邀用户使用 `/register#token=<token>` 完成注册。

## 常用命令

```bash
docker compose up --build -d       # 构建并后台启动
docker compose ps                  # 查看服务状态
docker compose logs -f backend     # 跟踪后端日志
docker compose logs -f market-data-service
docker compose down                # 停止服务，保留命名卷
```

本地 Python 与前端校验命令见 [开发指南](docs/development.md)。删除数据库和 Redis 数据需要显式使用 `docker compose down -v`，请确认数据不再需要后再执行。

## 目录结构

```text
backend/                 FastAPI、认证、Alembic、指标和状态引擎
  app/api/               HTTP 路由与依赖
  app/services/          应用服务
  app/repositories/      数据访问边界
  app/indicators/        指标输入、结果和注册表
  app/states/            状态定义、注册表和识别引擎
  alembic/                数据库迁移
market-data-service/     独立行情服务与 provider 适配器
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

- 工作台中的股票监控表、通知中心和设置页目前是展示骨架；仓库尚未提供监控表、提醒规则、通知记录或用户级行情查询 API。
- 指标结果和状态结果目前是后端纯计算对象，尚未写入 `IndicatorSnapshot` 等持久化表，也没有把它们接入前端表格。
- 通知 provider 目前只有抽象边界，没有可用的飞书、邮件或其他发送实现。
- `scheduler` 会启动 Celery Beat，但当前 Beat schedule 为空；Compose 没有单独的 Celery worker 服务。
- provider 注册表的全量行情 provider 当前只注册 `tdx`；证券主数据默认另有懒加载的 AKShare fallback，TDX
  证券列表失败或为空时才使用。AKShare 不负责报价、日线或分红。TDX 默认客户端的原始日线是未复权数据；
  请求 `qfq` 需要注入复权因子转换器。默认行情服务没有注入该转换器，因此实际同步未复权日线时应显式使用
  `adjustment=none`。
- TDX 网络节点和第三方数据可用性不由本仓库保证；部署前应自行验证数据授权、连通性和数据口径。
