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

`POST /internal/sync/securities` 调用 provider 获取证券列表，先在 domain 层校验并去重，再通过 PostgreSQL upsert 写入 `securities`。同一批次中的坏记录会形成单项失败结果，其他记录继续处理。

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
- 当前没有 AKShare、同花顺或其他已注册 provider；修改 `MARKET_DATA_PROVIDER` 为未注册名称会导致启动失败。
- 默认 Compose 不传递 TDX 细化调参变量；要修改节点、连接重试或分页上限，需要显式扩展 Compose 环境映射。
- 行情 API 是内部接口，没有由 Nginx 发布给外部浏览器。
