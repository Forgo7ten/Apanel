import { EmptyState } from "@/components/ui/EmptyState";
import { StateTag } from "@/components/ui/StateTag";

export const metadata = {
  title: "通知",
};

export default function NotificationsPage() {
  return (
    <div className="space-y-6">
      <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Notification center</p>
          <h1 className="mt-2 text-title font-bold tracking-tight text-primary">通知</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">集中查看触发记录，并管理需要关注的指标状态。</p>
        </div>
        <button type="button" disabled className="inline-flex h-9 items-center justify-center gap-2 rounded-panel border border-line bg-card px-3.5 text-sm font-medium text-secondary opacity-60" title="Sprint 6 开放">
          新建监听
          <span className="rounded border border-line px-1.5 py-0.5 text-[10px] text-muted">Sprint 6</span>
        </button>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-primary">通知记录</p>
              <p className="mt-1 text-xs text-muted">边缘触发后产生的通知会出现在这里</p>
            </div>
            <StateTag tone="neutral">暂无记录</StateTag>
          </div>
          <EmptyState title="还没有通知记录" description="完成监控表与监听规则配置后，指标状态变化会在此处留下记录。" />
        </div>

        <aside className="rounded-panel border border-line bg-panel p-card shadow-panel">
          <p className="text-sm font-semibold text-primary">监听规则</p>
          <p className="mt-1 text-xs leading-5 text-muted">规则会复用状态引擎，避免前后端重复判断。</p>
          <div className="mt-5 rounded-panel border border-line bg-card p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-secondary">已启用规则</span>
              <span className="font-mono text-lg text-primary">0</span>
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-line">
              <div className="h-full w-0 rounded-full bg-brand" />
            </div>
          </div>
          <div className="mt-4 flex items-start gap-2 text-xs leading-5 text-muted">
            <span className="mt-1 size-1.5 shrink-0 rounded-full bg-warning" />
            <p>边缘触发：状态首次进入时通知，持续保持期间不会重复发送。</p>
          </div>
        </aside>
      </section>
    </div>
  );
}
