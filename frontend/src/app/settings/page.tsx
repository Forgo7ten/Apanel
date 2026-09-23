import { StateTag } from "@/components/ui/StateTag";

export const metadata = {
  title: "设置",
};

const settingSections = [
  {
    title: "数据口径",
    description: "统一技术指标使用的价格口径。",
    items: [
      ["复权方式", "前复权"],
      ["行情来源", "待接入"],
    ],
  },
  {
    title: "指标偏好",
    description: "选择监控表默认展示的指标与周期。",
    items: [
      ["默认指标", "MA · RSI · BOLL · MACD"],
      ["刷新频率", "待配置"],
    ],
  },
  {
    title: "通知渠道",
    description: "为后续监听规则准备通知出口。",
    items: [["飞书 Webhook", "未配置"]],
  },
] as const;

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <section>
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Workspace settings</p>
        <h1 className="mt-2 text-title font-bold tracking-tight text-primary">设置</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">管理数据口径、指标偏好与通知出口，让工作台保持一致。</p>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="space-y-4">
          {settingSections.map((section) => (
            <section key={section.title} className="rounded-panel border border-line bg-panel shadow-panel">
              <div className="border-b border-line px-4 py-4">
                <h2 className="text-sm font-semibold text-primary">{section.title}</h2>
                <p className="mt-1 text-xs leading-5 text-muted">{section.description}</p>
              </div>
              <dl className="divide-y divide-line/80">
                {section.items.map(([label, value]) => (
                  <div key={label} className="flex flex-col gap-2 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
                    <dt className="text-xs text-secondary">{label}</dt>
                    <dd className="flex items-center gap-3 text-xs text-muted">
                      {value}
                      <button type="button" disabled className="rounded border border-line bg-card px-2 py-1 text-[11px] text-muted opacity-60" title="后续 Sprint 开放">编辑</button>
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>

        <aside className="h-fit rounded-panel border border-line bg-panel p-card shadow-panel">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-primary">环境状态</p>
            <StateTag tone="neutral">状态待接入</StateTag>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted">当前页面只提供前端设置入口。账号隔离、数据与通知配置将在对应 Sprint 接入。</p>
          <div className="mt-5 border-t border-line pt-4">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted">版本</span>
              <span className="font-mono text-secondary">v0.1.0</span>
            </div>
            <div className="mt-3 flex items-center justify-between text-xs">
              <span className="text-muted">前端状态端点</span>
              <span className="font-mono text-positive">/api/health</span>
            </div>
          </div>
        </aside>
      </section>
    </div>
  );
}
