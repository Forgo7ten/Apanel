# 开始使用

本指南描述从空目录配置本地环境、启动 Compose、创建管理员到完成邀请注册的流程。

## 前置条件

- Docker Engine，且 Docker Compose plugin 可用。
- 能够访问 Docker registry；首次构建会下载 Python、Node、PostgreSQL、Redis 和 Nginx 镜像及项目依赖。
- 本地没有占用 `HTTP_PORT`（默认 `8080`）的服务。

## 启动服务

在仓库根目录执行：

```bash
cp .env.example .env
```

编辑 `.env`，至少替换 `POSTGRES_PASSWORD`、`JWT_SECRET_KEY` 和 `INTERNAL_API_TOKEN`。密码应为 URL-safe 字符；`JWT_SECRET_KEY` 在非本地环境中至少使用 32 字节随机值。开发环境可以保持 `APP_ENV=development`，但也应使用唯一凭据。

启动并查看状态：

```bash
docker compose up --build -d
docker compose ps
```

`migrate` 成功结束后，行情服务和后端才会继续启动。可以跟踪启动日志：

```bash
docker compose logs -f backend
```

首次启动还会自动运行一次证券主数据 bootstrap。查看其状态或日志：

```bash
docker compose ps security-bootstrap
docker compose logs -f security-bootstrap
```

如果行情 provider 在启动期间暂时不可用，bootstrap 会独立重试，不会阻塞 Web；需要手工重跑时执行：

```bash
docker compose run --rm security-bootstrap
```

可在 `.env` 中通过 `SECURITY_BOOTSTRAP_TIMEOUT_SECONDS` 和
`SECURITY_BOOTSTRAP_RETRY_INTERVAL_SECONDS` 调整请求超时与重试间隔；默认
bootstrap 请求超时为 180 秒，覆盖行情服务 TDX 证券列表的 60 秒超时、AKShare
完整批次的 90 秒超时和收尾开销。

## 检查健康状态

```bash
curl -i http://localhost:8080/health
curl -i http://localhost:8080/api/health
curl -i http://localhost:8080/api/v1/health
```

三条路径分别检查 Nginx、Next.js 和后端。后端健康响应会探测 PostgreSQL 和 Redis；依赖不可用时返回 HTTP `503`。

## 创建管理员

后端健康后只需执行一次：

```bash
docker compose exec backend python -m app.cli bootstrap-admin \
  --username admin --email admin@example.com
```

命令会读取两次管理员密码。用户名至少 3 个字符，密码至少 8 个字符。若数据库中已有管理员，命令会以 `ADMIN_ALREADY_EXISTS` 失败，不会覆盖已有账号。

打开 <http://localhost:8080/login> 登录。访问令牌返回在 JSON 中，刷新令牌只设置为 HttpOnly Cookie，浏览器不需要也不应该读取刷新令牌内容。

## 邀请注册

管理员登录后，使用访问令牌创建邀请。下面的示例只展示请求形状：

```bash
curl -sS -X POST http://localhost:8080/api/v1/auth/invitations \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <admin_access_token>' \
  -d '{"email":"user@example.com"}'
```

成功响应中的 `data.token` 只返回这一次。把它放入注册地址的 fragment：

```text
http://localhost:8080/register#token=<invitation_token>
```

受邀用户也可以直接调用 API：

```bash
curl -sS -X POST http://localhost:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"invite_token":"<invitation_token>","username":"new-user","password":"a-password-at-least-8"}'
```

注册接口创建账号但不返回登录会话；前端会随后调用登录接口。邀请 token 是一次性的，过期、已接受或与邀请邮箱不匹配时会失败。

## 停止与数据

```bash
docker compose down
```

该命令停止容器但保留 `postgres_data` 和 `redis_data`。只有确认要清空本地数据库与缓存时才执行：

```bash
docker compose down -v
```

## 常见问题

### 后端没有启动

先查看迁移和依赖日志：

```bash
docker compose logs migrate
docker compose logs market-data-service
docker compose logs backend
```

确认 `.env` 中的 `APP_ENV`、`POSTGRES_PASSWORD`、`JWT_SECRET_KEY`、`INTERNAL_API_TOKEN` 和 `NODE_ENV` 都存在。生产环境还要确认刷新 Cookie 已配置为安全模式。

### 日线读取返回 provider 错误

行情查询接口的 `adjust` 默认值是 `qfq`，而默认 TDX client 只有未复权原始日线。没有配置复权因子转换器时，请使用 `adjust=none`，并阅读[行情数据说明](market-data.md)了解数据口径。
