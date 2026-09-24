"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { getSettings, updateSettings } from "@/api/settings";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState, QueryErrorState } from "@/components/ui/QueryState";
import { StateTag } from "@/components/ui/StateTag";
import { isApiError } from "@/lib/api-errors";
import { getParameterFields, normalizeSettings, SETTING_INDICATORS, toSettingsPayload, type SettingsForm } from "@/lib/settings-contract.mjs";

function errorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 404) return "后端尚未提供用户设置接口。";
  return error instanceof Error ? error.message : "设置保存失败，请稍后重试。";
}

export function SettingsWorkspace() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const [draft, setDraft] = useState<SettingsForm | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const serverForm = normalizeSettings(settingsQuery.data);
  const form = draft ?? serverForm;

  const saveMutation = useMutation({
    mutationFn: () => updateSettings(toSettingsPayload(form)),
    onSuccess: async (settings) => {
      setDraft(normalizeSettings(settings));
      setValidationError(null);
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  function updateForm(patch: Partial<SettingsForm>): void {
    setDraft((current) => ({ ...(current ?? form), ...patch }));
    setValidationError(null);
    setSaved(false);
    saveMutation.reset();
  }

  function toggleIndicator(indicator: string): void {
    const defaults = form.defaults.includes(indicator)
      ? form.defaults.filter((item) => item !== indicator)
      : [...form.defaults, indicator];
    updateForm({ defaults });
  }

  function updateParameter(indicator: string, key: string, value: string): void {
    const parsed: string | number = value.trim() === "" ? "" : Number(value);
    updateForm({
      parameters: {
        ...form.parameters,
        [indicator]: {
          ...form.parameters[indicator],
          [key]: Number.isFinite(parsed) ? parsed : value,
        },
      },
    });
  }

  function handleSave(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const webhook = form.webhook.trim();
    if (webhook) {
      try {
        const url = new URL(webhook);
        if (url.protocol !== "https:") throw new Error("protocol");
      } catch {
        setValidationError("飞书 Webhook 必须是有效的 HTTPS 地址。");
        return;
      }
    }

    for (const indicator of form.defaults) {
      const parameters = form.parameters[indicator] ?? {};
      const hasInvalidValue = Object.values(parameters).some((value) => typeof value !== "number" || !Number.isFinite(value) || value <= 0);
      if (hasInvalidValue) {
        setValidationError("默认指标参数必须是大于 0 的数字。");
        return;
      }
    }

    setValidationError(null);
    setSaved(false);
    saveMutation.mutate();
  }

  const error = validationError ?? (saveMutation.error ? errorMessage(saveMutation.error) : null);
  const showForm = settingsQuery.isSuccess && settingsQuery.data !== null && settingsQuery.data !== undefined;

  return (
    <div className="space-y-6">
      <section>
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Workspace settings</p>
        <h1 className="mt-2 text-title font-bold tracking-tight text-primary">设置</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">管理数据口径、指标偏好与通知出口，让工作台保持一致。</p>
      </section>

      {settingsQuery.isPending ? <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel"><LoadingState label="正在加载用户设置…" /></div> : null}
      {settingsQuery.isError ? (
        <div className="rounded-panel border border-line bg-panel shadow-panel">
          <QueryErrorState
            error={settingsQuery.error}
            onRetry={() => settingsQuery.refetch()}
            missingTitle="设置接口尚未就绪"
            missingDescription="后端尚未提供用户设置接口，请完成服务端路由后重试。"
          />
        </div>
      ) : null}

      {showForm ? (
        <form onSubmit={handleSave} className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-4">
            <section className="rounded-panel border border-line bg-panel shadow-panel">
              <div className="border-b border-line px-4 py-4">
                <h2 className="text-sm font-semibold text-primary">数据口径</h2>
                <p className="mt-1 text-xs leading-5 text-muted">所有指标使用同一复权方式，避免同一工作台内口径漂移。</p>
              </div>
              <div className="px-4 py-4">
                <fieldset disabled={saveMutation.isPending}>
                  <legend className="text-xs font-medium text-secondary">复权方式</legend>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <label className={`flex cursor-pointer items-center gap-2 rounded-panel border px-3 py-2.5 text-xs transition ${form.adjustType === "qfq" ? "border-brand bg-brand/10 text-primary" : "border-line bg-card text-secondary hover:border-brand/50"}`}>
                      <input type="radio" name="adjust-type" value="qfq" checked={form.adjustType === "qfq"} onChange={() => updateForm({ adjustType: "qfq" })} className="accent-brand" />
                      前复权（qfq）
                    </label>
                    <label className={`flex cursor-pointer items-center gap-2 rounded-panel border px-3 py-2.5 text-xs transition ${form.adjustType === "none" ? "border-brand bg-brand/10 text-primary" : "border-line bg-card text-secondary hover:border-brand/50"}`}>
                      <input type="radio" name="adjust-type" value="none" checked={form.adjustType === "none"} onChange={() => updateForm({ adjustType: "none" })} className="accent-brand" />
                      不复权（none）
                    </label>
                  </div>
                </fieldset>
              </div>
            </section>

            <section className="rounded-panel border border-line bg-panel shadow-panel">
              <div className="border-b border-line px-4 py-4">
                <h2 className="text-sm font-semibold text-primary">指标偏好</h2>
                <p className="mt-1 text-xs leading-5 text-muted">设置默认展示指标和参数；计算仍由后端完成。</p>
              </div>
              <div className="space-y-5 px-4 py-4">
                <fieldset disabled={saveMutation.isPending}>
                  <legend className="text-xs font-medium text-secondary">默认指标</legend>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {SETTING_INDICATORS.map((indicator) => {
                      const checked = form.defaults.includes(indicator.id);
                      return (
                        <label key={indicator.id} className={`inline-flex cursor-pointer items-center gap-2 rounded-panel border px-3 py-2 text-xs transition ${checked ? "border-brand bg-brand/10 text-primary" : "border-line bg-card text-secondary hover:border-brand/50"}`}>
                          <input type="checkbox" checked={checked} onChange={() => toggleIndicator(indicator.id)} className="accent-brand" />
                          {indicator.title}
                        </label>
                      );
                    })}
                  </div>
                </fieldset>

                <fieldset disabled={saveMutation.isPending}>
                  <legend className="text-xs font-medium text-secondary">默认参数</legend>
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    {SETTING_INDICATORS.map((indicator) => {
                      const fields = getParameterFields(indicator.id);
                      return (
                        <div key={indicator.id} className="rounded-panel border border-line bg-card p-3">
                          <div className="flex items-center justify-between">
                            <p className="text-xs font-semibold text-primary">{indicator.title}</p>
                            <span className="text-[10px] text-muted">后端参数</span>
                          </div>
                          <div className="mt-3 grid grid-cols-2 gap-2">
                            {fields.map((field) => (
                              <label key={field.key} className="text-[11px] text-muted">{field.title}
                                <input
                                  type="number"
                                  min="0.1"
                                  step="any"
                                  value={String(form.parameters[indicator.id]?.[field.key] ?? "")}
                                  onChange={(event) => updateParameter(indicator.id, field.key, event.target.value)}
                                  className="mt-1 h-8 w-full rounded border border-line bg-panel px-2 text-xs text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
                                />
                              </label>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </fieldset>
              </div>
            </section>

            <section className="rounded-panel border border-line bg-panel shadow-panel">
              <div className="border-b border-line px-4 py-4">
                <h2 className="text-sm font-semibold text-primary">显示设置</h2>
                <p className="mt-1 text-xs leading-5 text-muted">只控制展示密度和辅助信息，不改变后端指标结果。</p>
              </div>
              <div className="space-y-4 px-4 py-4">
                <fieldset disabled={saveMutation.isPending}>
                  <legend className="text-xs font-medium text-secondary">信息密度</legend>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <label className={`flex cursor-pointer items-center gap-2 rounded-panel border px-3 py-2.5 text-xs transition ${form.density === "compact" ? "border-brand bg-brand/10 text-primary" : "border-line bg-card text-secondary hover:border-brand/50"}`}>
                      <input type="radio" name="density" value="compact" checked={form.density === "compact"} onChange={() => updateForm({ density: "compact" })} className="accent-brand" />
                      紧凑
                    </label>
                    <label className={`flex cursor-pointer items-center gap-2 rounded-panel border px-3 py-2.5 text-xs transition ${form.density === "comfortable" ? "border-brand bg-brand/10 text-primary" : "border-line bg-card text-secondary hover:border-brand/50"}`}>
                      <input type="radio" name="density" value="comfortable" checked={form.density === "comfortable"} onChange={() => updateForm({ density: "comfortable" })} className="accent-brand" />
                      舒适
                    </label>
                  </div>
                  <div className="mt-4 grid gap-2 sm:grid-cols-3">
                    <label className="flex items-center gap-2 text-xs text-secondary"><input type="checkbox" checked={form.showStates} onChange={(event) => updateForm({ showStates: event.target.checked })} className="accent-brand" />显示状态</label>
                    <label className="flex items-center gap-2 text-xs text-secondary"><input type="checkbox" checked={form.showDeltas} onChange={(event) => updateForm({ showDeltas: event.target.checked })} className="accent-brand" />显示变化</label>
                    <label className="flex items-center gap-2 text-xs text-secondary"><input type="checkbox" checked={form.showMiniChart} onChange={(event) => updateForm({ showMiniChart: event.target.checked })} className="accent-brand" />显示迷你图</label>
                  </div>
                </fieldset>
              </div>
            </section>

            <section className="rounded-panel border border-line bg-panel shadow-panel">
              <div className="border-b border-line px-4 py-4">
                <h2 className="text-sm font-semibold text-primary">通知渠道</h2>
                <p className="mt-1 text-xs leading-5 text-muted">Webhook 仅用于后端发送飞书通知，前端不会记录或打印该地址。</p>
              </div>
              <div className="px-4 py-4">
                <fieldset disabled={saveMutation.isPending}>
                  <label htmlFor="feishu-webhook" className="text-xs font-medium text-secondary">飞书 Webhook</label>
                  <input
                    id="feishu-webhook"
                    type="password"
                    value={form.webhook}
                    onChange={(event) => updateForm({ webhook: event.target.value })}
                    placeholder={form.webhookConfigured ? "已配置；重新输入可替换，留空会清除" : "https://open.feishu.cn/open-apis/bot/v2/hook/..."}
                    autoComplete="new-password"
                    spellCheck={false}
                    className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
                  />
                  <p className="mt-2 text-[11px] leading-5 text-muted">为安全起见，已保存地址不会回填；提交空值会明确清除后端保存的 Webhook。</p>
                </fieldset>
              </div>
            </section>

            {error ? <p className="rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{error}</p> : null}
          </div>

          <aside className="h-fit rounded-panel border border-line bg-panel p-card shadow-panel">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-primary">保存状态</p>
              <StateTag tone={saved ? "positive" : saveMutation.error || validationError ? "negative" : "neutral"}>{saved ? "已保存" : saveMutation.isPending ? "保存中" : "未保存"}</StateTag>
            </div>
            <p className="mt-3 text-xs leading-5 text-muted">设置按当前登录用户隔离保存。复权、默认参数和显示设置会在后续数据请求中由后端应用。</p>
            <button type="submit" disabled={saveMutation.isPending} className="mt-5 h-10 w-full rounded-panel bg-brand px-3 text-sm font-medium text-white transition hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-50">
              {saveMutation.isPending ? "保存中…" : "保存设置"}
            </button>
            <div className="mt-5 border-t border-line pt-4 text-[11px] leading-5 text-muted">
              空 Webhook 会提交 `null`，用于清除已保存的通知出口；不会把敏感值放进错误提示。
            </div>
          </aside>
        </form>
      ) : null}

      {settingsQuery.isSuccess && !showForm ? (
        <div className="rounded-panel border border-line bg-panel shadow-panel">
          <EmptyState title="设置数据为空" description="后端返回了空设置对象，请完成用户设置初始化后重试。" action={<button type="button" onClick={() => settingsQuery.refetch()} className="rounded-panel border border-line bg-card px-3 py-2 text-xs text-secondary hover:text-primary">重新加载</button>} />
        </div>
      ) : null}

    </div>
  );
}
