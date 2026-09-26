"use client";

import { ErrorBoundaryFallback } from "@/components/ui/ErrorBoundaryFallback";

export default function WorkspaceError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorBoundaryFallback onRetry={reset} />;
}
