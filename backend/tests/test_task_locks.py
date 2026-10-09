from __future__ import annotations

import asyncio

import pytest

from app.tasks.locks import RENEW_LOCK_SCRIPT, RedisTaskLock, TaskLockError


class FakeRedis:
    def __init__(self) -> None:
        self.value = None
        self.ttl = None
        self.renewals = 0
        self.fail_renewal = False

    async def set(self, _key, value, *, nx, ex):
        assert nx is True
        if self.value is not None:
            return False
        self.value = value
        self.ttl = ex
        return True

    async def eval(self, script, _num_keys, _key, token, *args):
        if self.value != token:
            return 0
        if script == RENEW_LOCK_SCRIPT:
            if self.fail_renewal:
                raise ConnectionError("redis unavailable")
            self.ttl = int(args[0])
            self.renewals += 1
            return 1
        self.value = None
        return 1


@pytest.mark.asyncio
async def test_lock_renews_long_running_lease_and_stops_heartbeat_on_release() -> None:
    redis = FakeRedis()
    lock = RedisTaskLock(redis, "task", ttl_seconds=1)

    assert await lock.acquire() is True
    await asyncio.sleep(0.4)
    assert redis.renewals >= 1

    await lock.release()
    renewals = redis.renewals
    await asyncio.sleep(0.4)

    assert redis.value is None
    assert redis.renewals == renewals


@pytest.mark.asyncio
async def test_lock_renewal_never_extends_another_owner() -> None:
    redis = FakeRedis()
    lock = RedisTaskLock(redis, "task", ttl_seconds=30)

    assert await lock.acquire() is True
    redis.value = "new-owner"

    assert await lock.renew() is False
    await lock.release()
    assert redis.value == "new-owner"


@pytest.mark.asyncio
async def test_lock_marks_lease_lost_when_heartbeat_cannot_renew() -> None:
    redis = FakeRedis()
    lock = RedisTaskLock(redis, "task", ttl_seconds=1)

    assert await lock.acquire() is True
    redis.fail_renewal = True
    await asyncio.wait_for(lock.wait_lost(), timeout=1.0)

    with pytest.raises(TaskLockError, match="lease was lost"):
        lock.assert_owned()
    await lock.release()
