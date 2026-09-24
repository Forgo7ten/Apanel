import type { IndicatorState } from "@/api/types";

import { StateTag, stateToneFromLevel } from "@/components/ui/StateTag";

import { formatMetricValue, getDirection, isRecord } from "./indicator-utils";

const fieldOrder = ["upper", "middle", "lower", "ma5", "ma10", "diff", "dea", "histogram", "k", "d", "j"];
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

function getFields(value: unknown): Array<[string, unknown]> {
  if (!isRecord(value)) {
    return [];
  }

  const keys = Object.keys(value).filter((key) => !["state", "status", "states", "title", "label"].includes(key));
  keys.sort((left, right) => {
    const leftIndex = fieldOrder.indexOf(left.toLowerCase());
    const rightIndex = fieldOrder.indexOf(right.toLowerCase());
    return (leftIndex < 0 ? fieldOrder.length : leftIndex) - (rightIndex < 0 ? fieldOrder.length : rightIndex);
  });
  return keys.map((key) => [key, value[key]]);
}

export function CompositeCell({ value, states = [] }: { value: unknown; states?: IndicatorState[] }) {
  const fields = getFields(value);
  const displayStates = states.slice(0, 2);

  if (fields.length === 0) {
    return <span className="text-sm tabular-nums text-muted">—</span>;
  }

  return (
    <div className="min-w-[150px] space-y-1 py-1">
      <div className="space-y-0.5">
        {fields.slice(0, 4).map(([key, fieldValue]) => {
          const direction = getDirection(fieldValue);
          return (
            <div key={key} className="flex items-center justify-between gap-3 text-xs">
              <span className="text-muted">{getFieldLabel(key)}</span>
              <span className="tabular-nums text-secondary">
                {formatMetricValue(fieldValue)}
                {direction && direction !== "FLAT" ? (
                  <span className={direction === "UP" ? "ml-1 text-positive" : "ml-1 text-negative"} aria-label={direction === "UP" ? "上升" : "下降"}>
                    {direction === "UP" ? "↑" : "↓"}
                  </span>
                ) : null}
              </span>
            </div>
          );
        })}
      </div>
      {displayStates.length > 0 ? (
        <div className="flex flex-wrap gap-1 pt-0.5">
          {displayStates.map((state) => (
            <StateTag key={state.state_id} tone={stateToneFromLevel(state.level ?? state.severity)}>
              {state.title}
            </StateTag>
          ))}
        </div>
      ) : null}
    </div>
  );
}
