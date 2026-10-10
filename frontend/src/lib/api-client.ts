import { sessionScope, type ScopeSnapshot } from "@/lib/session-scope.mjs";
import { shouldRefreshAfterUnauthorized } from "@/lib/auth-logic.mjs";

import { ApiError, getResponseErrorCode, readJsonPayload, unwrapSuccess } from "./api-errors";
import { apiPath } from "./api-path";
import { clearSession, getAccessToken, refreshSession } from "./auth-session";

type ApiRequestOptions = RequestInit & {
  retryOnUnauthorized?: boolean;
};

function getHeaders(init: RequestInit): Headers {
  const headers = new Headers(init.headers);

  headers.set("Accept", "application/json");

  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const accessToken = getAccessToken();

  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  } else {
    headers.delete("Authorization");
  }

  return headers;
}

async function refreshOnce(): Promise<boolean> {
  const session = await refreshSession();
  return session !== null;
}

async function request<T>(path: string, init: RequestInit, alreadyRetried: boolean, scope: ScopeSnapshot): Promise<T> {
  sessionScope.assertCurrent(scope);
  let response: Response;

  try {
    response = await fetch(apiPath(path), {
      ...init,
      credentials: init.credentials ?? "include",
      headers: getHeaders(init),
      signal: init.signal ? AbortSignal.any([init.signal, scope.signal]) : scope.signal,
    });
  } catch {
    sessionScope.assertCurrent(scope);
    throw new ApiError(0, "NETWORK_ERROR");
  }

  sessionScope.assertCurrent(scope);
  if (shouldRefreshAfterUnauthorized(response.status, alreadyRetried)) {
    const refreshed = await refreshOnce();
    sessionScope.assertCurrent(scope);

    if (refreshed) {
      return request<T>(path, init, true, scope);
    }

    clearSession();
    throw new ApiError(401, "UNAUTHORIZED");
  }

  let payload: unknown;

  try {
    payload = await readJsonPayload(response);
  } catch (error) {
    sessionScope.assertCurrent(scope);
    if (response.status === 401) {
      clearSession();
    }
    throw error;
  }

  sessionScope.assertCurrent(scope);
  if (!response.ok) {
    if (response.status === 401) {
      clearSession();
    }

    throw new ApiError(response.status, getResponseErrorCode(payload, response.status));
  }

  return unwrapSuccess<T>(payload, response.status);
}

export async function apiFetch<T>(path: string, init: ApiRequestOptions = {}): Promise<T> {
  const { retryOnUnauthorized = true, ...requestInit } = init;

  return request<T>(path, requestInit, !retryOnUnauthorized, sessionScope.capture());
}
