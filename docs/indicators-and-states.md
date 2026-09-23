# 指标与状态

指标和状态代码位于 `backend/app/indicators` 与 `backend/app/states`。它们是纯 Python 计算边界，当前没有对应的 HTTP API，也没有直接写入数据库；应用服务可以在取得日线后调用它们，再决定如何保存或展示结果。

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

`IndicatorRegistry` 通过名称解析实现，不需要在 dispatch 代码中添加分支。自定义指标只需提供非空 `name` 和 `calculate(series, **parameters)`，并返回 `IndicatorResult`；注册表会再次执行结果合同校验。

## 状态输入与引擎

状态引擎消费不可变的 `IndicatorSnapshot`，包括 `indicator`、`values`、可选 `previous_values`、`parameters` 和 `metadata`。快照会统一大小写、复制 mapping 并拒绝非有限值。

`StateEngine.evaluate_state()` 接受当前快照和上一快照，返回 `StateResult`：

- `active`：当前是否满足状态条件。
- `transition`：本次是否从未激活进入激活，供边缘触发提醒使用。
- `status`：`ACTIVE` 或 `INACTIVE`。
- `reason`：缺少上一快照、指标不匹配或输入无效时的安全原因。
- `values`、状态 code、level、metadata：供 UI/提醒层复用。

状态需要上一快照才能确认变化；缺少上一快照时返回非激活且不产生 transition。引擎可按 `stream_id`、`security_id` 或 `symbol` 保留进程内的上一次 active 值，也可以由调用方传入 `previous_active`。进程内历史不是持久化；重启后若要保持提醒语义，调用方必须保存状态。

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

## 与产品层的关系

状态定义是 UI 和提醒层共用的领域对象，避免两处重复判断。但当前仓库还没有把日线、指标结果、状态引擎和前端监控表串成完整业务流程；调用方需要自行提供快照历史和持久化策略。
