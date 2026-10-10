/** Account lifetime, separate from access-token refresh generations. No credentials here. */
export class SessionChangedError extends Error {
  constructor() {
    super("登录会话已变化，请重新操作。");
    this.name = "SessionChangedError";
  }
}

export function createSessionScope() {
  let controller = new AbortController();
  let snapshot = Object.freeze({ version: 0, userId: null, signal: controller.signal });
  const listeners = new Set();
  return {
    capture: () => snapshot,
    version: () => snapshot.version,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    transition(userId, force = false) {
      const owner = userId === null || userId === undefined ? null : String(userId);
      if (!force && snapshot.userId === owner) return snapshot;
      const previous = controller;
      controller = new AbortController();
      snapshot = Object.freeze({ version: snapshot.version + 1, userId: owner, signal: controller.signal });
      previous.abort(new SessionChangedError());
      for (const listener of listeners) listener();
      return snapshot;
    },
    assertCurrent(captured) {
      if (snapshot !== captured || captured.signal.aborted) throw new SessionChangedError();
    },
  };
}

export const sessionScope = createSessionScope();
