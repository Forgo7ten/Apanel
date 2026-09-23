import type { ReactNode } from "react";

type StateTone = "positive" | "warning" | "negative" | "neutral";

const toneStyles: Record<StateTone, string> = {
  positive: "border-positive/25 bg-positive/10 text-positive",
  warning: "border-warning/25 bg-warning/10 text-warning",
  negative: "border-negative/25 bg-negative/10 text-negative",
  neutral: "border-line bg-card text-secondary",
};

const dotStyles: Record<StateTone, string> = {
  positive: "bg-positive",
  warning: "bg-warning",
  negative: "bg-negative",
  neutral: "bg-neutral",
};

export function StateTag({ tone = "neutral", children }: { tone?: StateTone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] font-medium ${toneStyles[tone]}`}>
      <span className={`size-1.5 rounded-full ${dotStyles[tone]}`} />
      {children}
    </span>
  );
}
