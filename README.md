# Apanel v1

Apanel 是面向 A 股技术指标监控与预警的 Docker Compose 应用。Sprint 0
提供前端、FastAPI 后端、行情服务、调度器、PostgreSQL、Redis 与 Nginx
的可运行编排骨架。

## 最短运行方式

```bash
cp .env.example .env
docker compose up --build
```

首次运行必须先复制 `.env.example` 为 `.env`，并在其中显式提供
`APP_ENV` 与 `POSTGRES_PASSWORD`；Compose 不会替你选择运行环境或使用公开的
固定数据库密码。`.env.example` 仅用于本地开发示例，暴露到其他环境前请替换为
唯一的 URL-safe 密码。

Compose 会先启动 PostgreSQL，再运行一次 `alembic upgrade head`；迁移成功后才会
启动后端与行情服务。停止服务时使用 `docker compose down`，不会删除数据库卷。

启动后访问：

- Web：<http://localhost:8080>
- 网关健康检查：<http://localhost:8080/health>
- API：<http://localhost:8080/api/v1>

后台运行可使用 `docker compose up --build -d`，查看状态使用
`docker compose ps`，停止并保留数据库卷使用 `docker compose down`。

`.env` 只用于本地配置，不要提交；在非本地环境中务必替换示例数据库密码（使用
URL-safe 字符），并通过反向代理或防火墙限制 PostgreSQL、Redis 和内部服务的
访问。数据库与 Redis 数据分别保存在 `postgres_data` 和 `redis_data` Compose
命名卷中。认证配置也必须替换 `JWT_SECRET_KEY`；生产环境使用
`APP_ENV=production`、`REFRESH_COOKIE_SECURE=true` 和 HTTPS。
迁移与调度器只执行数据库/任务基础设施，不接收或校验 `JWT_SECRET_KEY`；只有
backend API 进程需要生产 JWT 密钥。

## 首次创建管理员与邀请注册

迁移完成、后端容器可用后，在 backend 容器内执行一次：

```bash
docker compose exec backend python -m app.cli bootstrap-admin \
  --username admin --email admin@example.com
```

命令会安全地交互式读取管理员密码；也可以在自动化环境中用 stdin 提供两行密码。
管理员登录 <http://localhost:8080/login> 后，调用 `POST /api/v1/auth/invitations`
创建邀请。响应中的 token 只返回一次，把它放入注册地址的 URL fragment：
`http://localhost:8080/register#token=<token>`。受邀用户提交用户名和密码完成注册，
然后登录进入工作台。refresh token 只通过 HttpOnly cookie 传输，不会出现在 JSON 响应中。

## 本地验证

后端和行情服务是两个独立的 Python 项目，都包含名为 `app` 的包；Python 测试和
工具命令必须分别在对应目录运行，避免顶层包名冲突：

```bash
(cd backend && python -m pytest)
(cd backend && ruff check .)
(cd market-data-service && python -m pytest)
(cd market-data-service && ruff check .)
(cd frontend && npm test)
(cd frontend && npm run lint && npm run typecheck && npm run build)
```

`APP_ENV=production` 时，后端和行情服务会拒绝已知默认数据库密码；本地开发可在
复制出的 `.env` 中保留 `APP_ENV=development`，但仍应按需替换示例数据库密码。
