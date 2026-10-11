declare module "@/lib/notification-contract.mjs" {
  export type AlertOption = {
    id: string;
    title: string;
    level?: string;
  };

  export const ALERT_STATE_OPTIONS: readonly AlertOption[];
  export const ALERT_INDICATOR_OPTIONS: readonly AlertOption[];
  export const ALERT_OPERATOR_OPTIONS: readonly AlertOption[];

  export function buildAlertPayload(form: {
    security_id: number | string;
    condition_type: "STATE" | "VALUE";
    state_id?: string;
    indicator?: string;
    operator?: string;
    threshold?: number | string;
    parameters?: import("@/api/types").IndicatorParameters;
    field?: string;
    adjust_type?: "qfq" | "none";
  }): Record<string, unknown>;
  export function displayAlertThreshold(indicator: string | undefined, threshold: number | string | null | undefined): string;
  export function alertThresholdLabel(indicator: string | undefined, threshold: number | string | null | undefined): string;
  export function shouldShowNotificationRetry(record: unknown): boolean;
}
