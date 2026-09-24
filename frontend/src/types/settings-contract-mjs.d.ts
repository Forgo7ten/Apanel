declare module "@/lib/settings-contract.mjs" {
  export type SettingsParameter = string | number | boolean;

  export type SettingsForm = {
    adjustType: "qfq" | "none";
    defaults: string[];
    parameters: Record<string, Record<string, SettingsParameter>>;
    density: "compact" | "comfortable";
    showStates: boolean;
    showDeltas: boolean;
    showMiniChart: boolean;
    webhook: string;
    webhookConfigured: boolean;
  };

  export const DEFAULT_INDICATORS: readonly string[];
  export const SETTING_INDICATORS: readonly { id: string; title: string }[];
  export function getParameterFields(indicator: string): readonly { key: string; title: string }[];
  export function normalizeSettings(input: unknown): SettingsForm;
  export function toSettingsPayload(form: SettingsForm): {
    adjust_type: "qfq" | "none";
    indicator_settings: { defaults: string[]; parameters: Record<string, Record<string, SettingsParameter>> };
    display_settings: { density: "compact" | "comfortable"; show_states: boolean; show_deltas: boolean; show_mini_chart: boolean };
    notification_settings: { feishu_webhook: string | null };
  };
}
