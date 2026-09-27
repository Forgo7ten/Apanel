declare module "@/lib/indicator-metadata.mjs" {
  export const INDICATOR_METADATA: Record<string, { title: string; parameters: Record<string, number>; fields: Array<{ value: string; label: string }>; adjustment: boolean }>;
  export const INDICATOR_OPTIONS: Array<{ id: string; value: string; title: string; label: string }>;
  export function normalizeIndicatorType(value: unknown): string;
  export function getDefaultIndicatorParameters(indicatorType: string): Record<string, number>;
  export function getIndicatorFieldOptions(indicatorType: string): Array<{ value: string; label: string }>;
  export function indicatorUsesAdjustment(indicatorType: string): boolean;
  export function getIndicatorDisplayTitle(
    indicatorType: string,
    parameters?: Record<string, unknown>,
    fallback?: string,
  ): string;
}
