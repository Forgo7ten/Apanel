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

需要 Node.js latest/current。安装锁定依赖：

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

## 官方年度交易日历维护

每年在三所官方休市公告发布并逐项核对后，使用 Backend CLI 为已有库导入完整 `CN` 年度日历。JSON 清单字段和冲突处理规则见 [行情数据](market-data.md)。先运行 `python -m app.cli import-trading-calendar --file /secure/path/official.json` 预览，再附加 `--apply` 写入；正式订正已有年度预期须在核实后加 `--replace-existing`。文件必须是 Backend 进程可读取的绝对路径。此命令只操作现有日历表，不新增数据库模型或迁移；不能把未经确认的新年工作日当成未来交易所开市日，也不要将临时清单或操作日志加入 `docs/`。

## 数据库初始化与结构检查

仅在**全新空 PostgreSQL 数据库**首次启动时，Compose 的 `migrate` 服务执行唯一 `0001_fresh_schema` 初始化：

```bash
docker compose up migrate
```

在完整栈运行后，可以在后端容器中查询版本并检查是否位于唯一 head：

```bash
docker compose exec backend alembic current
docker compose exec backend alembic upgrade head
```

初始化使用容器内的 `DATABASE_URL`，不要把 `alembic.ini` 中的占位 URL 当作真实连接地址。当前只维护全新建库基线；调整表结构时必须同步更新模型和基线 DDL，并在独立空库验证 `alembic upgrade head`、`alembic check`。

## CI

`.github/workflows/ci.yml` 在 push 到 `main` 和 pull request 时运行四个 job：

- Backend：Python 3.12、PostgreSQL 16、pytest、Ruff、`alembic upgrade head`。
- Market Data Hub：Python 3.12、pytest、Ruff。
- Frontend：Node latest、`npm test`、lint、typecheck、production build。
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

**重要：只支持新建空 PostgreSQL 数据库**，唯一 Alembic revision 为 `0001_fresh_schema`；旧库升级、旧明文迁移和数据库 downgrade 均不支持。不要通过手工删除旧表绕过版本失败。

## 文档维护

`README.md` 和 `docs/` 只记录当前可运行的能力、架构、接口、安全与运维流程，应随对应实现同步更新。测试日志、基准输出、临时 Review 和阶段性结论保存在仓库外，不进入 `docs/` 或 Git 提交。产品约束变化时同步更新正式设计来源及对应运行文档。

## 修改边界

- HTTP controller 只做解析、依赖注入和响应映射；业务规则放在 service/domain 层。
- Watch/Alert Service 若需要启动历史数据准备，只依赖 `SecurityBootstrapScheduler` 协议；Celery adapter 位于 task 层，测试可注入 recorder/no-op scheduler，禁止 Service 直接 import task object。
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

### PostgreSQL 数据库回归

`backend/tests/test_alert_postgres_concurrency.py` 与 `market-data-hub/tests/test_analysis_stream_postgres.py` 需要 `APANEL_TEST_POSTGRES_URL` 指向可丢弃数据库；测试会创建/删除表，严禁传入业务数据库。分别验证跨进程替代的行锁语义、设置首写竞争、同步 fencing、行情/dirty 标记原子回滚及相同输入不重复写入。

批量 Indicator/State Repository 使用有界 UPSERT RETURNING，返回 ORM 对象时刷新 identity map。Ruff、全量 Python/Node 测试、空库 `upgrade head` / `alembic check` 与禁止 downgrade 的测试均属于发布检查，不把 SQLite 通过当作 PostgreSQL 并发验证。

`test_analysis_recovery_postgres.py`、`test_fresh_baseline_postgres.py` 分别覆盖事务/恢复和新库建表、旧 revision 拒绝、禁止 downgrade。CI 使用独立 PostgreSQL 服务运行测试与 `alembic check`。

### 可复现分析性能基线

`backend/scripts/benchmark_analysis.py` 使用独立空数据库生成合成数据，拒绝已有表的数据库，不执行 DROP。数据库名称必须以 `apanel_bench_` 或 `apanel_remaining_bench` 开头。示例（从 backend 执行）：

```bash
APP_ENV=test APANEL_BENCHMARK_POSTGRES_URL="$DEDICATED_EMPTY_BENCHMARK_URL" \
  python scripts/benchmark_analysis.py --symbols 100 --bars 400 --output /tmp/apanel-analysis-benchmark.json
```

脚本覆盖 qfq/none 的冷物化、次日增量和同日幂等重放；结果写到仓库外的临时目录，**不得提交测试 JSON 或跑分报告**。它统计耗时、SQL 数、实际存储行数和 Python 进程峰值 RSS。它不调用真实行情源、Alert 或飞书，因此不是完整 EOD 端到端耗时；生成数据与初始建表不计入阶段耗时。`per_stream_p95_ms` 表示这一轮股票/复权序列之间的分位数，不是多轮负载压测的服务 SLA。

### 前端 Query Key 一致性

所有 TanStack Query 的读取、更新、失效、取消与移除都统一使用 `frontend/src/lib/query-keys.mjs`。Watch 明细完整键包含表 ID 和复权口径：`queryKeys.watchTable(id, adjustment)`。当同一业务变更会影响两个复权口径时使用 `queryClient.setQueriesData({ queryKey: queryKeys.watchTablePrefix(id) }, updater)`，**不要**把 `watchTablePrefix` 传给单值 `setQueryData`，否则会生成没有消费者的幽灵缓存。删除表格使用同一前缀移除所有复权明细，历史通知使用 `queryKeys.notificationHistory()` 与后端游标分页。新增查询键/写入路径需运行 `frontend/tests/query-key-regressions.test.mjs`。
