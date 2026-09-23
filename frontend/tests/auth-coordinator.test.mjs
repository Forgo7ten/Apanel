import assert from "node:assert/strict";
import test from "node:test";

import {
  createAuthRefreshCoordinator,
  createAuthStateMessage,
  createRefreshEpochGuard,
  isAuthStateMessage,
} from "../src/lib/auth-coordinator.mjs";

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function abortAwareDelay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new Error("aborted"));
      return;
    }

    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason ?? new Error("aborted"));
      },
      { once: true },
    );
  });
}

function createMemoryStorage() {
  const values = new Map();

  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

test("refresh result is discarded when logout advances the epoch", async () => {
  let epoch = 0;
  const guard = createRefreshEpochGuard(() => epoch);
  let published = false;

  const refresh = (async () => {
    await wait(10);
    if (guard.isCurrent()) {
      published = true;
    }
    return guard.isCurrent();
  })();

  epoch += 1;
  assert.equal(await refresh, false);
  assert.equal(published, false);
});

test("refresh task receives an abort signal when its timeout expires", async () => {
  const coordinator = createAuthRefreshCoordinator({
    requestTimeoutMs: 20,
    lockTimeoutMs: 10,
  });
  let aborted = false;

  await assert.rejects(
    coordinator.run(async (signal) => {
      signal.addEventListener("abort", () => {
        aborted = true;
      });
      await abortAwareDelay(100, signal);
    }),
    { name: "AbortError" },
  );

  assert.equal(aborted, true);
});

test("a transport that ignores abort cannot publish a late refresh result", async () => {
  const coordinator = createAuthRefreshCoordinator({ requestTimeoutMs: 10 });
  let transportResult;
  let coordinatorResult;

  const operation = coordinator.run(async () => {
    await wait(35);
    transportResult = { accessToken: "late-token" };
    return transportResult;
  }).then((result) => {
    coordinatorResult = result;
  });

  await assert.rejects(operation, { name: "AbortError" });
  await wait(45);
  assert.deepEqual(transportResult, { accessToken: "late-token" });
  assert.equal(coordinatorResult, undefined);
  // The coordinator promise already rejected at the deadline, so callers
  // cannot observe or commit the late value.
});

test("Web Lock acquisition has its own bounded abort path", async () => {
  const locks = {
    request() {
      return new Promise(() => {});
    },
  };
  const coordinator = createAuthRefreshCoordinator({
    locks,
    lockTimeoutMs: 20,
    requestTimeoutMs: 100,
  });

  await assert.rejects(coordinator.run(async () => "never"), { name: "AbortError" });
});

test("unavailable storage degrades to one direct refresh attempt", async () => {
  let attempts = 0;
  const storage = {
    getItem() {
      throw new Error("storage disabled");
    },
    setItem() {
      throw new Error("storage disabled");
    },
  };
  const coordinator = createAuthRefreshCoordinator({
    storage,
    requestTimeoutMs: 30,
    lockTimeoutMs: 10,
  });

  assert.equal(
    await coordinator.run(async () => {
      attempts += 1;
      return "direct";
    }),
    "direct",
  );
  assert.equal(attempts, 1);
});

test("storage lease heartbeats and never removes another owner's lease", async () => {
  const storage = createMemoryStorage();
  const coordinator = createAuthRefreshCoordinator({
    storage,
    leaseMs: 40,
    leaseHeartbeatMs: 5,
    leaseWaitMs: 2,
    requestTimeoutMs: 30,
    lockTimeoutMs: 20,
  });
  let firstExpiry = 0;

  await assert.rejects(
    coordinator.run(async (signal) => {
      await wait(12);
      const lease = JSON.parse(storage.getItem("apanel-auth-refresh-lease"));
      firstExpiry = lease.expiresAt;
      storage.setItem(
        "apanel-auth-refresh-lease",
        JSON.stringify({ owner: "another-context", expiresAt: Date.now() + 1000 }),
      );
      await abortAwareDelay(100, signal);
    }),
    { name: "AbortError" },
  );

  assert.ok(firstExpiry > Date.now());
  const remainingLease = JSON.parse(storage.getItem("apanel-auth-refresh-lease"));
  assert.equal(remainingLease.owner, "another-context");
  assert.equal(typeof remainingLease.expiresAt, "number");
});

test("storage heartbeat renews the lease through a long refresh and releases its owner", async () => {
  const storage = createMemoryStorage();
  const coordinator = createAuthRefreshCoordinator({
    storage,
    leaseMs: 30,
    leaseHeartbeatMs: 5,
    leaseWaitMs: 1,
    requestTimeoutMs: 100,
    lockTimeoutMs: 50,
  });
  let initialExpiry = 0;
  let renewedExpiry = 0;

  await coordinator.run(async () => {
    initialExpiry = JSON.parse(storage.getItem("apanel-auth-refresh-lease")).expiresAt;
    await wait(45);
    renewedExpiry = JSON.parse(storage.getItem("apanel-auth-refresh-lease")).expiresAt;
  });

  assert.ok(renewedExpiry > initialExpiry);
  assert.equal(storage.getItem("apanel-auth-refresh-lease"), null);
});

test("broadcast state messages contain no access token or session payload", () => {
  const message = createAuthStateMessage({
    source: "tab-a",
    epoch: 42,
    status: "authenticated",
    timestamp: 123,
  });

  assert.deepEqual(message, {
    source: "tab-a",
    epoch: 42,
    status: "authenticated",
    timestamp: 123,
  });
  assert.equal(JSON.stringify(message).includes("accessToken"), false);
  assert.equal(JSON.stringify(message).includes("AuthSession"), false);
  assert.equal(
    isAuthStateMessage({ ...message, accessToken: "secret-token" }),
    false,
    "messages carrying credentials are not valid coordination messages",
  );
});

test("two contexts serialize refreshes through one shared storage lease", async () => {
  const storage = createMemoryStorage();
  const options = {
    storage,
    leaseMs: 80,
    leaseHeartbeatMs: 10,
    leaseWaitMs: 1,
    requestTimeoutMs: 60,
    lockTimeoutMs: 40,
  };
  const first = createAuthRefreshCoordinator(options);
  const second = createAuthRefreshCoordinator(options);
  let active = 0;
  let maximumActive = 0;

  await Promise.all([
    first.run(async () => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await wait(20);
      active -= 1;
      return "ok";
    }),
    second.run(async () => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await wait(20);
      active -= 1;
      return "ok";
    }),
  ]);

  assert.equal(maximumActive, 1);
  assert.equal(storage.getItem("apanel-auth-refresh-lease"), null);
});
