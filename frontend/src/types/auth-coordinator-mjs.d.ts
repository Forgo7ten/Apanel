declare module "@/lib/auth-coordinator.mjs" {
  export type AuthStateStatus = "authenticated" | "unauthenticated";

  export type AuthStateMessage = {
    source: string;
    epoch: number;
    status: AuthStateStatus;
    timestamp: number;
  };

  export const AUTH_REFRESH_LEASE_KEY: string;
  export const DEFAULT_AUTH_REFRESH_TIMEOUT_MS: number;
  export const DEFAULT_AUTH_REFRESH_LOCK_TIMEOUT_MS: number;
  export const DEFAULT_AUTH_REFRESH_LEASE_MS: number;
  export const DEFAULT_AUTH_REFRESH_LEASE_HEARTBEAT_MS: number;
  export const DEFAULT_AUTH_REFRESH_LEASE_WAIT_MS: number;
  export const DEFAULT_AUTH_REFRESH_LOCK_NAME: string;

  export type AuthRefreshCoordinatorOptions = {
    locks?: LockManager | null;
    storage?: Storage | null;
    lockName?: string;
    requestTimeoutMs?: number;
    lockTimeoutMs?: number;
    leaseKey?: string;
    leaseMs?: number;
    leaseHeartbeatMs?: number;
    leaseWaitMs?: number;
    now?: () => number;
    random?: () => number;
    setTimeoutFn?: typeof setTimeout;
    clearTimeoutFn?: typeof clearTimeout;
    setIntervalFn?: typeof setInterval;
    clearIntervalFn?: typeof clearInterval;
  };

  export function createAuthRefreshCoordinator(options?: AuthRefreshCoordinatorOptions): {
    run<T>(
      task: (signal: AbortSignal) => Promise<T>,
      options?: { signal?: AbortSignal; requestTimeoutMs?: number; lockTimeoutMs?: number },
    ): Promise<T>;
  };

  export function createRefreshEpochGuard(getEpoch: () => number): {
    initialEpoch: number;
    isCurrent(): boolean;
  };

  export function createAuthStateMessage(input: {
    source: string;
    epoch: number;
    status: AuthStateStatus;
    timestamp?: number;
  }): AuthStateMessage;

  export function isAuthStateMessage(value: unknown): value is AuthStateMessage;
}
