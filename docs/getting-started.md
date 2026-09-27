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

如果要测试/使用飞书 Webhook 加密存储，建议同时生成 `APP_SECRETS_KEY`。它必须是 URL-safe base64 编码的 32-byte key，例如：

```bash
python - <<'PY'
import base64, secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
```

生产环境 `APP_SECRETS_KEY` 必填，并且只应提供给 Backend/Worker。

启动并查看状态：

```bash
docker compose up --build -d
docker compose ps
```

`migrate` 成功结束后，行情数据中枢和后端才会继续启动。可以分别跟踪 API、任务与行情中枢日志：

```bash
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f market-data-hub
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
bootstrap 请求超时为 180 秒，覆盖行情数据中枢 TDX 证券列表的 60 秒超时、AKShare
完整批次的 90 秒超时和收尾开销。

## 启动后的服务状态

`docker compose ps` 中，`migrate` 和 `security-bootstrap` 属于一次性任务，成功完成后显示 exited/completed 属于正常状态；长期运行的服务应包括 `postgres`、`redis`、`market-data-hub`、`backend`、`worker`、`scheduler`、`frontend` 与 `nginx`。

如果 Web 可以打开但指标一直没有数据，优先检查 `worker` 与 `security-bootstrap`，而不是反复刷新页面，因为指标/状态 GET 接口不会在读取时触发补算。

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

## 首次业务使用

1. 登录后创建 Watch Table。
2. 添加证券。API 会立即返回表格 membership，并异步 enqueue 历史 bootstrap；数据准备期间部分指标可以暂时 unavailable。
3. 新建动态列时，`NUMBER/DELTA` 对多值指标选择具体 field；`STATUS` 选择具体状态。
4. 在设置中选择 `qfq` 或 `none`。该设置影响 Watch/详情默认读取口径，不会改写已经创建的 AlertRule。
5. 配置飞书 Webhook 后，设置接口只显示“已配置”，不会再次返回 URL 明文。
6. 可以从指标/状态详情直接创建监听，也可以在通知页手工创建 VALUE/STATE 规则。

如果数据库来自旧版本且 `user_settings` 中已经保存过 Webhook 明文，在配置 `APP_SECRETS_KEY` 后执行：

```bash
docker compose exec backend python -m app.cli migrate-user-secrets
```

迁移命令把 legacy Webhook 搬入加密 `user_secrets` 表；执行后应确认旧 JSON 中已无明文。

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
docker compose logs market-data-hub
docker compose logs backend
```

确认 `.env` 中的 `APP_ENV`、`POSTGRES_PASSWORD`、`JWT_SECRET_KEY`、`INTERNAL_API_TOKEN` 和 `NODE_ENV` 都存在。生产环境还要提供 `APP_SECRETS_KEY`，并确认刷新 Cookie 已配置为安全模式。

### 日线读取返回 provider 错误

行情查询与分析支持 `adjust=qfq` 和 `adjust=none`。两种口径都由 Market Data Hub 直接请求 eltdx/TDX 对应序列；正常 EOD 使用小窗口增量读取，首次监控或参数需求扩大时由 bootstrap 拉取足够历史。若返回 adjustment mismatch，Hub 会拒绝写入而不是静默混用口径。详见[行情数据说明](market-data.md)。

### 指标一直显示 unavailable

先查看 worker 是否收到证券 bootstrap：

```bash
docker compose logs worker | grep -E 'bootstrap|pipeline'
```

确认该证券已经同步足够的 qfq/none 历史、用户所需自定义参数已进入 calculation demand，并检查当天是否为交易日/停牌。GET 指标/状态接口是只读的，不会通过“打开页面”偷偷补算历史。
