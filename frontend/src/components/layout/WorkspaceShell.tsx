import type { ReactNode } from "react";

import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function WorkspaceShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-app lg:flex">
      <Sidebar />
      <main className="min-w-0 flex-1">
        <Header />
        <div className="mx-auto w-full max-w-[1600px] px-page py-page">{children}</div>
      </main>
    </div>
  );
}
