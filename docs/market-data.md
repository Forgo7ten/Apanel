# 行情数据中枢

`market-data-hub` 是 Apanel 唯一的外部行情数据边界。Backend、Worker 和 Scheduler 不直接依赖 TDX、eltdx、AKShare 或其他公开数据源；它们只通过 Hub 的内部 HTTP API 触发同步，并从共享 PostgreSQL 读取规范化后的公共行情事实。

## v1 Provider 范围

第一版只包含两个 Provider：

| Provider | 上游 | Security Master | Quote | Daily Bar | Dividend |
| --- | --- | ---: | ---: | ---: | ---: |
| `eltdx` | TDX 7709 | 是 | 是 | 是 | 是 |
| `akshare` | AKShare HTTP APIs | fallback only | 否 | 否 | 否 |

不在 v1 中接入 Cninfo、Tencent、Sina、Eastmoney 或其他 Provider。后续扩展必须继续留在 Hub 内，不得在 Backend 恢复外部 Provider 抽象。

## Capability Provider 架构

Hub 不再要求每个 Provider 实现一个“大而全”的 `MarketDataProvider`。内部使用独立 capability contracts：

- `SecurityMasterProvider`
- `QuoteProvider`
- `DailyBarProvider`
- `DividendProvider`

`ProviderRouting` 在应用启动时解析并校验每个 capability 的实际 Provider。v1 中 Quote、Daily Bar、Dividend 都固定到 `eltdx`；Security Master 使用 eltdx 主源 + AKShare 整批 fallback。

证券 fallback 采用 `FIRST_SUCCESS_WHOLE_BATCH` 语义：eltdx 成功返回完整批次时不会调用 AKShare；eltdx 失败或返回空批次时才尝试 AKShare。两者的部分结果不会拼接。

## eltdx 生命周期

Hub 固定依赖 `eltdx==3.2.2`。一个进程只创建一个 `TdxClient`，由 FastAPI lifespan 持有；构造、主站探测、连接和关闭都在线程池中执行，避免阻塞 event loop。eltdx 自己负责 7709 host ranking、连接池、心跳和底层 failover；Apanel 不再维护第二套旧 TDX client 连接/重试实现。

默认不配置固定主站。`ELTDX_HOSTS` 为空时使用 eltdx 自带的 host set 与 ranking；需要固定节点时才显式设置 CSV/JSON host:port 列表。

## 证券范围

Hub 从 eltdx `client.codes.all("sh"/"sz"/"bj")` 获取代码表，只保留：

- `category == "a_share"` → `STOCK`
- `category == "etf"` → `ETF`

最终范围与 PRD 一致：沪市、深市、北交所普通股票和 ETF。指数、B 股、债券等其他分类不会进入 `securities`。

## 当前价

报价使用 eltdx `client.quotes.get_snapshots(...)`。`last_price`、`change`、`change_pct` 转换成 Apanel `Quote`。

eltdx 基础快照公开的是 `time_raw`，Hub 不把它猜测成完整交易所时间。v1 的 `Quote.timestamp` 明确定义为 Hub 观察到该快照的 UTC 时间。盘中刷新频率仍由 Backend Scheduler 按 PRD 的 5～10 分钟节奏控制。

## 日线与复权

领域层只接受：

- `qfq`
- `none`

两种模式都直接调用 eltdx：

```python
client.bars.get(
    full_code,
    period="day",
    adjust="qfq" or "none",
    all_pages=True,
)
```

因此 QFQ 使用 TDX `0x052d` 主站服务端复权结果。Apanel 已删除旧的本地 `apply_qfq` 近似算法，不再用 `0x000f` corporate-action records 自行重建默认 QFQ。

`KlineBar.volume_lots` 写入 Apanel `DailyBar.volume`，`amount` 写入成交额。Hub 会校验返回的 `adjust_mode` 与请求一致，并按交易日排序、过滤请求日期范围。

技术指标仍属于 Backend。MA、RSI、KDJ、BOLL、MACD 等继续基于最近完成交易日的统一日线口径计算，盘中 Quote 不参与日线指标重算。

## 现金分红

现金分红使用 eltdx `client.corporate.capital_changes()`。只处理 `category == 1` 的权息记录。

TDX `c1_value` 表示每 10 股现金分红，而 Apanel canonical contract 定义：

```text
Dividend.cash_amount = 每股现金金额
```

因此 Hub 写入前执行：

```text
cash_per_share = c1_value / 10
```

零现金记录和非现金分红 category 会被忽略；同一日期多条现金分红在 Adapter 内聚合。Backend 的 TTM Dividend Yield 继续按“过去 12 个月实际每股现金派息总额 / 当前价格”计算。

## AKShare fallback

AKShare 只负责 Security Master fallback：

- 股票：`stock_info_a_code_name()`
- ETF：`fund_etf_spot_em()`

Adapter 保留现有保护：lazy import、完整批次校验、最低记录数、单 in-flight worker、总 timeout、关闭时 drain。eltdx security master 成功时不会 import/call AKShare。

HTTP proxy 环境变量只对 AKShare 等 HTTP Provider 有意义；eltdx 的 TDX 7709 是原生 TCP，本项目不宣称它会通过 `HTTP_PROXY` / `HTTPS_PROXY` 转发。

## 同步与持久化

Hub 负责写入共享公共行情事实：

- `securities`
- `daily_bars`
- `quote_snapshots`
- `dividend_events`

内部同步接口：

- `POST /internal/sync/securities`
- `POST /internal/sync/daily`
- `POST /internal/sync/quotes`
- `POST /internal/sync/dividends`

读取接口：

- `GET /internal/quotes/{symbol}`
- `GET /internal/daily-bars/{symbol}`
- `GET /internal/dividends/{symbol}`

第一版架构重构不增加 provenance schema，也不改变指标/状态/提醒表。后续如果需要记录 provider provenance，应独立设计 DB migration，而不是把 Provider SDK model 直接写入公共表。

## 出站代理

根目录 `.env` 可配置：

```dotenv
MARKET_DATA_HTTP_PROXY=
MARKET_DATA_HTTPS_PROXY=
MARKET_DATA_ALL_PROXY=
MARKET_DATA_NO_PROXY=
```

这些值只注入 `market-data-hub`。`NO_PROXY` 始终包含 Compose 内部服务名，避免内部 HTTP 请求绕过容器网络。

## 可用性与授权

- TDX 公网主站仍可能临时不可达，Hub 会把 eltdx transport/timeout 错误映射成稳定的 Provider 错误语义。
- AKShare 仅是 Security Master fallback，不是 Quote/DailyBar/Dividend 的第二来源。
- 日线在 v1 中保持 single-provider pinned，不允许按天混用不同来源。
- `electkismet/eltdx` 当前根许可证为 Research-Only；正式商业/生产部署前必须确认授权边界。
