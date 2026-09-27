# 指标与状态

指标和状态的纯计算代码位于 `backend/app/indicators` 与 `backend/app/states`；持久化和 API 编排位于 `IndicatorService`、`StateService`。计算内核不依赖 ORM，但服务层会把结果写入 `indicator_snapshots` / `indicator_states`，并通过证券指标/状态 API 与 Watch 工作台读取。

## 指标输入

指标引擎接受以下形式的序列：

- `Candle` 对象。
- 包含 `open`、`high`、`low`、`close`、可选 `volume` 和 `timestamp` 的 mapping。
- 只包含收盘价的数值序列。

每根 Candle 都要求 `close`；数值会在边界转为有限 `float`，价格必须为正，成交量不能为负。KDJ 还要求每根 Candle 有 `high` 和 `low`。输入序列会被复制和标准化，计算不会修改调用方数据。

序列长度不足、参数不是正整数/正数、数值不是有限数时，会抛出指标域错误，例如 `InsufficientDataError`、`InvalidParameterError` 或 `InvalidInputError`。

## 内置指标

默认注册表由 `create_default_registry()` 创建，名称和别名如下：

| 名称 | 别名 | 默认参数 | 结果值 |
| --- | --- | --- | --- |
| `ma` | `sma` | `period=5` | `value` |
| `projected_ma` | `pma` | `period=5` | `value`、`projected_close` |
| `rsi` | — | `period=14` | `value`、`average_gain`、`average_loss` |
| `kdj` | — | `period=9`、`k_period=3`、`d_period=3` | `rsv`、`k`、`d`、`j` |
| `boll` | `bollinger` | `period=20`、`multiplier=2.0` | `upper`、`middle`、`lower`、`width` |
| `macd` | — | `fast_period=12`、`slow_period=26`、`signal_period=9` | `diff`、`dea`、`histogram` |

实现口径：

- MA 是最近窗口的简单平均；Projected MA 假定下一根收盘价等于最近收盘价，再计算下一窗口平均值。
- RSI 使用 Wilder 平滑平均；全涨、全跌和无变化序列有明确的边界值。
- KDJ 在第一段完整窗口前以 50 作为 K/D 种子；零高低差时 RSV 为 50。
- BOLL 使用窗口总体标准差，`width=(upper-lower)/middle`；中轨为零时 width 为 0。
- MACD 使用首个窗口的 SMA 作为 EMA 种子，`histogram=diff-dea`，要求 `fast_period < slow_period`。

## 指标结果与快照

所有结果都是不可变对象，包含：

- `indicator`：规范化的指标名。
- `parameters`：本次计算使用的参数。
- `values`：有限数值映射。

`IndicatorResult.to_snapshot()` 可转换为：

```json
{
  "indicator": "rsi",
  "parameters": {"period": 14},
  "values": {
    "value": 62.4,
    "average_gain": 1.2,
    "average_loss": 0.7
  }
}
```

`IndicatorRegistry` 通过名称解析实现，不需要在 dispatch 代码中添加分支。内置指标同时实现 `calculate_series()`，历史重建时按单次线性扫描计算整段序列，避免逐日重复计算前缀。自定义指标仍可只实现 `calculate(series, **parameters)`；注册表会保留兼容 fallback 并执行结果合同校验。

持久化按 `qfq` / `none` 分开保存，同一指标类型的多个参数 variant 共用一条交易日 snapshot，通过稳定 `parameter_key` 区分。读取 API 不再隐式重算；数据生成由 bootstrap/EOD/rebase 流程负责。

## 状态输入与引擎

状态引擎消费不可变的 `IndicatorSnapshot`，包括 `indicator`、`values`、可选 `previous_values`、`parameters` 和 `metadata`。快照会统一大小写、复制 mapping 并拒绝非有限值。

`StateEngine.evaluate_state()` 接受当前快照和上一快照，返回 `StateResult`：

- `active`：当前是否满足状态条件。
- `transition`：本次是否从未激活进入激活，供边缘触发提醒使用。
- `status`：`ACTIVE` 或 `INACTIVE`。
- `reason`：缺少上一快照、指标不匹配或输入无效时的安全原因。
- `values`、状态 code、level、metadata：供 UI/提醒层复用。

状态需要上一快照才能确认变化；缺少上一快照时返回非激活且不产生 transition。生产路径由 `StateService` 从数据库读取上一日状态并传入 `previous_active`，因此重启不会丢失 Edge 语义。状态记录包含 adjustment 与 parameter identity；例如不同 MA 周期组合可同时存在而不会互相覆盖。

## 内置状态

默认注册表当前包含以下定义。`edge` 表示 evaluator 以交叉/突破判断边缘；`continuous` 表示每次依据当前与前一快照计算 active。两类状态都会由引擎在从非激活进入激活时最多产生一次 `transition`。

| Code | 指标 | 模式 | Level | 判定 |
| --- | --- | --- | --- | --- |
| `MA_CROSS_UP` | MA | edge | `POSITIVE` | short 上穿 long |
| `MA_CROSS_DOWN` | MA | edge | `NEGATIVE` | short 下穿 long |
| `MA_GAP_NARROWING` | MA | continuous | `WARNING` | short/long 绝对间距缩小 |
| `MA_GAP_EXPANDING` | MA | continuous | `INFO` | short/long 绝对间距扩大 |
| `KDJ_K_CROSS_D_UP` | KDJ | edge | `POSITIVE` | K 上穿 D |
| `KDJ_K_CROSS_D_DOWN` | KDJ | edge | `NEGATIVE` | K 下穿 D |
| `KDJ_ALL_RISING` | KDJ | continuous | `POSITIVE` | K、D、J 同时上升 |
| `KDJ_ALL_FALLING` | KDJ | continuous | `NEGATIVE` | K、D、J 同时下降 |
| `BOLL_WIDTH_NARROWING` | BOLL | continuous | `WARNING` | width 下降 |
| `BOLL_WIDTH_EXPANDING` | BOLL | continuous | `INFO` | width 上升 |
| `BOLL_ALL_RISING` | BOLL | continuous | `POSITIVE` | upper/middle/lower 同时上升 |
| `BOLL_ALL_FALLING` | BOLL | continuous | `NEGATIVE` | upper/middle/lower 同时下降 |
| `BOLL_BREAK_UPPER` | BOLL | edge | `POSITIVE` | 价格向上突破 upper |
| `BOLL_BREAK_LOWER` | BOLL | edge | `NEGATIVE` | 价格向下跌破 lower |
| `MACD_DIFF_CROSS_DEA_UP` | MACD | edge | `POSITIVE` | DIFF 上穿 DEA |
| `MACD_DIFF_CROSS_DEA_DOWN` | MACD | edge | `NEGATIVE` | DIFF 下穿 DEA |
| `MACD_RED_BAR_GROWING` | MACD | continuous | `POSITIVE` | 正 histogram 增长 |
| `MACD_RED_BAR_SHRINKING` | MACD | continuous | `NEGATIVE` | 正 histogram 缩短 |

BOLL 突破状态还需要当前和上一交易日价格；缺少价格时返回 `missing_price`，不会产生提醒。比较使用默认 `tolerance=1e-9`，可在构造 `StateEngine` 或调用时覆盖。

## 计算需求与持久化边界

实际计算需求不是一套全局固定参数，而是 Watch Table 列、enabled VALUE Alert 和 enabled STATE Alert 的并集。新增自定义 RSI/BOLL/MACD 参数或 MA state 周期组合后，bootstrap/EOD 会把对应 variant 纳入计算；`parameter_key` 用于稳定区分同一 indicator type 的多个参数组合。

内置指标使用 `calculate_series()` 线性遍历历史序列；StateService 直接消费已物化 snapshot，不再重复触发 IndicatorService 全历史重算。GET `/indicators*` 与 `/states*` 都是只读读取，缺数据应由后台 bootstrap/EOD 修复，而不是由 HTTP GET 隐式写库。

## Watch / Alert 对同一状态的引用

`STATUS` 列和 STATE Alert 都不会只按“指标类型”模糊匹配。两者都持久化明确的 `state_code` 与参数集合，服务端再生成/使用稳定的 `parameter_key`。例如 `MA5/10` 与 `MA20/60` 的 `MA_CROSS_UP` 是两个独立 state instance，可以同时展示、查询和监听。

Frontend 从指标/状态详情创建 Alert 时会把当前状态的参数与 `adjust_type` 一并带入；已有规则不会因为用户后来修改全局 indicator defaults 或 adjustment 设置而漂移。

## 与产品层的关系

Watch 表格、指标详情和提醒共用同一套持久化快照/状态。用户选择的 qfq/none 只决定当前工作台读取哪条分析序列；已有提醒规则把 adjustment、参数和具体 scalar field 固化在规则上，不会随着用户后续修改默认设置而漂移。
