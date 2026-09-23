import { safeAuthErrorMessage } from "@/lib/auth-logic.mjs";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(safeAuthErrorMessage(code, status));
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function toApiError(error: unknown): ApiError {
  if (isApiError(error)) {
    return error;
  }

  return new ApiError(0, "NETWORK_ERROR");
}

export function getResponseErrorCode(payload: unknown, status: number): string {
  if (isRecord(payload)) {
    const error = payload.error;

    if (isRecord(error) && typeof error.code === "string" && error.code.length > 0) {
      return error.code;
    }
  }

  if (status === 401) {
    return "UNAUTHORIZED";
  }

  return status >= 500 ? "INTERNAL_ERROR" : "INVALID_REQUEST";
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export async function readJsonPayload(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return undefined;
  }

  let text: string;

  try {
    text = await response.text();
  } catch {
    throw new ApiError(response.status, "INVALID_RESPONSE");
  }

  if (text.trim().length === 0) {
    return undefined;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(response.status, "INVALID_RESPONSE");
  }
}

export function unwrapSuccess<T>(payload: unknown, status: number): T {
  if (!isRecord(payload) || payload.success !== true) {
    throw new ApiError(status, getResponseErrorCode(payload, status));
  }

  return payload.data as T;
}

export async function readSuccess<T>(response: Response): Promise<T> {
  const payload = await readJsonPayload(response);

  if (!response.ok) {
    throw new ApiError(response.status, getResponseErrorCode(payload, response.status));
  }

  return unwrapSuccess<T>(payload, response.status);
}
