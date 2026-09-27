# 开发指南

仓库包含三个独立的应用目录：`backend`、`market-data-hub` 和 `frontend`。两个 Python 项目都使用 `app` 作为包名，因此 Python 命令应在对应目录中运行，避免导入到另一套代码。

## 后端

需要 Python 3.12 或更高版本。创建虚拟环境并安装开发依赖：

```bash
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

常用命令：

```bash
python -m pytest
ruff check .
```

开发服务器可在已配置 `DATABASE_URL` 和 `REDIS_URL` 的环境中启动：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 行情数据服务

行情服务同样需要 Python 3.12 或更高版本：

```bash
cd market-data-hub
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

常用命令：

```bash
python -m pytest
ruff check .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

直接运行时，`DATABASE_URL`、`REDIS_URL` 和 `INTERNAL_API_TOKEN` 由 Pydantic Settings 读取；TDX provider 选项见[配置参考](configuration.md)。

## 前端

需要 Node.js 20。安装锁定依赖：

```bash
cd frontend
npm ci
```

项目脚本来自 `frontend/package.json`：

```bash
npm run dev
npm test
npm run lint
npm run typecheck
npm run build
```

浏览器 API 基路径由 `NEXT_PUBLIC_API_BASE_URL` 读取，默认是 `/api/v1`。在 Compose 中通过 Nginx 使用默认同源路径；直接运行前端时必须提供一个浏览器可访问的后端基路径，并自行处理跨域和凭据策略。

## 数据库迁移

Compose 启动时由 `migrate` 服务自动执行：

```bash
docker compose up migrate
```

在完整栈运行后，也可以在后端容器中查看或执行迁移：

```bash
docker compose exec backend alembic current
docker compose exec backend alembic upgrade head
```

迁移使用容器内的 `DATABASE_URL`，不要把 `alembic.ini` 中的占位 URL 当作真实连接地址。新增迁移时在 `backend` 目录执行 Alembic 命令，并确保模型元数据和迁移脚本同步。

## CI

`.github/workflows/ci.yml` 在 push 到 `main` 和 pull request 时运行四个 job：

- Backend：Python 3.12、PostgreSQL 16、pytest、Ruff、`alembic upgrade head`。
- Market Data Hub：Python 3.12、pytest、Ruff。
- Frontend：Node 20、`npm test`、lint、typecheck、production build。
- Compose：使用测试凭据执行 `docker compose config -q`。

CI 不访问真实行情 provider；真实 eltdx/AKShare smoke test 应放在 staging/manual gate，避免把第三方网络可用性当成代码单元测试。

## 提交前建议检查

```bash
# 根目录
git diff --check
docker compose config -q

# 确认 Alembic 只有一个 head
cd backend && alembic heads
```

涉及 schema 的变更还应在 PostgreSQL 上执行 `alembic upgrade head`；涉及 Hub shared ORM 的变更必须同时更新/验证 Backend migration owner 与 Hub persistence contract。

## 文档维护

`README.md` 与 `docs/` 描述当前可运行实现，应随代码一起更新并进入版本控制。`plan_docs/` 是需求/设计来源与历史规划材料，不应把其中旧的 `market-data-service`、Provider 或 Sprint 状态直接当作当前运行事实；实现状态以当前代码、migration、Compose 和 `docs/` 为准。若产品约束发生变化，应先更新正式 PRD/设计来源，再同步运行文档。

## 修改边界

- HTTP controller 只做解析、依赖注入和响应映射；业务规则放在 service/domain 层。
- Backend 只调用 `MarketDataHubClient`；外部行情 Provider 只能存在于 `market-data-hub/app/providers/`，API controller 不直接调用 eltdx/AKShare。
- 指标只接收标准化输入并返回 `IndicatorResult`；状态只接收 `IndicatorSnapshot`，不要在前端复制计算逻辑。
- 用户身份来自后端当前会话和数据库角色，不接受前端提交的 `user_id` 作为权限依据。
- 变更后优先运行受影响目录的 pytest 和 `ruff check .`，再运行前端相应脚本或 Compose 冒烟检查。

## 测试目录

```text
backend/tests/                 认证、Watch、指标/状态、Alert/Notification、Settings、任务与迁移契约
market-data-hub/tests/         Provider、domain、批量同步、持久化、日历和内部 API
frontend/tests/                认证协调、Watch/详情、Alert、Settings、Notification 与浏览器契约
```
