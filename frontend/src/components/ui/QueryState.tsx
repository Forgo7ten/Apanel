import { isApiError } from "@/lib/api-errors";

import { EmptyState } from "./EmptyState";

export function LoadingState({ label = "正在加载数据…" }: { label?: string }) {
  return (
    <div className="space-y-3 p-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="grid grid-cols-[minmax(150px,1.4fr)_repeat(3,minmax(100px,1fr))] gap-4 rounded-panel border border-line/70 bg-card/30 px-4 py-4">
          <span className="h-4 animate-pulse rounded bg-line/70" />
          <span className="h-4 animate-pulse rounded bg-line/50" />
          <span className="h-4 animate-pulse rounded bg-line/50" />
          <span className="h-4 animate-pulse rounded bg-line/50" />
        </div>
      ))}
    </div>
  );
}

export function QueryErrorState({
  error,
  onRetry,
  missingTitle = "监控接口尚未就绪",
  missingDescription = "后端尚未提供该监控接口，请完成服务端路由后重试。",
}: {
  error: unknown;
  onRetry: () => void;
  missingTitle?: string;
  missingDescription?: string;
}) {
  const isMissingEndpoint = isApiError(error) && error.status === 404;
  const title = isMissingEndpoint ? missingTitle : "数据加载失败";
  const description = isMissingEndpoint
    ? missingDescription
    : error instanceof Error
      ? error.message
      : "暂时无法获取监控数据，请检查网络后重试。";

  return (
    <EmptyState
      title={title}
      description={description}
      action={
        <button
          type="button"
          onClick={onRetry}
          className="rounded-panel border border-line bg-card px-3 py-2 text-xs font-medium text-secondary transition hover:border-brand/60 hover:text-primary focus:outline-none focus:ring-2 focus:ring-brand/40"
        >
          重新尝试
        </button>
      }
    />
  );
}
