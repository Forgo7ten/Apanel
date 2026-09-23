declare module "@/lib/health.mjs" {
  export function createHealthPayload(now?: Date): {
    status: "ok";
    service: "frontend";
    timestamp: string;
  };
}
