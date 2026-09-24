"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { searchSecurities } from "@/api/securities";
import type { AlertRule, CreateAlertInput, Security } from "@/api/types";
import { StateTag, stateToneFromLevel } from "@/components/ui/StateTag";
import { isApiError } from "@/lib/api-errors";
import {
  ALERT_INDICATOR_OPTIONS,
  ALERT_OPERATOR_OPTIONS,
  ALERT_STATE_OPTIONS,
  buildAlertPayload,
} from "@/lib/notification-contract.mjs";

type SelectedSecurity = Security & { id: number | string };

function getSecurityIdentifier(security: Security | null): number | string | null {
  if (!security) return null;
  const identifier = security.id ?? security.security_id;
  if (typeof identifier === "number" && Number.isFinite(identifier)) return identifier;
  if (typeof identifier === "string" && identifier.trim()) return identifier;
  return null;
}

function getRuleSecurity(rule: AlertRule): SelectedSecurity | null {
  const identifier = rule.security_id;
  const security = rule.security;
  const id = security ? getSecurityIdentifier(security) : identifier;
  if (id === null || id === undefined) return null;

  return {
    id,
    security_id: id,
    symbol: security?.symbol ?? rule.symbol ?? String(id),
    name: security?.name ?? rule.name ?? `证券 ${String(id)}`,
    market: security?.market ?? "",
    type: security?.type,
  };
}

export function AlertWizard({
  open,
  initialRule,
  isSubmitting = false,
  submitError,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initialRule?: AlertRule | null;
  isSubmitting?: boolean;
  submitError?: unknown;
  onClose: () => void;
  onSubmit: (input: CreateAlertInput) => void;
}) {
  const initialSecurity = initialRule ? getRuleSecurity(initialRule) : null;
  const [securityQuery, setSecurityQuery] = useState(initialSecurity?.symbol ?? "");
  const [selectedSecurity, setSelectedSecurity] = useState<SelectedSecurity | null>(initialSecurity);
  const [conditionType, setConditionType] = useState<"STATE" | "VALUE">(initialRule?.condition_type.toUpperCase() === "VALUE" ? "VALUE" : "STATE");
  const [stateId, setStateId] = useState(initialRule?.state_id ?? initialRule?.state_code ?? ALERT_STATE_OPTIONS[0]?.id ?? "");
  const [indicator, setIndicator] = useState(initialRule?.indicator ?? initialRule?.indicator_type ?? ALERT_INDICATOR_OPTIONS[0]?.id ?? "RSI");
  const [operator, setOperator] = useState(initialRule?.operator ?? ALERT_OPERATOR_OPTIONS[0]?.id ?? ">=");
  const [threshold, setThreshold] = useState(initialRule?.threshold === null || initialRule?.threshold === undefined ? "70" : String(initialRule.threshold));
  const [formError, setFormError] = useState<string | null>(null);

  const editing = initialRule !== null && initialRule !== undefined;
  const title = editing ? "编辑监听规则" : "新建监听规则";
  const selectedState = useMemo(() => ALERT_STATE_OPTIONS.find((item) => item.id === stateId), [stateId]);

  const searchQuery = useQuery({
    queryKey: ["security-search", securityQuery.trim()],
    queryFn: () => searchSecurities(securityQuery.trim()),
    enabled: open && securityQuery.trim().length >= 2 && selectedSecurity?.symbol !== securityQuery.trim(),
    staleTime: 60_000,
  });

  if (!open) return null;

  function chooseSecurity(security: Security): void {
    const id = getSecurityIdentifier(security);
    if (id === null) return;
    setSelectedSecurity({ ...security, id, security_id: id });
    setSecurityQuery(security.symbol);
    setFormError(null);
  }

  function handleSecurityQueryChange(value: string): void {
    setSecurityQuery(value);
    if (selectedSecurity && value.trim() !== selectedSecurity.symbol) {
      setSelectedSecurity(null);
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const securityId = getSecurityIdentifier(selectedSecurity);
    if (securityId === null) {
      setFormError("请从搜索结果中选择一只股票。");
      return;
    }

    try {
      const payload = buildAlertPayload({
        security_id: securityId,
        condition_type: conditionType,
        state_id: stateId,
        indicator,
        operator,
        threshold,
      });
      onSubmit(payload as CreateAlertInput);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "请检查监听条件。");
    }
  }

  const serverError = isApiError(submitError) && submitError.status === 404
    ? editing ? "后端尚未提供修改监听规则接口。" : "后端尚未提供创建监听规则接口。"
    : submitError instanceof Error ? submitError.message : null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-app/80 px-4 py-8 backdrop-blur-sm" role="presentation">
      <div className="mx-auto max-w-xl rounded-panel border border-line bg-panel shadow-panel" role="dialog" aria-modal="true" aria-labelledby="alert-wizard-title">
        <div className="flex items-start justify-between border-b border-line px-5 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Alert workflow</p>
            <h2 id="alert-wizard-title" className="mt-1 text-lg font-semibold text-primary">{title}</h2>
            <p className="mt-1 text-xs text-muted">选择股票，再选择后端状态或数值条件。</p>
          </div>
          <button type="button" onClick={onClose} className="rounded border border-line bg-card px-2 py-1 text-xs text-secondary hover:text-primary" aria-label="关闭监听规则窗口">
            关闭
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5 px-5 py-5">
          <fieldset disabled={isSubmitting} className="space-y-5">
            <div>
              <label htmlFor="alert-security" className="text-xs font-medium text-secondary">股票</label>
              <input
                id="alert-security"
                value={securityQuery}
                onChange={(event) => handleSecurityQueryChange(event.target.value)}
                placeholder="输入代码或名称搜索"
                autoComplete="off"
                className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
              />
              {selectedSecurity ? (
                <div className="mt-2 flex items-center justify-between rounded-panel border border-brand/40 bg-brand/10 px-3 py-2">
                  <span>
                    <span className="block text-xs font-medium text-primary">{selectedSecurity.name}</span>
                    <span className="mt-0.5 block font-mono text-[11px] text-muted">{selectedSecurity.symbol}{selectedSecurity.market ? ` · ${selectedSecurity.market}` : ""}</span>
                  </span>
                  <span className="text-[11px] text-positive">已选择</span>
                </div>
              ) : null}
              {searchQuery.isPending ? <p className="mt-2 text-xs text-muted" aria-live="polite">正在搜索证券…</p> : null}
              {!selectedSecurity && searchQuery.data?.length ? (
                <div className="mt-2 max-h-48 overflow-y-auto rounded-panel border border-line bg-card p-1" role="listbox" aria-label="证券搜索结果">
                  {searchQuery.data.map((security) => {
                    const id = getSecurityIdentifier(security);
                    return (
                      <button
                        type="button"
                        key={`${security.symbol}-${String(id ?? "missing")}`}
                        disabled={id === null}
                        onClick={() => chooseSecurity(security)}
                        className="flex w-full items-center justify-between rounded px-3 py-2 text-left transition hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
                        role="option"
                        aria-selected="false"
                      >
                        <span>
                          <span className="block text-sm font-medium text-primary">{security.name}</span>
                          <span className="mt-0.5 block font-mono text-[11px] text-muted">{security.symbol} · {security.market}</span>
                        </span>
                        {id === null ? <span className="text-[10px] text-warning">缺少 ID</span> : null}
                      </button>
                    );
                  })}
                </div>
              ) : null}
              {!selectedSecurity && searchQuery.isError ? (
                <div className="mt-2 flex items-center justify-between gap-3 rounded-panel border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">
                  <span>证券搜索失败，请稍后重试。</span>
                  <button type="button" onClick={() => searchQuery.refetch()} className="rounded border border-warning/30 px-2 py-1 text-[11px] text-warning hover:bg-warning/10">重试</button>
                </div>
              ) : null}
              {!selectedSecurity && !searchQuery.isPending && !searchQuery.isError && securityQuery.trim().length >= 2 && searchQuery.data?.length === 0 ? <p className="mt-2 text-xs text-muted">没有匹配的证券。</p> : null}
            </div>

            <div>
              <span className="text-xs font-medium text-secondary">条件类型</span>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {(["STATE", "VALUE"] as const).map((type) => (
                  <label key={type} className={`flex cursor-pointer items-center gap-2 rounded-panel border px-3 py-2.5 text-xs transition ${conditionType === type ? "border-brand bg-brand/10 text-primary" : "border-line bg-card text-secondary hover:border-brand/50"}`}>
                    <input type="radio" name="alert-condition-type" value={type} checked={conditionType === type} onChange={() => setConditionType(type)} className="accent-brand" />
                    {type === "STATE" ? "状态监听" : "数值监听"}
                  </label>
                ))}
              </div>
            </div>

            {conditionType === "STATE" ? (
              <div>
                <label htmlFor="alert-state" className="text-xs font-medium text-secondary">状态</label>
                <select id="alert-state" value={stateId} onChange={(event) => setStateId(event.target.value)} className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30">
                  {ALERT_STATE_OPTIONS.map((option) => <option key={option.id} value={option.id}>{option.title} · {option.id}</option>)}
                </select>
                {selectedState ? <div className="mt-2"><StateTag tone={stateToneFromLevel(selectedState.level)}>{selectedState.title}</StateTag></div> : null}
                <p className="mt-2 text-[11px] leading-5 text-muted">状态语义来自后端 State Registry；前端只提交状态 ID。</p>
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-[1fr_1fr_1.2fr]">
                <label className="text-xs font-medium text-secondary">指标
                  <select value={indicator} onChange={(event) => setIndicator(event.target.value)} className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-2 text-sm font-normal text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30">
                    {ALERT_INDICATOR_OPTIONS.map((option) => <option key={option.id} value={option.id}>{option.title}</option>)}
                  </select>
                </label>
                <label className="text-xs font-medium text-secondary">比较
                  <select value={operator} onChange={(event) => setOperator(event.target.value)} className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-2 text-sm font-normal text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30">
                    {ALERT_OPERATOR_OPTIONS.map((option) => <option key={option.id} value={option.id}>{option.id} · {option.title}</option>)}
                  </select>
                </label>
                <label htmlFor="alert-threshold" className="text-xs font-medium text-secondary">阈值
                  <input id="alert-threshold" type="number" step="any" value={threshold} onChange={(event) => setThreshold(event.target.value)} className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm font-normal text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30" />
                </label>
              </div>
            )}
          </fieldset>

          {formError || serverError ? <p className="rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{formError ?? serverError}</p> : null}

          <div className="flex justify-end gap-2 border-t border-line pt-4">
            <button type="button" onClick={onClose} className="rounded-panel border border-line bg-card px-3.5 py-2 text-xs font-medium text-secondary hover:text-primary">取消</button>
            <button type="submit" disabled={isSubmitting} className="rounded-panel bg-brand px-3.5 py-2 text-xs font-medium text-white transition hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-50">
              {isSubmitting ? "保存中…" : editing ? "保存规则" : "创建规则"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
