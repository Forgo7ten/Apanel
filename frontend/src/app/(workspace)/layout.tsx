import type { ReactNode } from "react";

import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { WorkspaceShell } from "@/components/layout/WorkspaceShell";

// Workspace pages depend on a browser-only in-memory session. Do not let a
// static HTML cache turn an authenticated shell into shared public output.
export const dynamic = "force-dynamic";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <ProtectedRoute>
      <WorkspaceShell>{children}</WorkspaceShell>
    </ProtectedRoute>
  );
}
