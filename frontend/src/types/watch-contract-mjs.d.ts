declare module "@/lib/watch-contract.mjs" {
  export function getSecurityIdentifier(security: unknown): number | string | null;
  export function toAddStockPayload(security: unknown): { security_id: number | string } | null;
}
