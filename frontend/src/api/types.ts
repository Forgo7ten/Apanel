export type Identifier = number | string;

export type IndicatorViewMode = "NUMBER" | "DELTA" | "STATUS" | "COMPOSITE";

export type IndicatorParameterValue = string | number | boolean | null | string[] | number[];

export type IndicatorParameters = Record<string, IndicatorParameterValue>;

export type IndicatorScalarValue = number | string | null;

export type IndicatorDirection = "UP" | "DOWN" | "FLAT" | string;

export type IndicatorFieldValue = {
  value: IndicatorScalarValue;
  previous_value: IndicatorScalarValue;
  delta: IndicatorScalarValue;
  direction: IndicatorDirection;
};

type IndicatorColumnValueMetadata = {
  column_id: Identifier;
  indicator_type: string;
  parameters: IndicatorParameters;
  available: boolean;
  error_code?: string;
};

export type ScalarIndicatorColumnValue = IndicatorColumnValueMetadata & {
  view_mode: "NUMBER" | "DELTA";
  value: IndicatorScalarValue;
  previous_value: IndicatorScalarValue;
  delta: IndicatorScalarValue;
  direction: IndicatorDirection;
  parameter_key: string;
};

export type CompositeIndicatorColumnValue = IndicatorColumnValueMetadata & {
  view_mode: "COMPOSITE";
  fields: Record<string, IndicatorFieldValue>;
  value?: IndicatorScalarValue;
  previous_value?: IndicatorScalarValue;
  delta?: IndicatorScalarValue;
  direction?: IndicatorDirection;
  parameter_key: string;
};

export type StatusIndicatorColumnValue = IndicatorColumnValueMetadata & {
  view_mode: "STATUS";
  value?: IndicatorScalarValue;
  status?: string | number | null;
  state?: string | number | null;
  parameter_key: string;
};

export type IndicatorColumnValue =
  | ScalarIndicatorColumnValue
  | CompositeIndicatorColumnValue
  | StatusIndicatorColumnValue;

export type Security = {
  id?: Identifier;
  security_id?: Identifier;
  symbol: string;
  name: string;
  market: string;
  type?: string;
};

export type WatchTableSummary = {
  id: Identifier;
  name: string;
  stock_count: number;
};

export type WatchTableColumn = {
  id?: Identifier;
  key?: string;
  type: string;
  column_type?: string;
  indicator_type?: string;
  title?: string;
  label?: string;
  view_mode: IndicatorViewMode;
  parameters?: IndicatorParameters;
  hidden?: boolean;
  visible?: boolean;
  order?: number;
  width?: number;
};

export type PriceSnapshot = {
  value?: number | string | null;
  price?: number | string | null;
  change?: number | string | null;
  delta?: number | string | null;
  direction?: "UP" | "DOWN" | "FLAT" | string | null;
  timestamp?: string | null;
};

export type IndicatorState = {
  state_id: string;
  state_code?: string;
  title: string;
  indicator_type?: string;
  status?: string;
  level?: "INFO" | "POSITIVE" | "WARNING" | "NEGATIVE" | "CRITICAL" | string;
  severity?: "INFO" | "POSITIVE" | "WARNING" | "NEGATIVE" | "CRITICAL" | string;
  active?: boolean;
  transition?: boolean;
  trade_date?: string;
  metadata?: Record<string, unknown>;
};

export type WatchTableStock = {
  security_id?: Identifier;
  symbol: string;
  name?: string;
  market?: string;
  security?: Security;
  price?: PriceSnapshot | number | string | null;
  indicators?: Record<string, unknown>;
  indicator_values?: Record<string, unknown>;
  values?: Record<string, unknown>;
  column_values?: Record<string, IndicatorColumnValue>;
  states?: IndicatorState[];
};

export type WatchTableDetails = Omit<WatchTableSummary, "stock_count"> & {
  stock_count?: number;
  columns: WatchTableColumn[];
  stocks: WatchTableStock[];
};

export type QuoteSnapshot = {
  price: number;
  change: number;
  timestamp: string;
};

export type DailyBar = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

export type IndicatorSnapshot = Record<string, unknown>;

export type IndicatorHistoryPoint = {
  trade_date: string;
  indicator_type: string;
  parameter_key: string;
  parameters: IndicatorParameters;
  values: Record<string, number>;
  previous_values?: Record<string, number> | null;
  delta?: Record<string, number> | null;
};

export type IndicatorHistoryResponse = {
  items: IndicatorHistoryPoint[];
};

export type StateHistoryPoint = IndicatorState & {
  trade_date: string;
};

export type StateHistoryResponse = {
  items: StateHistoryPoint[];
};

export type HistoryQuery = {
  start?: string;
  end?: string;
  /** History currently has one coherent PRD sequence; do not request none. */
  adjust?: "qfq";
  parameter_key?: string;
};

export type CreateWatchTableInput = {
  name: string;
};

export type AddStockInput = {
  security_id: Identifier;
};

export type CreateColumnInput = {
  column_type: "INDICATOR" | string;
  indicator_type: string;
  parameters?: IndicatorParameters;
  view_mode: IndicatorViewMode;
};

export type UpdateColumnInput = Partial<{
  visible: boolean;
  hidden: boolean;
  position: number;
  order: number;
  width: number;
  view_mode: IndicatorViewMode;
  parameters: IndicatorParameters;
}>;

export type AlertConditionType = "STATE" | "VALUE";

export type AlertOperator = ">" | ">=" | "<" | "<=" | "=" | "!=";

export type AlertRule = {
  id: Identifier;
  security_id: Identifier;
  condition_type: AlertConditionType | string;
  state_id?: string | null;
  state_code?: string | null;
  indicator?: string | null;
  indicator_type?: string | null;
  operator?: AlertOperator | string | null;
  threshold?: number | string | null;
  enabled?: boolean;
  symbol?: string | null;
  name?: string | null;
  security?: Security | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type CreateAlertInput = {
  security_id: Identifier;
  condition_type: AlertConditionType;
  state_id?: string;
  indicator?: string;
  operator?: AlertOperator;
  threshold?: number;
};

export type UpdateAlertInput = Partial<CreateAlertInput> & {
  enabled?: boolean;
};

export type NotificationRecord = {
  id?: Identifier;
  alert_rule_id?: Identifier | null;
  security_id?: Identifier | null;
  symbol: string;
  name?: string | null;
  title: string;
  channel: string;
  status?: string | null;
  content?: Record<string, unknown> | string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean;
  indicator?: string | null;
  state_id?: string | null;
  created_at: string;
  sent_at?: string | null;
};

export type AdjustmentType = "qfq" | "none";

export type IndicatorSettings = {
  defaults?: string[];
  parameters?: Record<string, Record<string, IndicatorParameterValue>>;
};

export type DisplaySettings = {
  density?: "compact" | "comfortable";
  show_states?: boolean;
  show_deltas?: boolean;
  show_mini_chart?: boolean;
};

export type NotificationSettings = {
  /** Empty string is never sent; the UI maps it to null to clear the secret. */
  feishu_webhook?: string | null;
  /** Backends may expose configuration state without returning the secret. */
  feishu_webhook_configured?: boolean;
};

export type UserSettings = {
  adjust_type?: AdjustmentType;
  indicator_settings?: IndicatorSettings;
  display_settings?: DisplaySettings;
  notification_settings?: NotificationSettings;
};

export type UpdateSettingsInput = UserSettings;
