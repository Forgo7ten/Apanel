"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "./AuthProvider";

function AuthLoadingState() {
  return (
    <main className="grid min-h-screen place-items-center bg-app px-6" aria-busy="true" aria-live="polite">
      <div className="flex items-center gap-3 rounded-panel border border-line bg-panel px-4 py-3 text-sm text-secondary shadow-panel">
        <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-brand" />
        正在恢复工作台会话…
      </div>
    </main>
  );
}

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
    }
  }, [router, status]);

  if (status !== "authenticated") {
    return <AuthLoadingState />;
  }

  return <>{children}</>;
}
