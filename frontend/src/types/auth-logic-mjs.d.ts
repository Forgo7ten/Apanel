declare module "@/lib/auth-logic.mjs" {
  export function extractInviteToken(hash: string): string | null;
  export function stripUrlFragment(url: string): string;
  export function shouldRefreshAfterUnauthorized(status: number, alreadyRetried: boolean): boolean;
  export function safeAuthErrorMessage(code: unknown, status?: number): string;
}
