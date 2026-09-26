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
  }): Record<string, unknown>;
  export function shouldShowNotificationRetry(record: unknown): boolean;
}
