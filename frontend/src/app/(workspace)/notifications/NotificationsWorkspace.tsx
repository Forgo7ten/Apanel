"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { createAlert, deleteAlert, getAlerts, updateAlert } from "@/api/alerts";
import { getNotifications, retryNotification } from "@/api/notifications";
import type { AlertRule, CreateAlertInput, Identifier, NotificationRecord } from "@/api/types";
import { AlertCard } from "@/components/alerts/AlertCard";
import { AlertWizard } from "@/components/alerts/AlertWizard";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState, QueryErrorState } from "@/components/ui/QueryState";
import { StateTag } from "@/components/ui/StateTag";
import { isApiError } from "@/lib/api-errors";
import { shouldShowNotificationRetry } from "@/lib/notification-contract.mjs";

function sameId(left: Identifier | undefined, right: Identifier | undefined): boolean {
  return left !== undefined && right !== undefined && String(left) === String(right);
}

function errorMessage(error: unknown, fallback: string): string {
  if (isApiError(error) && error.status === 404) return fallback;
  return error instanceof Error ? error.message : "操作未完成，请稍后重试。";
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function notificationName(record: NotificationRecord): string {
  return record.name ? `${record.name} · ${record.symbol}` : record.symbol;
}

function NotificationItem({
  record,
  onRetry,
  isRetrying,
}: {
  record: NotificationRecord;
  onRetry: (id: Identifier) => void;
  isRetrying: boolean;
}) {
  const status = record.status?.toUpperCase();
  const statusTone = status === "FAILED" || status === "ERROR" ? "negative" : status === "SENT" || status === "SUCCESS" ? "positive" : "neutral";
  const retryable = shouldShowNotificationRetry(record);

  return (
    <article className="border-b border-line/70 px-4 py-4 last:border-b-0">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-primary">{notificationName(record)}</p>
            <StateTag tone="neutral">{record.channel || "FEISHU"}</StateTag>
            {status ? <StateTag tone={statusTone}>{status}</StateTag> : null}
          </div>
          <p className="mt-2 text-sm text-secondary">{record.title}</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
            {record.indicator ? <span>指标：{record.indicator}</span> : null}
            {record.state_id ? <span>状态：{record.state_id}</span> : null}
            {record.alert_rule_id !== undefined && record.alert_rule_id !== null ? <span className="font-mono">规则 #{String(record.alert_rule_id)}</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-3">
          {retryable && record.id !== undefined && record.id !== null ? (
            <button
              type="button"
              onClick={() => onRetry(record.id as Identifier)}
              disabled={isRetrying}
              className="rounded-panel border border-line bg-card px-2.5 py-1.5 text-xs font-medium text-secondary transition hover:border-brand/60 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRetrying ? "重试中…" : "重试"}
            </button>
          ) : null}
          <time className="pt-1 text-xs tabular-nums text-muted" dateTime={record.created_at}>{formatDate(record.created_at)}</time>
        </div>
      </div>
      {record.error_message ? <p className="mt-2 rounded-panel border border-negative/20 bg-negative/5 px-3 py-2 text-xs leading-5 text-negative" role="status">{record.error_message}</p> : null}
    </article>
  );
}

export function NotificationsWorkspace() {
  const queryClient = useQueryClient();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);

  const notificationsQuery = useQuery({ queryKey: ["notifications"], queryFn: getNotifications });
  const alertsQuery = useQuery({ queryKey: ["alerts"], queryFn: getAlerts });

  const createMutation = useMutation({
    mutationFn: (input: CreateAlertInput) => createAlert(input),
    onSuccess: async () => {
      setWizardOpen(false);
      setEditingRule(null);
      await queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: Identifier; input: Parameters<typeof updateAlert>[1] }) => updateAlert(id, input),
    onSuccess: async () => {
      setWizardOpen(false);
      setEditingRule(null);
      await queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: Identifier; enabled: boolean }) => updateAlert(id, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: Identifier) => deleteAlert(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
  const retryMutation = useMutation({
    mutationFn: (id: Identifier) => retryNotification(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const rules = useMemo(() => alertsQuery.data ?? [], [alertsQuery.data]);
  const enabledRuleCount = rules.filter((rule) => rule.enabled !== false).length;
  const wizardError = editingRule ? updateMutation.error : createMutation.error;

  function openCreateWizard(): void {
    createMutation.reset();
    updateMutation.reset();
    setEditingRule(null);
    setWizardOpen(true);
  }

  function openEditWizard(rule: AlertRule): void {
    createMutation.reset();
    updateMutation.reset();
    setEditingRule(rule);
    setWizardOpen(true);
  }

  function submitRule(input: CreateAlertInput): void {
    if (editingRule) {
      updateMutation.mutate({ id: editingRule.id, input });
    } else {
      createMutation.mutate(input);
    }
  }

  function toggleRule(rule: AlertRule): void {
    toggleMutation.reset();
    toggleMutation.mutate({ id: rule.id, enabled: rule.enabled === false });
  }

  function removeRule(rule: AlertRule): void {
    if (!window.confirm(`确认删除 ${rule.symbol ?? `规则 #${String(rule.id)}`} 的监听规则吗？`)) return;
    deleteMutation.reset();
    deleteMutation.mutate(rule.id);
  }

  return (
    <div className="space-y-6">
      <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Notification center</p>
          <h1 className="mt-2 text-title font-bold tracking-tight text-primary">通知</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">集中查看触发记录，并管理需要关注的指标状态。</p>
        </div>
        <button type="button" onClick={openCreateWizard} className="inline-flex h-9 items-center justify-center gap-2 rounded-panel bg-brand px-3.5 text-sm font-medium text-white shadow-panel transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/40">
          <span className="text-base leading-none">+</span>
          新建监听
        </button>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(340px,0.9fr)]">
        <div className="min-w-0 overflow-hidden rounded-panel border border-line bg-panel shadow-panel">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-primary">通知记录</p>
              <p className="mt-1 text-xs text-muted">边缘触发后产生的通知会出现在这里</p>
            </div>
            {notificationsQuery.data ? <StateTag tone="neutral">{notificationsQuery.data.length} 条</StateTag> : null}
          </div>
          {notificationsQuery.isPending ? <LoadingState label="正在加载通知记录…" /> : null}
          {notificationsQuery.isError ? (
            <QueryErrorState
              error={notificationsQuery.error}
              onRetry={() => notificationsQuery.refetch()}
              missingTitle="通知接口尚未就绪"
              missingDescription="后端尚未提供通知记录接口，请完成服务端路由后重试。"
            />
          ) : null}
          {!notificationsQuery.isPending && !notificationsQuery.isError && notificationsQuery.data?.length === 0 ? (
            <EmptyState title="还没有通知记录" description="监听规则触发后，飞书通知记录会在此处留下记录。" />
          ) : null}
          {!notificationsQuery.isPending && !notificationsQuery.isError && notificationsQuery.data?.length ? (
            <div>{notificationsQuery.data.map((record, index) => <NotificationItem
              key={String(record.id ?? `${record.symbol}-${record.created_at}-${index}`)}
              record={record}
              onRetry={(id) => {
                retryMutation.reset();
                retryMutation.mutate(id);
              }}
              isRetrying={retryMutation.isPending && sameId(retryMutation.variables, record.id)}
            />)}</div>
          ) : null}
          {retryMutation.error ? <p className="mx-4 mb-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs text-negative" role="alert">{errorMessage(retryMutation.error, "通知重试失败，请稍后重试。")}</p> : null}
        </div>

        <div className="min-w-0 rounded-panel border border-line bg-panel shadow-panel">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-primary">监听规则</p>
              <p className="mt-1 text-xs text-muted">规则会复用状态引擎，避免前后端重复判断。</p>
            </div>
            {alertsQuery.data ? <StateTag tone={enabledRuleCount ? "positive" : "neutral"}>{enabledRuleCount} 条启用</StateTag> : null}
          </div>
          {alertsQuery.isPending ? <LoadingState label="正在加载监听规则…" /> : null}
          {alertsQuery.isError ? (
            <QueryErrorState
              error={alertsQuery.error}
              onRetry={() => alertsQuery.refetch()}
              missingTitle="监听规则接口尚未就绪"
              missingDescription="后端尚未提供 Alert CRUD 接口，请完成服务端路由后重试。"
            />
          ) : null}
          {!alertsQuery.isPending && !alertsQuery.isError && rules.length === 0 ? (
            <EmptyState
              title="还没有监听规则"
              description="从一只股票和一个状态开始，建立你的第一条边缘触发规则。"
              action={<button type="button" onClick={openCreateWizard} className="rounded-panel bg-brand px-3.5 py-2 text-xs font-medium text-white hover:bg-brand/90">创建监听规则</button>}
            />
          ) : null}
          {!alertsQuery.isPending && !alertsQuery.isError && rules.length ? (
            <div className="space-y-3 p-3">
              {rules.map((rule) => (
                <AlertCard
                  key={String(rule.id)}
                  rule={rule}
                  onEdit={openEditWizard}
                  onToggle={toggleRule}
                  onDelete={removeRule}
                  isToggling={toggleMutation.isPending && sameId(toggleMutation.variables?.id, rule.id)}
                  isDeleting={deleteMutation.isPending && sameId(deleteMutation.variables, rule.id)}
                />
              ))}
            </div>
          ) : null}
          {toggleMutation.error ? <p className="mx-4 mb-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs text-negative" role="alert">{errorMessage(toggleMutation.error, "后端尚未提供规则启停接口。")}</p> : null}
          {deleteMutation.error ? <p className="mx-4 mb-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs text-negative" role="alert">{errorMessage(deleteMutation.error, "后端尚未提供删除监听规则接口。")}</p> : null}
        </div>
      </section>

      <div className="flex items-start gap-2 rounded-panel border border-warning/20 bg-warning/5 px-3 py-2.5 text-xs leading-5 text-muted">
        <span className="mt-1 size-1.5 shrink-0 rounded-full bg-warning" />
        <p>边缘触发：状态首次进入时通知，持续保持期间不会重复发送；退出后再次进入才会重新通知。</p>
      </div>

      <AlertWizard
        key={`${wizardOpen ? "open" : "closed"}-${editingRule?.id ?? "new"}`}
        open={wizardOpen}
        initialRule={editingRule}
        isSubmitting={createMutation.isPending || updateMutation.isPending}
        submitError={wizardError}
        onClose={() => {
          if (createMutation.isPending || updateMutation.isPending) return;
          setWizardOpen(false);
          setEditingRule(null);
        }}
        onSubmit={submitRule}
      />
    </div>
  );
}
