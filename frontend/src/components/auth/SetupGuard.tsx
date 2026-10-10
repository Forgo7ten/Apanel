"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getSetupState, type SetupState } from "@/lib/setup";

export function SetupGuard({ children, setupPage = false }: {
  children: React.ReactNode;
  setupPage?: boolean;
}) {
  const router = useRouter();
  const [state, setState] = useState<SetupState | "ERROR" | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getSetupState().then((result) => {
      if (cancelled) return;
      setState(result);
      if (!setupPage && result === "PENDING") router.replace("/setup");
      if (setupPage && result === "COMPLETED") router.replace("/login");
    }).catch(() => { if (!cancelled) setState("ERROR"); });
    return () => { cancelled = true; };
  }, [router, setupPage]);

  if (state === "ERROR") {
    return <main className="grid min-h-screen place-items-center bg-app p-8 text-center text-secondary">
      无法检查初始化状态。请检查后端服务，然后刷新页面。
    </main>;
  }
  if (state === null || state === "NOT_READY"
      || (setupPage && state === "COMPLETED")
      || (!setupPage && state === "PENDING")) {
    return <main className="grid min-h-screen place-items-center bg-app p-8 text-secondary">
      {state === "NOT_READY" ? "初始化任务尚未准备好，请稍后刷新。" : "正在检查系统初始化状态…"}
    </main>;
  }
  return <>{children}</>;
}
