declare module "@/lib/watch-contract.mjs" {
  export function getSecurityIdentifier(security: unknown): number | string | null;
  export function toAddStockPayload(security: unknown): { security_id: number | string } | null;
  export const WATCH_INDICATOR_OPTIONS: ReadonlyArray<{ value: string; label: string }>;
  export function getIndicatorFieldOptions(indicatorType: unknown): ReadonlyArray<{ value: string; label: string }>;
  export function getDefaultIndicatorParameters(indicatorType: unknown): Record<string, number>;
  export function toCreateColumnPayload(input: {
    indicatorType: unknown;
    viewMode?: string;
    parameters?: Record<string, number | string | boolean | null | number[] | string[]>;
  }): {
    column_type: "INDICATOR";
    indicator_type: string;
    parameters: Record<string, number | string | boolean | null | number[] | string[]>;
    view_mode: "NUMBER" | "DELTA" | "STATUS" | "COMPOSITE";
  };
  export function toColumnOrderPayload(columnOrder: unknown): { column_ids: number[] };
  export function watchDetailViewState(input?: {
    isPending?: boolean;
    isFetching?: boolean;
    isError?: boolean;
    hasData?: boolean;
  }): "loading" | "error" | "ready" | "refreshing" | "refresh-error" | "empty";
  export function errorBoundaryCopy(): {
    title: string;
    description: string;
    retryLabel: string;
  };
  export function isCurrentWatchDetail(detail: unknown, activeTableId: number | string | null): boolean;
  export function calculateVirtualRange(input?: {
    itemCount?: number;
    scrollTop?: number;
    viewportHeight?: number;
    rowHeight?: number;
    overscan?: number;
  }): {
    start: number;
    end: number;
    offsetTop: number;
    offsetBottom: number;
    totalHeight: number;
  };
  export function dialogFocusTargetIndex(currentIndex: number, count: number, shiftKey?: boolean): number;
  export const WATCH_DEFAULT_ROW_HEIGHT: number;
  export const WATCH_COMPOSITE_LAYOUT: Readonly<{
    fieldBlockHeight: number;
    fieldGap: number;
    maxStateTags: number;
    stateBlockHeight: number;
    stateGap: number;
    verticalPadding: number;
  }>;
  export function watchRowLayoutContract(fieldCount?: number): {
    rowHeight: number;
    contentHeight: number;
    fieldCount: number;
    effectiveFieldCount: number;
    fieldBlockHeight: number;
    fieldGap: number;
    maxStateTags: number;
    stateBlockHeight: number;
    stateGap: number;
    verticalPadding: number;
  };
  export function virtualScrollTopForKey(
    key: string,
    currentScrollTop?: number,
    viewportHeight?: number,
    totalHeight?: number,
    rowHeight?: number,
  ): number | null;
}
