import type { ReactNode } from "react";

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-4 grid size-11 place-items-center rounded-panel border border-line bg-panel text-muted">
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13Z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="m8 15 2.5-2.5 2 2 3.5-4M8 8.5h.01" />
        </svg>
      </div>
      <h3 className="text-sm font-semibold text-primary">{title}</h3>
      <p className="mt-2 max-w-sm text-xs leading-5 text-muted">{description}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
