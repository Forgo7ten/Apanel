/**
 * Browser-independent refresh coordination primitives.
 *
 * The access token deliberately never crosses this seam.  A tab that needs a
 * token performs its own refresh while holding the shared coordination lock;
 * only the HttpOnly refresh cookie is shared by the browser.
 */

export const AUTH_REFRESH_LEASE_KEY = "apanel-auth-refresh-lease";
export const DEFAULT_AUTH_REFRESH_TIMEOUT_MS = 5_000;
export const DEFAULT_AUTH_REFRESH_LOCK_TIMEOUT_MS = 4_000;
export const DEFAULT_AUTH_REFRESH_LEASE_MS = 10_000;
export const DEFAULT_AUTH_REFRESH_LEASE_HEARTBEAT_MS = 2_000;
export const DEFAULT_AUTH_REFRESH_LEASE_WAIT_MS = 50;
export const DEFAULT_AUTH_REFRESH_LOCK_NAME = "apanel-auth-refresh";

const STORAGE_UNAVAILABLE = Symbol("storage-unavailable");
let ownerSequence = 0;

function createAbortError(message = "Authentication refresh timed out.") {
  const error = new Error(message);
  error.name = "AbortError";
  return error;
}

function signalReason(signal) {
  return signal.reason ?? createAbortError();
}

function abortController(controller, reason) {
  if (!controller.signal.aborted) {
    controller.abort(reason ?? createAbortError());
  }
}

function mergeSignals(signals) {
  const controller = new AbortController();
  const cleanups = [];

  const abortFrom = (signal) => {
    abortController(controller, signalReason(signal));
  };

  for (const signal of signals) {
    if (!signal) {
      continue;
    }

    if (signal.aborted) {
      abortFrom(signal);
      break;
    }

    const listener = () => abortFrom(signal);
    signal.addEventListener("abort", listener, { once: true });
    cleanups.push(() => signal.removeEventListener("abort", listener));
  }

  return {
    signal: controller.signal,
    cleanup() {
      for (const cleanup of cleanups) {
        cleanup();
      }
    },
  };
}

function waitFor(milliseconds, signal, setTimeoutFn, clearTimeoutFn) {
  if (signal.aborted) {
    return Promise.reject(signalReason(signal));
  }

  return new Promise((resolve, reject) => {
    let timer;
    const onAbort = () => {
      clearTimeoutFn(timer);
      signal.removeEventListener("abort", onAbort);
      reject(signalReason(signal));
    };
    timer = setTimeoutFn(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);

    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function awaitWithSignal(value, signal) {
  if (signal.aborted) {
    return Promise.reject(signalReason(signal));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    const onAbort = () => {
      if (settled) {
        return;
      }

      settled = true;
      signal.removeEventListener("abort", onAbort);
      reject(signalReason(signal));
    };

    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(value).then(
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

function readLease(storage, leaseKey) {
  const raw = storage.getItem(leaseKey);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw);
    if (
      !parsed ||
      typeof parsed !== "object" ||
      typeof parsed.owner !== "string" ||
      parsed.owner.length === 0 ||
      typeof parsed.expiresAt !== "number" ||
      !Number.isFinite(parsed.expiresAt)
    ) {
      return null;
    }

    return { owner: parsed.owner, expiresAt: parsed.expiresAt };
  } catch {
    return null;
  }
}

function safeReadLease(storage, leaseKey) {
  try {
    return { ok: true, lease: readLease(storage, leaseKey) };
  } catch {
    return { ok: false, lease: null };
  }
}

function releaseLease(storage, leaseKey, owner) {
  try {
    const current = readLease(storage, leaseKey);
    if (current?.owner === owner) {
      storage.removeItem(leaseKey);
    }
  } catch {
    // Storage can disappear while a page is closing. Never retry the refresh.
  }
}

async function runWithStorageLease({
  task,
  signal,
  abortLease,
  onLeaseAcquired,
  storage,
  leaseKey,
  leaseMs,
  leaseHeartbeatMs,
  leaseWaitMs,
  now,
  random,
  setIntervalFn,
  clearIntervalFn,
  setTimeoutFn,
  clearTimeoutFn,
}) {
  // Time and Math.random are injectable for deterministic tests and can be
  // equal in two contexts. Add a per-realm sequence so coordinators created
  // together never accidentally share an owner identity.
  const owner = `${now()}-${random().toString(36).slice(2)}-${ownerSequence++}`;

  while (!signal.aborted) {
    const current = safeReadLease(storage, leaseKey);
    if (!current.ok) {
      return STORAGE_UNAVAILABLE;
    }

    const currentTime = now();
    if (!current.lease || current.lease.expiresAt <= currentTime) {
      const candidate = { owner, expiresAt: currentTime + leaseMs };

      try {
        storage.setItem(leaseKey, JSON.stringify(candidate));
      } catch {
        return STORAGE_UNAVAILABLE;
      }

      const confirmed = safeReadLease(storage, leaseKey);
      if (!confirmed.ok) {
        return STORAGE_UNAVAILABLE;
      }

      if (confirmed.lease?.owner === owner) {
        onLeaseAcquired();
        let heartbeatTimer = null;

        const heartbeat = () => {
          if (signal.aborted) {
            return;
          }

          const latest = safeReadLease(storage, leaseKey);
          if (!latest.ok || latest.lease?.owner !== owner || latest.lease.expiresAt <= now()) {
            abortLease(createAbortError("Authentication refresh lease was lost."));
            return;
          }

          try {
            storage.setItem(leaseKey, JSON.stringify({ owner, expiresAt: now() + leaseMs }));

            // A localStorage lease has no compare-and-swap primitive. Verify
            // the write as well as the read before it so a competing context
            // cannot silently replace this owner while the request is alive.
            const renewed = safeReadLease(storage, leaseKey);
            if (!renewed.ok || renewed.lease?.owner !== owner) {
              abortLease(createAbortError("Authentication refresh lease was lost."));
            }
          } catch {
            abortLease(createAbortError("Authentication refresh lease is unavailable."));
          }
        };

        heartbeatTimer = setIntervalFn(heartbeat, leaseHeartbeatMs);

        try {
          const result = await awaitWithSignal(task(signal), signal);
          if (signal.aborted) {
            throw signalReason(signal);
          }

          const stillOwned = safeReadLease(storage, leaseKey);
          if (!stillOwned.ok || stillOwned.lease?.owner !== owner || stillOwned.lease.expiresAt <= now()) {
            throw createAbortError("Authentication refresh lease was lost.");
          }

          return result;
        } finally {
          if (heartbeatTimer !== null) {
            clearIntervalFn(heartbeatTimer);
          }
          // If the request was aborted, keep the owner record until its
          // expiry. The underlying server request may still be unwinding, so
          // releasing early could let another tab present the old cookie.
          if (!signal.aborted) {
            releaseLease(storage, leaseKey, owner);
          }
        }
      }
    }

    await waitFor(leaseWaitMs, signal, setTimeoutFn, clearTimeoutFn);
  }

  throw signalReason(signal);
}

/**
 * Create a coordinator with injectable browser capabilities and timers.
 * `task` receives the signal that covers lock wait, lease ownership, and the
 * refresh request itself. A timeout is therefore a terminal attempt, never a
 * reason to issue a second refresh.
 */
export function createAuthRefreshCoordinator(options = {}) {
  const {
    locks = null,
    storage = null,
    lockName = DEFAULT_AUTH_REFRESH_LOCK_NAME,
    requestTimeoutMs = DEFAULT_AUTH_REFRESH_TIMEOUT_MS,
    lockTimeoutMs = DEFAULT_AUTH_REFRESH_LOCK_TIMEOUT_MS,
    leaseKey = AUTH_REFRESH_LEASE_KEY,
    leaseMs = DEFAULT_AUTH_REFRESH_LEASE_MS,
    leaseHeartbeatMs = Math.min(DEFAULT_AUTH_REFRESH_LEASE_HEARTBEAT_MS, Math.floor(leaseMs / 3)),
    leaseWaitMs = DEFAULT_AUTH_REFRESH_LEASE_WAIT_MS,
    now = () => Date.now(),
    random = () => Math.random(),
    setTimeoutFn = globalThis.setTimeout,
    clearTimeoutFn = globalThis.clearTimeout,
    setIntervalFn = globalThis.setInterval,
    clearIntervalFn = globalThis.clearInterval,
  } = options;

  async function run(task, runOptions = {}) {
    const externalSignal = runOptions.signal;
    const operationController = new AbortController();
    const operationTimer = setTimeoutFn(() => {
      abortController(operationController, createAbortError());
    }, runOptions.requestTimeoutMs ?? requestTimeoutMs);

    const directSignals = mergeSignals([operationController.signal, externalSignal]);

    const runDirect = async () => {
      try {
        // Race the task against the operation timeout. Browser fetch normally
        // honors AbortSignal, but this also bounds injected/test transports
        // that ignore it and prevents a late result from being published.
        return await awaitWithSignal(task(directSignals.signal), directSignals.signal);
      } finally {
        directSignals.cleanup();
      }
    };

    try {
      if (locks && typeof locks.request === "function") {
        const lockController = new AbortController();
        const lockTimer = setTimeoutFn(() => {
          abortController(lockController, createAbortError("Authentication refresh lock timed out."));
        }, runOptions.lockTimeoutMs ?? lockTimeoutMs);
        const lockSignals = mergeSignals([operationController.signal, lockController.signal, externalSignal]);

        try {
          const lockRequest = locks.request(
            lockName,
            { mode: "exclusive", signal: lockSignals.signal },
            async () => {
              clearTimeoutFn(lockTimer);
              if (lockSignals.signal.aborted) {
                throw signalReason(lockSignals.signal);
              }
              return await awaitWithSignal(task(lockSignals.signal), lockSignals.signal);
            },
          );
          return await awaitWithSignal(lockRequest, lockSignals.signal);
        } finally {
          clearTimeoutFn(lockTimer);
          lockSignals.cleanup();
        }
      }

      if (storage && typeof storage.getItem === "function" && typeof storage.setItem === "function") {
        const leaseController = new AbortController();
        const leaseTimer = setTimeoutFn(() => {
          abortController(leaseController, createAbortError("Authentication refresh lease wait timed out."));
        }, runOptions.lockTimeoutMs ?? lockTimeoutMs);
        const leaseSignals = mergeSignals([operationController.signal, leaseController.signal, externalSignal]);

        try {
          const result = await runWithStorageLease({
            task,
            signal: leaseSignals.signal,
            abortLease: (reason) => abortController(leaseController, reason),
            onLeaseAcquired: () => clearTimeoutFn(leaseTimer),
            storage,
            leaseKey,
            leaseMs,
            leaseHeartbeatMs: Math.max(1, leaseHeartbeatMs),
            leaseWaitMs,
            now,
            random,
            setIntervalFn,
            clearIntervalFn,
            setTimeoutFn,
            clearTimeoutFn,
          });

          if (result !== STORAGE_UNAVAILABLE) {
            return result;
          }

          // Private browsing and disabled storage are normal browser states.
          // Fall back to one bounded attempt; never retry after a failure.
          return await runDirect();
        } finally {
          clearTimeoutFn(leaseTimer);
          leaseSignals.cleanup();
        }
      }

      return await runDirect();
    } finally {
      clearTimeoutFn(operationTimer);
    }
  }

  return { run };
}

export function createRefreshEpochGuard(getEpoch) {
  const initialEpoch = getEpoch();

  return {
    initialEpoch,
    isCurrent() {
      return getEpoch() === initialEpoch;
    },
  };
}

export function createAuthStateMessage({ source, epoch, status, timestamp = Date.now() }) {
  return { source, epoch, status, timestamp };
}

export function isAuthStateMessage(value) {
  if (value === null || typeof value !== "object") {
    return false;
  }

  const keys = Object.keys(value);
  if (keys.some((key) => !["source", "epoch", "status", "timestamp"].includes(key))) {
    return false;
  }

  return (
    typeof value.source === "string" &&
    value.source.length > 0 &&
    typeof value.epoch === "number" &&
    Number.isFinite(value.epoch) &&
    (value.status === "authenticated" || value.status === "unauthenticated") &&
    typeof value.timestamp === "number" &&
    Number.isFinite(value.timestamp)
  );
}
