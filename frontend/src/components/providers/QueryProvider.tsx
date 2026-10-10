"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";

import { sessionScope, SessionChangedError } from "@/lib/session-scope.mjs";

function AccountQueries({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        retry: (count, error) => !(error instanceof SessionChangedError) && count < 1,
        staleTime: 30_000,
      },
    },
  }));
  useEffect(() => () => {
    void queryClient.cancelQueries();
    queryClient.clear();
  }, [queryClient]);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const serverVersion = () => 0;
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const version = useSyncExternalStore(sessionScope.subscribe, sessionScope.version, serverVersion);
  // A late callback can only hold the OLD QueryClient. It cannot repopulate a new account's cache.
  return <AccountQueries key={version}>{children}</AccountQueries>;
}
