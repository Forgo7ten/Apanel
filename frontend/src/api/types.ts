export type Identifier = number | string;

export type IndicatorViewMode = "NUMBER" | "DELTA" | "STATUS" | "COMPOSITE";

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
  parameters?: Record<string, string | number | boolean>;
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
  title: string;
  level?: "INFO" | "POSITIVE" | "WARNING" | "NEGATIVE" | "CRITICAL" | string;
  severity?: "INFO" | "POSITIVE" | "WARNING" | "NEGATIVE" | "CRITICAL" | string;
  active?: boolean;
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

export type IndicatorHistoryPoint = Record<string, unknown>;

export type StateHistoryPoint = Record<string, unknown>;

export type CreateWatchTableInput = {
  name: string;
};

export type AddStockInput = {
  security_id: Identifier;
};

export type CreateColumnInput = {
  column_type: "INDICATOR" | string;
  indicator_type: string;
  parameters?: Record<string, string | number | boolean>;
  view_mode: IndicatorViewMode;
};

export type UpdateColumnInput = Partial<{
  visible: boolean;
  hidden: boolean;
  order: number;
  width: number;
  view_mode: IndicatorViewMode;
}>;
