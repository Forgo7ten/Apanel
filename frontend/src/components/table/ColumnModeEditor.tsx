"use client";

import { useMemo, useState } from "react";
import type { IndicatorParameters, IndicatorViewMode, UpdateColumnInput, WatchTableColumn } from "@/api/types";
import { ALERT_STATE_OPTIONS } from "@/lib/notification-contract.mjs";
import { buildColumnModeUpdate, columnModeFields, columnModeParameters, maModeNeedsConfirmation } from "@/lib/column-mode-contract.mjs";

export function ColumnModeEditor({ column, mode, onSave, onCancel }: {
  column: WatchTableColumn;
  mode: IndicatorViewMode;
  onSave: (input: UpdateColumnInput) => Promise<void>;
  onCancel: () => void;
}) {
  const indicator = column.indicator_type ?? column.type;
  const [parameters, setParameters] = useState<IndicatorParameters>(() => columnModeParameters(column, mode));
  const availableFields = useMemo(() => columnModeFields(indicator, parameters), [indicator, parameters]);
  const [field, setField] = useState(() => {
    const options = columnModeFields(indicator, columnModeParameters(column, mode));
    return options.find((item) => item.value === column.parameters?.field)?.value ?? options[0]?.value ?? "";
  });
  const states = ALERT_STATE_OPTIONS.filter((item) => item.id.startsWith(`${indicator.toUpperCase()}_`));
  const [stateCode, setStateCode] = useState(column.view_mode === "STATUS" ? column.state_code ?? states[0]?.id ?? "" : states[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const requiresMaConfirmation = maModeNeedsConfirmation(column, mode);
  const [maConfirmed, setMaConfirmed] = useState(false);
  const numeric = mode === "NUMBER" || mode === "DELTA";

  async function save() {
    if (saving || (requiresMaConfirmation && !maConfirmed)) return;
    setError(null);
    try {
      const payload = buildColumnModeUpdate({ indicatorType: indicator, viewMode: mode, parameters, field, stateCode });
      setSaving(true);
      await onSave(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存展示模式失败。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => { event.preventDefault(); void save(); }} className="space-y-4" aria-label="配置指标列展示模式">
      <h3 className="text-sm font-semibold text-primary">配置「{indicator}」展示模式：{mode}</h3>
      <p className="text-xs leading-5 text-muted">请先指定状态或数值字段，再保存模式，避免后端因缺少参数拒绝修改。</p>
      {mode === "STATUS" ? (
        <label className="block text-xs text-secondary">状态
          <select value={stateCode} onChange={(event) => setStateCode(event.target.value)} disabled={saving || states.length === 0} className="mt-2 block w-full rounded-panel border border-line bg-card px-3 py-2 text-primary">
            {states.length === 0 ? <option value="">该指标暂无可选状态</option> : states.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.id}</option>)}
          </select>
        </label>
      ) : null}
      {requiresMaConfirmation ? (
        <label className="flex items-start gap-2 rounded-panel border border-line bg-card/40 p-3 text-xs leading-5 text-secondary">
          <input type="checkbox" checked={maConfirmed} onChange={(event) => setMaConfirmed(event.target.checked)} disabled={saving} className="mt-1 accent-brand" />
          <span>当前 MA 列不是双周期。转换为状态模式需要选定短周期和长周期，会改变该列的计算参数；请检查下方两个周期并确认。</span>
        </label>
      ) : null}
      {Object.entries(parameters).length ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {Object.entries(parameters).map(([key, value]) => (
            <label key={key} className="block text-xs text-secondary">{key}
              {Array.isArray(value) ? <span className="mt-2 block rounded border border-line bg-card px-2 py-2 text-xs text-primary" title="保留已有 MA 多周期参数，本次仅更改展示模式">{value.join("、")}</span> : (
                <input type="number" min="0.001" step="any" value={String(value ?? "")} disabled={saving} onChange={(event) => setParameters((current) => ({ ...current, [key]: event.target.value }))} className="mt-2 block w-full rounded-panel border border-line bg-card px-3 py-2 text-primary" />
              )}
            </label>
          ))}
        </div>
      ) : null}
      {numeric && availableFields.length ? (
        <label className="block text-xs text-secondary">显示字段
          <select value={availableFields.some((item) => item.value === field) ? field : ""} onChange={(event) => setField(event.target.value)} disabled={saving} className="mt-2 block w-full rounded-panel border border-line bg-card px-3 py-2 text-primary">
            {!availableFields.some((item) => item.value === field) ? <option value="">请选择字段</option> : null}
            {availableFields.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
      ) : null}
      {mode === "STATUS" && states.length === 0 ? <p role="alert" className="text-xs text-warning">此指标没有受支持的状态，不能切换到 STATUS 模式。</p> : null}
      {error ? <p role="alert" className="text-xs text-negative">{error}</p> : null}
      <div className="flex justify-end gap-2 border-t border-line pt-3">
        <button type="button" onClick={onCancel} disabled={saving} className="rounded-panel border border-line px-3 py-2 text-xs text-secondary disabled:opacity-40">取消</button>
        <button type="submit" disabled={saving || (mode === "STATUS" && states.length === 0) || (requiresMaConfirmation && !maConfirmed)} className="rounded-panel bg-brand px-3 py-2 text-xs text-white disabled:opacity-50">{saving ? "保存中…" : "保存展示模式"}</button>
      </div>
    </form>
  );
}
