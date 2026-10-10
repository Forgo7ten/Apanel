# Current State 与状态事件存储

## 当前实现边界

生产写入路径是 `AnalysisService`：按 `security_id + adjust_type` 锁定 `analysis_streams`，在同一事务内物化 Indicator、生成 State 时间线、发布 Current State/State Event 并推进水位。不在锁内调用行情提供方或飞书。EOD 与 bootstrap 共用此发布入口；等待中的行情修订和配置修改只能在发布提交后取得同一锁，其新 dirty/generation 不会被旧发布覆盖。

`current_indicator_states` 保存每个精确状态目标的最新快照。`state_events` 只保存变化，而不是每天重复的 ACTIVE/INACTIVE observation。现在使用**非 ORM 的 `StateRow`** 作为状态计算与 API 的临时对象。

## 状态与质量

| event_kind | 含义 | 是否作为新的状态进入事件 |
| --- | --- | --- |
| BASELINE | 初始化时观察到的状态，不宣称准确进入日期 | 否 |
| ENTER | 可证明的条件进入 | 非历史重述时可触发 |
| EXIT | 可证明的条件退出 | 当前默认不发送进入通知 |
| UNKNOWN | 数据不足或无效，不能当成 INACTIVE | 否 |
| RECOVER | 恢复为可判断状态，但无法证明在缺口中何时变化 | 否 |

若缺口之后相邻的有效指标本身足以证明一个 edge，允许记录可证明的 ENTER；并非所有 UNKNOWN 后的 ACTIVE 都被强制当成 RECOVER。无法确定进入日期时 `since_date=null`、`since_is_exact=false`，`observed_from` 记录当前开始能够观察的日期。

历史修订通过旧事件 `valid=false` 和新事件 `is_restatement=true` 表达，保留审计证据，不要求额外建立一个含义重复的 CORRECTION 状态。历史重述不是新行情事件，不应补发为今日提醒。已有通知保留原来的事件引用及数据版本。

## 序号与分页版本是不同概念

`event_sequence` 在 stream 的事务锁内分配，随新事件提交。它是消费者的顺序水位，不能替换为全局自增 ID 的最大值。

`state_generation` 是有效事件集合及查询日期锚点的版本。只撤销旧事件、没有插入任何新事件时，`event_sequence` 不变，但 `state_generation` 必须递增。当前观察日期推进而没有产生事件时，默认历史窗口也会移动，同样更新分页版本。分页版本由当前分析事务管理。

`GET /api/v1/securities/{symbol}/states/events` 的 `generation` 现在对应 `state_generation`。第一页返回 `generation` 与 `next_cursor`；后续请求携带该 `generation` 和 `after_seq=next_cursor`。版本不匹配返回 `STATE_EVENT_GENERATION_CHANGED` (409)，调用方应从第一页重新读取。重复重放相同时间线且截止日期不变时，不分配新的事件 ID 或推进版本。

## 日度历史与事件查询

`/states/history` 保留默认 90、显式最多 366 个日历日的日度返回语义，不把永久存储恢复成每天一条。已完成首次状态发布的 stream 根据保留的 IndicatorSnapshot、实际日历和区间前的事件锚点只读重建日度字段；最多返回 20,000 个状态行。

查询起始日期可以是周末或节假日。需要查找区间内第一条真实 observation，再查它前一个确认交易日，而不是试图为一个 CLOSED 日期求前驱。缺少指标或日历仍应 fail closed，不能跳过真正缺失的开市日伪造连续性。

当前只识别 `_format=2` 的多变体 IndicatorSnapshot；不会解析旧版扁平格式，格式不符应拒绝继续计算。

## 结构初始化约束

`state_events` 与 `current_indicator_states` 随 Backend 的 `0001_fresh_schema` 在空库中创建；首次发布之前返回 `STATE_EVENTS_NOT_READY`，由正常 bootstrap/EOD 任务建立状态时间线。数据库初始化、维护操作和停机注意事项见 [开始使用](getting-started.md) 与 [开发指南](development.md)。
