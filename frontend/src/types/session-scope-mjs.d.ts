declare module "@/lib/session-scope.mjs" {
  export type ScopeSnapshot = Readonly<{ version: number; userId: string | null; signal: AbortSignal }>;
  export class SessionChangedError extends Error {}
  export type SessionScope = {
    capture(): ScopeSnapshot;
    version(): number;
    subscribe(listener: () => void): () => void;
    transition(userId: string | number | null | undefined, force?: boolean): ScopeSnapshot;
    assertCurrent(captured: ScopeSnapshot): void;
  };
  export function createSessionScope(): SessionScope;
  export const sessionScope: SessionScope;
}
