"""Small Redis lease used to prevent overlapping scheduled jobs."""

from __future__ import annotations

import secrets
from typing import Any

RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class TaskLockError(RuntimeError):
    """Redis could not acquire or release a scheduled-task lease."""


class RedisTaskLock:
    """An ownership-safe, non-blocking Redis lock.

    The random token is never logged.  Release uses a compare-and-delete Lua
    script so a late task cannot delete a newer owner's lock after its TTL has
    expired.
    """

    def __init__(self, client: Any, key: str, *, ttl_seconds: int) -> None:
        if not key or not key.strip():
            raise ValueError("lock key is required")
        if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("lock ttl must be positive")
        self.client = client
        self.key = key
        self.ttl_seconds = int(ttl_seconds)
        self._token = secrets.token_urlsafe(32)
        self._acquired = False

    async def acquire(self) -> bool:
        """Attempt once and return false when another job owns the lock."""

        try:
            acquired = await self.client.set(
                self.key,
                self._token,
                nx=True,
                ex=self.ttl_seconds,
            )
        except Exception as exc:  # pragma: no cover - concrete Redis errors vary
            raise TaskLockError("task lock acquire failed") from exc
        self._acquired = bool(acquired)
        return self._acquired

    async def release(self) -> None:
        """Release only when this lease still owns the key."""

        if not self._acquired:
            return
        try:
            await self.client.eval(RELEASE_LOCK_SCRIPT, 1, self.key, self._token)
        except (AttributeError, NotImplementedError):
            # Minimal fakes and Redis-compatible clients without EVAL can still
            # be used in tests.  Never use this fallback for a real Redis error.
            try:
                owner = await self.client.get(self.key)
                if owner == self._token:
                    await self.client.delete(self.key)
            except Exception as exc:  # pragma: no cover - fake-specific path
                raise TaskLockError("task lock release failed") from exc
        except Exception as exc:  # pragma: no cover - concrete Redis errors vary
            raise TaskLockError("task lock release failed") from exc
        finally:
            self._acquired = False

    async def __aenter__(self) -> RedisTaskLock:
        if not await self.acquire():
            raise TaskLockError("task lock is already held")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


__all__ = ["RELEASE_LOCK_SCRIPT", "RedisTaskLock", "TaskLockError"]
