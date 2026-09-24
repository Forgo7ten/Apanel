import type { IndicatorState, IndicatorViewMode } from "@/api/types";
import { StateTag, stateToneFromLevel } from "@/components/ui/StateTag";

import { CompositeCell } from "./CompositeCell";
import { formatMetricValue, getDelta, getDirection, getDisplayNumber, isRecord } from "./indicator-utils";

type IndicatorCellProps = {
  mode: IndicatorViewMode;
  value: unknown;
  states?: IndicatorState[];
};

function DeltaCell({ value }: { value: unknown }) {
  const delta = getDelta(value);
  const direction = getDirection(value);
  const normalizedDelta = typeof delta === "number" ? Math.abs(delta) : typeof delta === "string" && delta.startsWith("-") ? delta.slice(1) : delta;
  const deltaText = normalizedDelta === null ? null : formatMetricValue(normalizedDelta);

  return (
    <div className="space-y-0.5">
      <div className="tabular-nums text-sm text-primary">{formatMetricValue(value)}</div>
      {deltaText !== null ? (
        <div className={`text-xs tabular-nums ${direction === "UP" ? "text-positive" : direction === "DOWN" ? "text-negative" : "text-muted"}`}>
          {direction === "UP" ? "↑" : direction === "DOWN" ? "↓" : "→"}
          {deltaText}
        </div>
      ) : null}
    </div>
  );
}

function StatusCell({ value, states }: { value: unknown; states: IndicatorState[] }) {
  const stateValue = isRecord(value) ? value.status ?? value.state ?? value.title ?? value.label : value;
  const stateText = typeof stateValue === "string" || typeof stateValue === "number" ? String(stateValue) : null;
  const state = states[0];

  if (state) {
    return <StateTag tone={stateToneFromLevel(state.level ?? state.severity)}>{state.title}</StateTag>;
  }

  return stateText ? <StateTag>{stateText}</StateTag> : <span className="text-sm text-muted">—</span>;
}

export function IndicatorCell({ mode, value, states = [] }: IndicatorCellProps) {
  switch (mode) {
    case "DELTA":
      return <DeltaCell value={value} />;
    case "STATUS":
      return <StatusCell value={value} states={states} />;
    case "COMPOSITE":
      return <CompositeCell value={value} states={states} />;
    case "NUMBER":
    default:
      return <span className="text-sm tabular-nums text-primary">{formatMetricValue(getDisplayNumber(value))}</span>;
  }
}
