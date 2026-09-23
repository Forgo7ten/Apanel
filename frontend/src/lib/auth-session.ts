import { safeAuthErrorMessage } from "@/lib/auth-logic.mjs";
import {
  AUTH_REFRESH_LEASE_KEY,
  DEFAULT_AUTH_REFRESH_LEASE_HEARTBEAT_MS,
  DEFAULT_AUTH_REFRESH_LEASE_MS,
  DEFAULT_AUTH_REFRESH_LEASE_WAIT_MS,
  DEFAULT_AUTH_REFRESH_LOCK_NAME,
  DEFAULT_AUTH_REFRESH_LOCK_TIMEOUT_MS,
  DEFAULT_AUTH_REFRESH_TIMEOUT_MS,
  createAuthRefreshCoordinator,
  createAuthStateMessage,
  createRefreshEpochGuard,
  isAuthStateMessage,
  type AuthStateMessage,
} from "@/lib/auth-coordinator.mjs";

import { ApiError, getResponseErrorCode, isRecord, readJsonPayload, unwrapSuccess } from "./api-errors";
import { apiPath } from "./api-path";

export type AuthUser = {
  id: number | string;
  username: string;
  email?: string | null;
  status?: string | null;
};

export type AuthSession = {
  accessToken: string;
  user: AuthUser;
};

type AuthPayload = Record<string, unknown>;
type FetchLike = typeof fetch;

const AUTH_CHANNEL_NAME = "apanel-auth";
const REFRESH_LOCK_NAME = DEFAULT_AUTH_REFRESH_LOCK_NAME;
const REFRESH_REQUEST_TIMEOUT_MS = DEFAULT_AUTH_REFRESH_TIMEOUT_MS;
const REFRESH_LOCK_TIMEOUT_MS = DEFAULT_AUTH_REFRESH_LOCK_TIMEOUT_MS;
const REFRESH_LEASE_MS = DEFAULT_AUTH_REFRESH_LEASE_MS;
const REFRESH_LEASE_HEARTBEAT_MS = DEFAULT_AUTH_REFRESH_LEASE_HEARTBEAT_MS;
const REFRESH_LEASE_WAIT_MS = DEFAULT_AUTH_REFRESH_LEASE_WAIT_MS;

type AuthBroadcastMessage = AuthStateMessage;
type RefreshOutcome = {
  session: AuthSession | null;
  discarded: boolean;
  published: boolean;
  generation: number;
};

let currentSession: AuthSession | null = null;
const sessionListeners = new Set<(session: AuthSession | null) => void>();
let sessionGeneration = 0;
let sessionEpoch = Date.now();
let refreshInFlight: Promise<RefreshOutcome> | null = null;
let activeRefreshController: AbortController | null = null;
const tabSource = `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function abortActiveRefresh(): void {
  if (!activeRefreshController || activeRefreshController.signal.aborted) {
    return;
  }

  const reason = new Error("Authentication session changed.");
  reason.name = "AbortError";
  activeRefreshController.abort(reason);
}

function getRefreshStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

const refreshCoordinator = createAuthRefreshCoordinator({
  locks: typeof navigator !== "undefined" && typeof navigator.locks?.request === "function" ? navigator.locks : null,
  storage: getRefreshStorage(),
  lockName: REFRESH_LOCK_NAME,
  requestTimeoutMs: REFRESH_REQUEST_TIMEOUT_MS,
  lockTimeoutMs: REFRESH_LOCK_TIMEOUT_MS,
  leaseKey: AUTH_REFRESH_LEASE_KEY,
  leaseMs: REFRESH_LEASE_MS,
  leaseHeartbeatMs: REFRESH_LEASE_HEARTBEAT_MS,
  leaseWaitMs: REFRESH_LEASE_WAIT_MS,
});

function createAuthChannel(): BroadcastChannel | null {
  if (typeof window === "undefined" || typeof window.BroadcastChannel !== "function") {
    return null;
  }

  let channel: BroadcastChannel;
  try {
    channel = new window.BroadcastChannel(AUTH_CHANNEL_NAME);
  } catch {
    return null;
  }

  channel.addEventListener("message", (event: MessageEvent<unknown>) => {
    const message = event.data;

    if (!isAuthStateMessage(message) || message.source === tabSource) {
      return;
    }

    sessionEpoch = Math.max(sessionEpoch, message.epoch);

    if (message.status === "unauthenticated") {
      // A clear signal is deliberately fail-closed and immediate. It also
      // advances the local generation so every older refresh is discarded.
      abortActiveRefresh();
      currentSession = null;
      sessionGeneration = Math.max(sessionGeneration, message.epoch) + 1;
      notifySessionListeners();
      return;
    }

    // Authenticated messages carry no credentials. They invalidate work
    // started before the new epoch; this tab obtains its own token later by
    // refreshing under the same lock.
    if (message.epoch > sessionGeneration) {
      sessionGeneration = message.epoch;
    }
  });
  return channel;
}

const authChannel = createAuthChannel();

export function getSession(): AuthSession | null {
  return currentSession;
}

export function getAccessToken(): string | null {
  return currentSession?.accessToken ?? null;
}

export function setSession(session: AuthSession): AuthSession {
  abortActiveRefresh();
  applySession(session, true);
  return session;
}

export function clearSession(): void {
  abortActiveRefresh();
  applySession(null, true);
}

export function subscribeToSession(listener: (session: AuthSession | null) => void): () => void {
  sessionListeners.add(listener);

  return () => {
    sessionListeners.delete(listener);
  };
}

function notifySessionListeners(): void {
  for (const listener of sessionListeners) {
    listener(currentSession);
  }
}

function applySession(session: AuthSession | null, broadcast: boolean): void {
  currentSession = session;
  sessionGeneration += 1;
  sessionEpoch = Math.max(sessionEpoch + 1, Date.now());
  notifySessionListeners();

  if (broadcast) {
    broadcastAuthState(session ? "authenticated" : "unauthenticated", sessionEpoch);
  }
}

function broadcastAuthState(status: AuthBroadcastMessage["status"], epoch = sessionEpoch): void {
  if (!authChannel) {
    return;
  }

  try {
    const message = createAuthStateMessage({
      source: tabSource,
      epoch,
      status,
      timestamp: Date.now(),
    });
    authChannel.postMessage(message);
  } catch {
    // BroadcastChannel is an optional optimization. Local state remains safe.
  }
}

function normalizeUser(value: unknown): AuthUser | null {
  if (!isRecord(value)) {
    return null;
  }

  const id = value.id;
  const username = value.username;

  if ((typeof id !== "number" && typeof id !== "string") || typeof username !== "string" || username.trim() === "") {
    return null;
  }

  return {
    id,
    username,
    email: typeof value.email === "string" ? value.email : null,
    status: typeof value.status === "string" ? value.status : null,
  };
}

function getPayloadUser(data: unknown): AuthUser | null {
  if (!isRecord(data)) {
    return null;
  }

  return normalizeUser(data.user) ?? normalizeUser(data);
}

function getPayloadToken(data: unknown): string | null {
  if (!isRecord(data)) {
    return null;
  }

  const token = data.access_token ?? data.accessToken;
  return typeof token === "string" && token.trim() !== "" ? token : null;
}

function authHeaders(accessToken?: string): HeadersInit {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  return headers;
}

async function fetchWithSignal(
  fetchImpl: FetchLike,
  input: RequestInfo | URL,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<Response> {
  if (!signal) {
    return fetchImpl(input, init);
  }

  if (signal.aborted) {
    throw signal.reason ?? new Error("Authentication request aborted.");
  }

  return new Promise<Response>((resolve, reject) => {
    let settled = false;

    const cleanup = () => {
      signal.removeEventListener("abort", onAbort);
    };

    const onAbort = () => {
      if (settled) {
        return;
      }

      settled = true;
      cleanup();
      reject(signal.reason ?? new Error("Authentication request aborted."));
    };

    signal.addEventListener("abort", onAbort, { once: true });

    let pending: Promise<Response>;
    try {
      pending = Promise.resolve(fetchImpl(input, init));
    } catch (error) {
      if (!settled) {
        settled = true;
        cleanup();
        reject(error);
      }
      return;
    }

    pending.then(
      (response) => {
        if (settled) {
          return;
        }

        settled = true;
        cleanup();
        resolve(response);
      },
      (error) => {
        if (settled) {
          return;
        }

        settled = true;
        cleanup();
        reject(error);
      },
    );
  });
}

async function awaitWithSignal<T>(value: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) {
    return value;
  }

  if (signal.aborted) {
    throw signal.reason ?? new Error("Authentication request aborted.");
  }

  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const onAbort = () => {
      if (settled) {
        return;
      }

      settled = true;
      signal.removeEventListener("abort", onAbort);
      reject(signal.reason ?? new Error("Authentication request aborted."));
    };

    signal.addEventListener("abort", onAbort, { once: true });
    value.then(
      (result) => {
        if (settled) {
          return;
        }

        settled = true;
        signal.removeEventListener("abort", onAbort);
        resolve(result);
      },
      (error) => {
        if (settled) {
          return;
        }

        settled = true;
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

async function requestAuth<T>(path: string, init: RequestInit, fetchImpl: FetchLike, signal?: AbortSignal): Promise<T> {
  let response: Response;

  try {
    const headers = new Headers(authHeaders());
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));

    response = await fetchWithSignal(fetchImpl, apiPath(path), {
      ...init,
      credentials: "include",
      headers,
      signal,
    }, signal);
  } catch {
    throw new ApiError(0, "NETWORK_ERROR");
  }

  const payload = await awaitWithSignal(readJsonPayload(response), signal);

  if (!response.ok) {
    throw new ApiError(response.status, getResponseErrorCode(payload, response.status));
  }

  return unwrapSuccess<T>(payload, response.status);
}

async function fetchCurrentUser(accessToken: string, fetchImpl: FetchLike, signal?: AbortSignal): Promise<AuthUser | null> {
  let response: Response;

  try {
    response = await fetchWithSignal(fetchImpl, apiPath("/auth/me"), {
      method: "GET",
      credentials: "include",
      headers: authHeaders(accessToken),
      signal,
    }, signal);
  } catch {
    throw new ApiError(0, "NETWORK_ERROR");
  }

  const data = await requestResponseData(response, signal);
  return getPayloadUser(data);
}

async function requestResponseData(response: Response, signal?: AbortSignal): Promise<unknown> {
  const payload = await awaitWithSignal(readJsonPayload(response), signal);

  if (!response.ok) {
    throw new ApiError(response.status, "UNAUTHORIZED");
  }

  return unwrapSuccess<unknown>(payload, response.status);
}

async function createSessionFromPayload(
  data: unknown,
  fetchImpl: FetchLike,
  signal?: AbortSignal,
): Promise<AuthSession | null> {
  const accessToken = getPayloadToken(data);

  if (!accessToken) {
    return null;
  }

  const user = getPayloadUser(data) ?? (await fetchCurrentUser(accessToken, fetchImpl, signal));

  if (!user) {
    return null;
  }

  return { accessToken, user };
}

function getFetch(fetchImpl?: FetchLike): FetchLike {
  return fetchImpl ?? globalThis.fetch.bind(globalThis);
}

function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(signal.reason ?? new Error("Authentication refresh aborted."));
  }

  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(signal?.reason ?? new Error("Authentication refresh aborted."));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);

    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function performRefresh(request: FetchLike, signal: AbortSignal): Promise<AuthSession | null> {
  const data = await requestAuth<AuthPayload>("/auth/refresh", { method: "POST" }, request, signal);
  return createSessionFromPayload(data, request, signal);
}

export function refreshSession(
  fetchImpl?: FetchLike,
  options: { commit?: boolean } = {},
): Promise<AuthSession | null> {
  const request = getFetch(fetchImpl);
  const commit = options.commit ?? true;

  const applyRefreshOutcome = (outcome: RefreshOutcome): AuthSession | null => {
    const isCurrentOutcome = !outcome.discarded && sessionGeneration === outcome.generation;

    if (isCurrentOutcome && commit) {
      applySession(outcome.session, !outcome.published);
    }

    // The request may resolve after logout (or another tab's clear message)
    // has advanced the generation. Never return that late result to a caller:
    // AuthProvider intentionally uses commit:false during bootstrap and would
    // otherwise publish the stale session back into memory.
    return isCurrentOutcome ? outcome.session : currentSession;
  };

  if (refreshInFlight) {
    return refreshInFlight.then(applyRefreshOutcome);
  }

  const epochGuard = createRefreshEpochGuard(() => sessionGeneration);
  const generationAtStart = epochGuard.initialEpoch;
  const refreshController = new AbortController();
  activeRefreshController = refreshController;
  const operation = (async (): Promise<RefreshOutcome> => {
    try {
      const result = await refreshCoordinator.run(
        async (signal) => {
          // Let a previous lock holder's invalidation message reach this tab
          // before deciding whether a network request is still needed.
          await delay(0, signal);

          if (!epochGuard.isCurrent()) {
            return { session: null, discarded: true, published: false };
          }

          const session = await performRefresh(request, signal);
          if (!epochGuard.isCurrent() || signal.aborted) {
            return { session: null, discarded: true, published: false };
          }

          // Publish only the new epoch/status while the coordination lock is
          // held. The access token remains local to this tab.
          sessionEpoch = Math.max(sessionEpoch + 1, Date.now());
          broadcastAuthState(session ? "authenticated" : "unauthenticated", sessionEpoch);
          return { session, discarded: false, published: true };
        },
        {
          signal: refreshController.signal,
          requestTimeoutMs: REFRESH_REQUEST_TIMEOUT_MS,
          lockTimeoutMs: REFRESH_LOCK_TIMEOUT_MS,
        },
      );

      if (result.discarded || !epochGuard.isCurrent()) {
        return { session: null, discarded: true, published: false, generation: generationAtStart };
      }

      return {
        session: result.session,
        discarded: false,
        published: result.published,
        generation: generationAtStart,
      };
    } catch {
      return {
        session: null,
        discarded: !epochGuard.isCurrent(),
        published: false,
        generation: generationAtStart,
      };
    } finally {
      if (activeRefreshController === refreshController) {
        activeRefreshController = null;
      }
    }
  })();

  let trackedOperation: Promise<RefreshOutcome>;
  trackedOperation = operation.finally(() => {
    if (refreshInFlight === trackedOperation) {
      refreshInFlight = null;
    }
  });
  refreshInFlight = trackedOperation;
  return trackedOperation.then(applyRefreshOutcome);
}

export async function login(username: string, password: string, fetchImpl?: FetchLike): Promise<AuthSession> {
  const request = getFetch(fetchImpl);
  const data = await requestAuth<AuthPayload>(
    "/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    request,
  );

  const session = await createSessionFromPayload(data, request);

  if (session) {
    setSession(session);
    return session;
  }

  throw new ApiError(502, "INVALID_RESPONSE");
}

export async function register(
  username: string,
  password: string,
  inviteToken: string | null,
  fetchImpl?: FetchLike,
): Promise<AuthSession> {
  const request = getFetch(fetchImpl);
  const data = await requestAuth<AuthPayload>(
    "/auth/register",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, invite_token: inviteToken }),
    },
    request,
  );

  const session = await createSessionFromPayload(data, request);

  if (session) {
    setSession(session);
    return session;
  }

  // Registration creates the account first; the current backend intentionally
  // does not issue a session from that endpoint. Log in through the normal
  // token endpoint so the refresh cookie is established there.
  return login(username, password, request);
}

export async function logout(fetchImpl?: FetchLike): Promise<void> {
  const request = getFetch(fetchImpl);
  const accessToken = getAccessToken();

  // Invalidate local work before the network round trip. A refresh that is
  // already in flight must not be allowed to restore the logged-out session.
  clearSession();

  try {
    await request(apiPath("/auth/logout"), {
      method: "POST",
      credentials: "include",
      headers: authHeaders(accessToken ?? undefined),
    });
  } catch {
    // Local credentials must still be removed when the network is unavailable.
  }
}

export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }

  return safeAuthErrorMessage("NETWORK_ERROR");
}
