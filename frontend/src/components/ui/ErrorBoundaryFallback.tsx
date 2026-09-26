"use client";

import { errorBoundaryCopy } from "@/lib/watch-contract.mjs";

export function ErrorBoundaryFallback({ onRetry }: { onRetry: () => void }) {
  const copy = errorBoundaryCopy();
  return (
    <main className="grid min-h-[60vh] place-items-center px-6 py-12" role="alert">
      <section className="w-full max-w-md rounded-panel border border-line bg-panel p-6 text-center shadow-panel" aria-labelledby="error-boundary-title">
        <h1 id="error-boundary-title" className="text-base font-semibold text-primary">{copy.title}</h1>
        <p className="mt-2 text-sm leading-6 text-secondary">{copy.description}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-5 rounded-panel bg-brand px-3 py-2 text-xs font-medium text-white transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/40"
        >
          {copy.retryLabel}
        </button>
      </section>
    </main>
  );
}
