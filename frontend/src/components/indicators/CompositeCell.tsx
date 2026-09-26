import type { IndicatorState } from "@/api/types";

import { StateTag, stateToneFromLevel } from "@/components/ui/StateTag";

import { getColumnFields } from "@/lib/indicator-contract.mjs";
import { WATCH_COMPOSITE_LAYOUT } from "@/lib/watch-contract.mjs";

import { formatMetricValue, getDirection, getFieldValue } from "./indicator-utils";

const fieldLabels: Record<string, string> = {
  upper: "Upper",
  middle: "Middle",
  lower: "Lower",
  ma5: "MA5",
  ma10: "MA10",
  diff: "DIFF",
  dea: "DEA",
  histogram: "Histogram",
  k: "K",
  d: "D",
  j: "J",
};

function getFieldLabel(key: string): string {
  return fieldLabels[key.toLowerCase()] ?? key;
}

export function CompositeCell({ value, states = [] }: { value: unknown; states?: IndicatorState[] }) {
  const fields = getColumnFields(value);
  const displayStates = states.slice(0, WATCH_COMPOSITE_LAYOUT.maxStateTags);

  if (fields.length === 0) {
    return <span className="text-sm tabular-nums text-muted">—</span>;
  }

  return (
    <div className="min-w-[150px] whitespace-nowrap py-1">
      <div className="space-y-0.5">
        {fields.map(([key, fieldValue]) => {
          const direction = getDirection(fieldValue);
          const currentValue = getFieldValue(fieldValue, "value");
          const previousValue = getFieldValue(fieldValue, "previous_value");
          const delta = getFieldValue(fieldValue, "delta");
          return (
            <div key={key} className="flex h-8 items-start justify-between gap-3 text-xs leading-4">
              <span className="pt-0.5 leading-4 text-muted">{getFieldLabel(key)}</span>
              <span className="text-right tabular-nums leading-4 text-secondary">
                <span className="block h-4 leading-4">
                  {formatMetricValue(currentValue)}
                  {direction ? (
                    <span
                      className={direction === "UP" ? "ml-1 text-positive" : direction === "DOWN" ? "ml-1 text-negative" : "ml-1 text-muted"}
                      aria-label={direction === "UP" ? "上升" : direction === "DOWN" ? "下降" : "持平"}
                    >
                      {direction === "UP" ? "↑" : direction === "DOWN" ? "↓" : "→"}
                    </span>
                  ) : null}
                </span>
                <span className="block h-4 text-[10px] leading-4 text-muted">
                  前 {formatMetricValue(previousValue)} · Δ {formatMetricValue(delta)}
                </span>
              </span>
            </div>
          );
        })}
      </div>
      {displayStates.length > 0 ? (
        <div className="mt-1 flex h-5 max-w-full flex-nowrap items-center gap-1 overflow-x-auto">
          {displayStates.map((state) => (
            <StateTag key={state.state_id} compact tone={stateToneFromLevel(state.level ?? state.severity)}>
              {state.title}
            </StateTag>
          ))}
        </div>
      ) : null}
    </div>
  );
}
