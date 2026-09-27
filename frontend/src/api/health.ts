import type { BackendHealth } from "./types";
import { ApiError, isRecord, readJsonPayload } from "@/lib/api-errors";
import { apiPath } from "@/lib/api-path";

function isBackendHealth(value: unknown): value is BackendHealth {
  if (!isRecord(value)) return false;
  if (typeof value.service !== "string" || typeof value.version !== "string") return false;
  if (value.status !== "healthy" && value.status !== "degraded") return false;
  if (!isRecord(value.dependencies)) return false;

  return Object.values(value.dependencies).every((dependency) => (
    isRecord(dependency)
    && (dependency.status === "healthy" || dependency.status === "unhealthy")
  ));
}

export async function getBackendHealth(): Promise<BackendHealth> {
  let response: Response;

  try {
    response = await fetch(apiPath("/health"), {
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR");
  }

  const payload = await readJsonPayload(response);
  if (!isRecord(payload) || !isBackendHealth(payload.data)) {
    throw new ApiError(response.status, "INVALID_RESPONSE");
  }

  // /health intentionally returns HTTP 503 with a valid snapshot when one
  // infrastructure dependency is unhealthy. Treat that as health data rather
  // than a transport failure so the UI can distinguish degraded vs unreachable.
  if (response.ok || response.status === 503) {
    return payload.data;
  }

  throw new ApiError(response.status, "HEALTH_CHECK_FAILED");
}
