# 行情数据

`market-data-service` 是独立的 FastAPI 服务。它把外部 provider 的原始记录转换为统一 domain 对象，再通过 repository 写入共享 PostgreSQL，供内部读取接口使用。

## 支持的领域对象

| 对象 | 关键字段 | 存储表 |
| --- | --- | --- |
| `Security` | 六位 `symbol`、`name`、`market`、`exchange`、`security_type` | `securities` |
| `DailyBar` | 交易日、open/high/low/close、volume、amount、`adjustment` | `daily_bars` |
| `Quote` | price、change、change_percent、UTC `timestamp` | `quote_snapshots` |
| `Dividend` | 事件日期、`cash_amount` | `dividend_events` |

市场只接受 `SH`、`SZ`、`BJ`；证券类型只接受 `STOCK` 和 `ETF`。代码可写成裸六位代码（如 `600519`），也可写成 `SH.600519`、`SH:600519`、`600519.SH` 或 `600519:SH`。规范化后存储为六位数字，市场由前缀或代码规则推断。

价格使用 `Decimal`，交易日使用无时区 `date`，报价时间必须是带时区的时间并在存储前转换为 UTC。OHLC 必须为正数，`high`/`low` 必须包住 open、close，成交量和金额不能为负数。

## Provider 边界

服务通过 `MarketDataProvider` 抽象获取：

- `get_symbols()`：证券元数据。
- `get_quote(symbol)`：单证券报价。
- `get_daily_bars(symbol, start, end, adjustment)`：指定日期和复权模式的日线。
- `get_dividends(symbol)`：现金分红事件。

当前 `ProviderRegistry` 只注册 `tdx`。`TDXProvider` 把阻塞的 client 调用放到 worker thread，并使用超时、重试和多节点连接策略；无效记录、超时、provider 不可用和未配置会转换为稳定的 provider 错误类型。

证券主数据另有 `SecurityMasterProvider` seam。组合 provider 每次先调用 TDX
`get_symbols()`；TDX 返回非空完整批次时绝不调用 fallback，只有 TDX 失败或返回空批次才调用 AKShare。
AKShare 通过 `stock_info_a_code_name()` 获取股票、通过 `fund_etf_spot_em()` 获取 ETF；两个源都必须分别完整读取、字段校验和数量阈值校验通过后，才合成一个批次。股票使用
`code/name` 并标记为 `STOCK`，ETF 使用 `代码/名称` 并标记为 `ETF`，市场由现有领域规则推断为
`SH`、`SZ` 或 `BJ`。空源、缺列、空名称、非法代码、重复冲突或低于最低数量会让整个 AKShare
源失败，不会把 TDX 部分结果与 AKShare 结果混合，也不会写入半批数据。

AKShare 调用是阻塞函数，适配器会在 worker thread 中以一个独立总超时执行，并保证超时后的后台线程
结束前不会启动重叠航班；每次调用前会清理上游函数提供的 `cache_clear`，避免长驻进程复用永久旧快照。
AKShare 依赖固定为 `akshare==1.18.97`，仅在 fallback 实际被调用时懒加载；TDX 成功路径不会导入或调用
AKShare。股票最低默认 `1000` 条、ETF 最低默认 `1` 条，可通过配置调整。两边都失败时只返回稳定的
`PROVIDER_UNAVAILABLE`/`ProviderUnavailableError` 语义，不向响应暴露上游正文或 URL。

AKShare fallback 只负责证券主数据。报价、日线和现金分红始终继续使用原 TDX provider；AKShare
不会注册为全局 `MARKET_DATA_PROVIDER` 替代物。配置见[配置参考](configuration.md)中的
`SECURITY_MASTER_FALLBACK_PROVIDER`、`AKSHARE_SECURITY_TIMEOUT_SECONDS` 和最低数量设置。

## 出站代理

需要通过代理访问外部 provider 时，在根目录 `.env` 中设置专用于行情服务的变量：

```dotenv
MARKET_DATA_HTTP_PROXY=http://host.docker.internal:your-proxy-port
MARKET_DATA_HTTPS_PROXY=http://host.docker.internal:your-proxy-port
MARKET_DATA_ALL_PROXY=
MARKET_DATA_NO_PROXY=
```

Compose 只会把它们映射为 `market-data-service` 的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`；PostgreSQL、Redis、backend、worker、scheduler 和前端不会收到这些代理变量。代理变量为空时不启用出站代理。`NO_PROXY` 会自动包含 `localhost`、`127.0.0.1` 以及 Compose 内部服务名 `postgres`、`redis`、`backend` 和 `market-data-service`，避免服务间请求绕行代理。

Compose 为行情容器提供跨 Linux 的 `host.docker.internal`（`host-gateway`）解析。如果代理运行在宿主机，必须监听 Docker 可达的接口；仅监听宿主机 `127.0.0.1` 时，容器无法连接。代理 URL 中的用户名和密码属于敏感值，请仅保存在被 Git 忽略的 `.env` 中；行情服务不会将代理值写入应用日志。

默认依赖为 `pytdx==1.72`。默认 TDX 节点是：

```text
119.147.212.81:7709
101.227.73.20:7709
```

可通过 `TDX_SERVERS` 或 `TDX_SERVER_LIST` 覆盖，格式可以是逗号分隔字符串或 JSON 数组。

## 复权口径

领域层只接受两种模式：`qfq` 和 `none`。

默认 TDX client 的 `get_security_bars` 返回未复权日线。只有注入 `qfq_transformer` 并且转换后的每条记录显式标记为 `adjustment="qfq"` 时，才允许标记前复权。没有转换器时请求 `qfq` 会返回 provider 不支持/不可用错误；不能把原始未复权记录伪装成 `qfq`。

需要使用默认 TDX client 时，日线同步和读取应显式选择 `none`：

```json
{
  "symbols": ["600519"],
  "start": "2026-09-01",
  "end": "2026-09-24",
  "adjustment": "none"
}
```

HTTP 读取接口的 `adjust` 默认是 `qfq`，同步请求体的 `adjustment` 默认也是 `qfq`；这是 API 合同的默认值，不代表当前默认 provider 已具备复权能力。

## 同步流程

### 证券元数据

`POST /internal/sync/securities` 调用 provider 获取证券列表，先在 domain 层校验并去重，再通过 PostgreSQL 以一个事务写入 `securities`。证券主数据是整批原子语义：任一记录校验失败或持久化失败都会让整个批次失败，不返回成功记录，也不会写入部分数据。

Compose 包含一个一次性 `security-bootstrap` 服务。执行 `docker compose up --build -d` 时，
它会等待 `market-data-service` 健康后调用上述接口，并在 provider 暂时不可用时持续重试；
后端、前端、worker 和 scheduler 不依赖该服务，因此行情 provider 故障不会阻塞 Web 启动。
可用以下命令查看执行状态和日志：

```bash
docker compose ps security-bootstrap
docker compose logs -f security-bootstrap
```

如果首次运行失败或需要手工补齐主数据，可在项目根目录重新执行：

```bash
docker compose run --rm security-bootstrap
```

重试间隔和 HTTP 超时可通过 `.env` 中的
`SECURITY_BOOTSTRAP_RETRY_INTERVAL_SECONDS` 与 `SECURITY_BOOTSTRAP_TIMEOUT_SECONDS` 调整。
默认 bootstrap HTTP 超时为 180 秒，覆盖行情服务 TDX 证券列表默认 60 秒、AKShare 完整批次默认 90 秒及
AKShare worker 最多 5 秒的收尾，并保留少量 HTTP 开销余量。
内部 token 只注入 bootstrap 容器的环境变量，不会写入命令输出或日志。

### 日线

`POST /internal/sync/daily` 对每个规范化后的证券执行：

1. 调用 provider 获取日期范围和复权模式的日线。
2. 校验 provider 返回的 symbol、adjustment、OHLCV 和日期。
3. 去除同一证券、交易日和复权模式的重复记录。
4. 先尝试批量 upsert；发生可隔离的完整性错误时按证券重试。
5. 连接/事务等系统性数据库错误直接作为服务错误返回，不伪装成单证券失败。

响应的 `items` 为每个证券提供 `status`、`fetched`、`persisted` 和可选错误；部分失败时 `success=false`，但仍返回可用的逐项结果。

日线 upsert 依赖证券已经存在。实际部署应先同步证券元数据，再同步日线。服务启动不会自动从 TDX 拉取数据。

## 读取接口

行情服务当前提供：

- `GET /internal/quotes/{symbol}`：读取最新已保存报价。
- `GET /internal/daily-bars/{symbol}?start=&end=&adjust=`：读取指定日期和复权模式的已保存日线。
- `GET /health`：检查 PostgreSQL 和 Redis。

报价 provider 能力和 `quote_snapshots` repository 已存在，但当前没有公开报价写入/sync HTTP 路由；因此读取不到尚未持久化的报价是预期行为。

## 存储与一致性

行情表与认证表共用 Compose PostgreSQL，但行情 service 自己创建 session factory 和 repository。所有公开写入操作使用独立事务；冲突键通过 PostgreSQL `ON CONFLICT DO UPDATE` 处理，重复同步是幂等的。

Redis client 当前用于健康探针和基础设施连接边界，行情查询不会把数据默认写入 Redis 缓存。数据库与 Redis 数据分别保存在 `postgres_data` 和 `redis_data` 命名卷。

## 可用性限制

- TDX 公网节点可能不可达，`pytdx` 及其数据口径也需要部署方自行验证。
- AKShare 仅作为证券主数据的默认备用 provider；修改 `SECURITY_MASTER_FALLBACK_PROVIDER` 为 `none` 可关闭，
  修改 `MARKET_DATA_PROVIDER` 为未注册名称仍会导致启动失败。
- 默认 Compose 不传递 TDX 细化调参变量；要修改节点、连接重试或分页上限，需要显式扩展 Compose 环境映射。
- 行情 API 是内部接口，没有由 Nginx 发布给外部浏览器。
