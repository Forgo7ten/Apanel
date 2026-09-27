# 行情数据中枢

`market-data-hub` 是 Apanel 唯一的外部行情数据边界。Backend、Worker 和 Scheduler 不直接依赖 TDX、eltdx、AKShare 或其他公开数据源；它们只通过 Hub 的内部 HTTP API 触发同步，并从共享 PostgreSQL 读取规范化后的公共行情事实。

## Backend 与 Hub 的职责分界

- Hub 负责外部 Provider、格式归一化、共享行情事实持久化与 provider revision/交易日 actual validation。
- Backend 负责 Watch/Alert 需求汇总、指标/状态计算、用户设置、通知语义与调度策略。
- 两者共享 PostgreSQL 表，但 **只有 Backend Alembic** 负责 schema migration；Hub 不能维护第二套迁移历史。
- Hub sync response 保留 item 级成功/失败；Backend 负责 chunk、Celery retry policy 和是否继续后续指标/提醒步骤。

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

证券 universe 仍以 eltdx 为主：eltdx 失败或返回空批次时才使用 AKShare 完整批次 fallback。对于疑似受到 TDX 代码表字段宽度截断的 ETF 名称，Hub 会 best-effort 读取 AKShare 完整批次，并且只在 market/type 都一致的同一 symbol 上采用更长的显示名称；不会借此增加、删除或替换 eltdx 的证券 universe。名称补全失败时仍返回 eltdx 批次。

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
    ["sh600519", "sz000001", ...],
    period="day",
    adjust="qfq" or "none",
    count=target_count,
)
```

因此 QFQ 使用 TDX `0x052d` 主站服务端复权结果。Apanel 已删除旧的本地 `apply_qfq` 近似算法，不再用 `0x000f` corporate-action records 自行重建默认 QFQ。

`KlineBar.volume_lots` 写入 Apanel `DailyBar.volume`，`amount` 写入成交额。Hub 会校验返回的 `adjust_mode` 与请求一致。Quote 使用 `get_snapshots([...])` 原生批量能力；Daily Bar 正常 EOD 使用小 `count`，历史 bootstrap 使用需求驱动的 `lookback_bars`，不再为单日同步遍历固定 64 页。

Hub 还保存每只证券的 qfq corporate-action revision hash。revision 变化时会重新获取已有 coverage 对应的 qfq 历史，并在同步 item 中返回 `history_rebased/changed_from`，Backend 从变化起点重建指标/状态。

技术指标仍属于 Backend。MA、Projected MA、RSI、KDJ、BOLL、MACD 都按 `qfq` / `none` 独立物化；盘中 Quote 不参与日线指标重算。正常 EOD 按目标日期请求一个受限 count，再在 Hub 端过滤目标范围；首次 bootstrap 按 `lookback_bars` 拉取需求驱动的历史长度。

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

eltdx 的 TDX 7709 使用原生 TCP；本项目不通过运行时代理环境变量转发行情请求。

## 批量与失败隔离

Quote 通过 eltdx `get_snapshots([...])` 原生批量获取；Daily Bar 通过批量 codes + `count` 获取；Dividend 等无原生批量能力的路径使用有界并发。Backend 在调用 Hub 前再按 `MARKET_DATA_SYNC_BATCH_SIZE` chunk，因此不会把用户监控 universe 直接塞进一个无限大请求。

同步结果保留逐证券 `SyncItemResult`。单只证券停牌、无目标 bar 或业务校验失败不会抹掉同批其他证券的成功结果；transport/provider 不可用等 retryable failure 才会驱动 Celery 重试策略。

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
- `POST /internal/calendar/validate`

读取接口：

- `GET /internal/quotes/{symbol}`
- `GET /internal/daily-bars/{symbol}`
- `GET /internal/dividends/{symbol}`

共享 DB 还包含 `market_data_adjustment_state` 与 `trading_calendar`。前者保存 opaque provider revision；后者保存官方年度 expected open/close 与实际 validation。共享 schema 仍只有 `backend/alembic` 一套 migration owner，Hub ORM 必须与其保持一致。

## 交易日历

Future schedule 不能靠 eltdx 历史 K 线猜测。数据库中的 annual seed 负责官方未来休市安排；当前迁移内置 2025/2026 seed。对已经发生的日期，Hub 可调用 eltdx workday capability 写入 `actual_open`。官方预期与实际校验冲突时记录 `UNKNOWN`，Worker 不会退化成 Mon-Fri 猜测。

每个新年度应根据上交所、深交所、北交所官方休市通知更新 seed/导入数据。若三所安排出现差异，应按 market 拆分，而不是强行归并。

## 可用性与授权

- TDX 公网主站仍可能临时不可达，Hub 会把 eltdx transport/timeout 错误映射成稳定的 Provider 错误语义。
- AKShare 仅是 Security Master fallback，不是 Quote/DailyBar/Dividend 的第二来源。
- 日线在 v1 中保持 single-provider pinned，不允许按天混用不同来源。
- `electkismet/eltdx` 当前根许可证为 Research-Only；正式商业/生产部署前必须确认授权边界。
