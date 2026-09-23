import type { ReactNode } from "react";

export function AuthFrame({
  eyebrow,
  title,
  description,
  children,
  footer,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <main className="min-h-screen bg-app px-5 py-8 text-primary sm:px-8 sm:py-12">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-[1080px] items-center justify-center">
        <div className="grid w-full overflow-hidden rounded-panel border border-line bg-panel shadow-panel lg:grid-cols-[minmax(0,1fr)_420px]">
          <section className="hidden border-r border-line bg-card/40 p-8 lg:flex lg:flex-col lg:justify-between lg:p-10">
            <div>
              <div className="flex items-center gap-3">
                <div className="grid size-9 place-items-center rounded-panel bg-brand text-sm font-bold text-white shadow-panel">A</div>
                <div>
                  <p className="text-sm font-semibold tracking-wide text-primary">Apanel</p>
                  <p className="mt-0.5 text-[11px] uppercase tracking-[0.18em] text-muted">Signal workspace</p>
                </div>
              </div>
              <p className="mt-16 max-w-sm text-xs font-medium uppercase tracking-[0.18em] text-brand">Technical indicator workspace</p>
              <h2 className="mt-4 max-w-md text-3xl font-bold leading-tight tracking-tight text-primary">把指标变化压缩成值得关注的状态。</h2>
              <p className="mt-5 max-w-md text-sm leading-6 text-secondary">登录后继续使用高信息密度的股票监控工作台，集中查看变化并创建后续监听。</p>
            </div>
            <p className="text-xs leading-5 text-muted">Apanel 只提供指标监控与通知，不提供买卖建议或自动交易。</p>
          </section>

          <section className="p-6 sm:p-8 lg:p-10">
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              <div className="grid size-9 place-items-center rounded-panel bg-brand text-sm font-bold text-white shadow-panel">A</div>
              <div>
                <p className="text-sm font-semibold tracking-wide text-primary">Apanel</p>
                <p className="mt-0.5 text-[11px] uppercase tracking-[0.18em] text-muted">Signal workspace</p>
              </div>
            </div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">{eyebrow}</p>
            <h1 className="mt-3 text-2xl font-bold tracking-tight text-primary">{title}</h1>
            <p className="mt-2 text-sm leading-6 text-secondary">{description}</p>
            <div className="mt-7">{children}</div>
            <div className="mt-7 border-t border-line pt-5 text-center text-xs text-muted">{footer}</div>
          </section>
        </div>
      </div>
    </main>
  );
}
