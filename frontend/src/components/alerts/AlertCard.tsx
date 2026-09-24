import type { AlertRule } from "@/api/types";
import { ALERT_STATE_OPTIONS } from "@/lib/notification-contract.mjs";
import { stateToneFromLevel, StateTag } from "@/components/ui/StateTag";

const stateById = new Map(ALERT_STATE_OPTIONS.map((option) => [option.id, option]));

function getStateId(rule: AlertRule): string | null {
  return rule.state_id ?? rule.state_code ?? null;
}

function getSecurityName(rule: AlertRule): string {
  return rule.name ?? rule.security?.name ?? rule.symbol ?? `证券 ${String(rule.security_id)}`;
}

function getStateTitle(rule: AlertRule): string {
  const stateId = getStateId(rule);
  return (stateId && stateById.get(stateId)?.title) ?? stateId ?? "未命名状态";
}

function getConditionLabel(rule: AlertRule): string {
  if (rule.condition_type.toUpperCase() === "STATE") {
    return getStateTitle(rule);
  }

  const indicator = rule.indicator ?? rule.indicator_type ?? "指标";
  return `${indicator} ${rule.operator ?? ""} ${rule.threshold ?? ""}`.trim();
}

export function AlertCard({
  rule,
  onEdit,
  onToggle,
  onDelete,
  isToggling = false,
  isDeleting = false,
}: {
  rule: AlertRule;
  onEdit: (rule: AlertRule) => void;
  onToggle: (rule: AlertRule) => void;
  onDelete: (rule: AlertRule) => void;
  isToggling?: boolean;
  isDeleting?: boolean;
}) {
  const stateId = getStateId(rule);
  const stateOption = stateId ? stateById.get(stateId) : undefined;
  const enabled = rule.enabled !== false;
  const tone = stateToneFromLevel(stateOption?.level);

  return (
    <article className={`rounded-panel border border-line bg-card p-4 transition ${enabled ? "" : "opacity-65"}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold text-primary">{getSecurityName(rule)}</p>
            {rule.symbol ? <span className="font-mono text-[11px] text-muted">{rule.symbol}</span> : null}
            <StateTag tone={enabled ? "positive" : "neutral"}>{enabled ? "已启用" : "已停用"}</StateTag>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-secondary">
            <span className="rounded border border-line bg-panel px-2 py-1 font-medium text-muted">
              {rule.condition_type.toUpperCase() === "STATE" ? "状态" : "数值"}
            </span>
            <StateTag tone={rule.condition_type.toUpperCase() === "STATE" ? tone : "neutral"}>
              {getConditionLabel(rule)}
            </StateTag>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            disabled={isToggling || isDeleting}
            onClick={() => onToggle(rule)}
            className="rounded border border-line bg-panel px-2 py-1.5 text-[11px] font-medium text-secondary transition hover:border-brand/60 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
            aria-pressed={enabled}
          >
            {isToggling ? "保存中…" : enabled ? "停用" : "启用"}
          </button>
          <button
            type="button"
            disabled={isDeleting || isToggling}
            onClick={() => onEdit(rule)}
            className="rounded border border-line bg-panel px-2 py-1.5 text-[11px] font-medium text-secondary transition hover:border-brand/60 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            编辑
          </button>
          <button
            type="button"
            disabled={isDeleting || isToggling}
            onClick={() => onDelete(rule)}
            className="rounded border border-negative/30 bg-negative/5 px-2 py-1.5 text-[11px] font-medium text-negative transition hover:bg-negative/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isDeleting ? "删除中…" : "删除"}
          </button>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-line/70 pt-3 text-[11px] text-muted">
        <span>渠道：飞书 Webhook</span>
        {rule.created_at ? <span>创建于：{formatDate(rule.created_at)}</span> : null}
        <span className="font-mono">规则 #{String(rule.id)}</span>
      </div>
    </article>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
