# 开发指南

仓库包含三个独立的应用目录：`backend`、`market-data-service` 和 `frontend`。两个 Python 项目都使用 `app` 作为包名，因此 Python 命令应在对应目录中运行，避免导入到另一套代码。

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
cd market-data-service
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

## 修改边界

- HTTP controller 只做解析、依赖注入和响应映射；业务规则放在 service/domain 层。
- 外部行情访问通过 `MarketDataProvider`，不要在 API controller 中直接调用 TDX client。
- 指标只接收标准化输入并返回 `IndicatorResult`；状态只接收 `IndicatorSnapshot`，不要在前端复制计算逻辑。
- 用户身份来自后端当前会话和数据库角色，不接受前端提交的 `user_id` 作为权限依据。
- 变更后优先运行受影响目录的 pytest 和 `ruff check .`，再运行前端相应脚本或 Compose 冒烟检查。

## 测试目录

```text
backend/tests/                    认证、健康、指标和状态
market-data-service/tests/        provider、domain、同步、持久化和 API
frontend/tests/                   健康路由、认证逻辑和跨标签页协调
```
