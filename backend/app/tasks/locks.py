"""Small Redis lease used to prevent overlapping scheduled jobs."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import suppress
from typing import Any

RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

RENEW_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
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
        self._heartbeat_task: asyncio.Task[None] | None = None

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
        if self._acquired:
            self._heartbeat_task = asyncio.create_task(self._heartbeat())
        return self._acquired

    async def renew(self) -> bool:
        """Extend the TTL only while this instance still owns the lease."""

        if not self._acquired:
            return False
        try:
            renewed = await self.client.eval(
                RENEW_LOCK_SCRIPT,
                1,
                self.key,
                self._token,
                self.ttl_seconds,
            )
        except (AttributeError, NotImplementedError):
            owner = await self.client.get(self.key)
            if owner != self._token:
                return False
            renewed = await self.client.expire(self.key, self.ttl_seconds)
        except Exception as exc:  # pragma: no cover - concrete Redis errors vary
            raise TaskLockError("task lock renew failed") from exc
        return bool(renewed)

    async def _heartbeat(self) -> None:
        interval = max(0.1, self.ttl_seconds / 3)
        try:
            while self._acquired:
                await asyncio.sleep(interval)
                try:
                    renewed = await self.renew()
                except TaskLockError:
                    continue
                if not renewed:
                    self._acquired = False
                    return
        except asyncio.CancelledError:
            raise

    async def release(self) -> None:
        """Release only when this lease still owns the key."""

        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
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


__all__ = ["RELEASE_LOCK_SCRIPT", "RENEW_LOCK_SCRIPT", "RedisTaskLock", "TaskLockError"]
