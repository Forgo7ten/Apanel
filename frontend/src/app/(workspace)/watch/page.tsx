import { EmptyState } from "@/components/ui/EmptyState";
import { StateTag } from "@/components/ui/StateTag";

export const metadata = {
  title: "股票监控",
};

export default function WatchPage() {
  return (
    <div className="space-y-6">
      <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Watch workspace</p>
          <h1 className="mt-2 text-title font-bold tracking-tight text-primary">股票监控</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">在一个高密度工作区里扫描价格、指标变化与可关注状态。</p>
        </div>
        <button type="button" disabled className="inline-flex h-9 items-center justify-center gap-2 rounded-panel bg-brand px-3.5 text-sm font-medium text-white opacity-60 shadow-panel" title="Sprint 1 开放">
          <span className="text-base leading-none">+</span>
          添加股票
          <span className="hidden rounded border border-white/20 px-1.5 py-0.5 text-[10px] text-white/70 sm:inline">Sprint 1</span>
        </button>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div className="flex items-center gap-3">
              <div>
                <p className="text-sm font-semibold text-primary">默认监控表</p>
                <p className="mt-1 text-xs text-muted">用于承载自定义股票与指标列</p>
              </div>
              <StateTag tone="neutral">未配置</StateTag>
            </div>
            <span className="text-xs text-muted">0 支股票</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left" aria-label="股票监控表">
              <thead className="h-10 border-b border-line bg-card/50 text-[11px] font-medium uppercase tracking-wide text-secondary">
                <tr>
                  <th className="w-[28%] px-5 font-medium">股票</th>
                  <th className="px-4 font-medium">当前价</th>
                  <th className="px-4 font-medium">指标状态</th>
                  <th className="px-4 font-medium">变化</th>
                  <th className="px-5 font-medium">通知</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td colSpan={5}>
                    <EmptyState title="还没有监控股票" description="Sprint 0 先完成工作台骨架。连接后端后，你可以在这里添加第一只股票。" action={<span className="text-xs text-muted">股票数据将在后续 Sprint 接入</span>} />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <aside className="rounded-panel border border-line bg-panel p-card shadow-panel">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-primary">工作台状态</p>
            <StateTag tone="neutral">状态待接入</StateTag>
          </div>
          <dl className="mt-5 divide-y divide-line/80">
            <div className="flex items-center justify-between py-3 first:pt-0">
              <dt className="text-xs text-muted">市场数据</dt>
              <dd className="text-xs text-secondary">待接入</dd>
            </div>
            <div className="flex items-center justify-between py-3">
              <dt className="text-xs text-muted">指标计算</dt>
              <dd className="text-xs text-secondary">由后端提供</dd>
            </div>
            <div className="flex items-center justify-between py-3 last:pb-0">
              <dt className="text-xs text-muted">刷新策略</dt>
              <dd className="text-xs text-secondary">待配置</dd>
            </div>
          </dl>
          <div className="mt-5 rounded-panel border border-warning/20 bg-warning/5 p-3">
            <p className="text-xs font-medium text-warning">Sprint 0 占位</p>
            <p className="mt-1.5 text-xs leading-5 text-muted">前端仅负责展示与交互，RSI、MACD、BOLL 等指标由后端计算。</p>
          </div>
        </aside>
      </section>
    </div>
  );
}
