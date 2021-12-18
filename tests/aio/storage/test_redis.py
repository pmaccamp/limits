import asyncio
import time

import pytest  # type: ignore
import redis
import redis.sentinel
import rediscluster

from limits import RateLimitItemPerSecond, RateLimitItemPerMinute
from limits.storage import storage_from_string
from limits.aio.storage import RedisClusterStorage
from limits.aio.storage import RedisSentinelStorage
from limits.aio.storage import RedisStorage
from limits.aio.strategies import (
    FixedWindowRateLimiter,
    MovingWindowRateLimiter,
)
from limits.errors import ConfigurationError


@pytest.mark.asynchronous
class AsyncSharedRedisTests:
    @pytest.mark.asyncio
    async def test_fixed_window(self):
        limiter = FixedWindowRateLimiter(self.storage)
        per_second = RateLimitItemPerSecond(10)
        start = time.time()
        count = 0

        while time.time() - start < 0.5 and count < 10:
            assert await limiter.hit(per_second)
            count += 1
        assert not await limiter.hit(per_second)

        while time.time() - start <= 1:
            time.sleep(0.1)

        for _ in range(10):
            assert await limiter.hit(per_second)

    @pytest.mark.asyncio
    async def test_reset(self):
        limiter = FixedWindowRateLimiter(self.storage)

        for i in range(0, 10):
            rate = RateLimitItemPerMinute(i)
            await limiter.hit(rate)
        assert await self.storage.reset() == 10

    @pytest.mark.asyncio
    async def test_fixed_window_clear(self):
        limiter = FixedWindowRateLimiter(self.storage)
        per_min = RateLimitItemPerMinute(1)
        await limiter.hit(per_min)
        assert not await limiter.hit(per_min)
        await limiter.clear(per_min)
        assert await limiter.hit(per_min)

    @pytest.mark.asyncio
    async def test_moving_window_clear(self):
        limiter = MovingWindowRateLimiter(self.storage)
        per_min = RateLimitItemPerMinute(1)
        await limiter.hit(per_min)
        assert not await limiter.hit(per_min)
        await limiter.clear(per_min)
        assert await limiter.hit(per_min)

    @pytest.mark.asyncio
    async def test_moving_window_expiry(self):
        limiter = MovingWindowRateLimiter(self.storage)
        limit = RateLimitItemPerSecond(2)
        assert await limiter.hit(limit)
        time.sleep(0.9)
        assert await limiter.hit(limit)
        assert not await limiter.hit(limit)
        time.sleep(0.1)
        assert await limiter.hit(limit)
        last = time.time()

        while time.time() - last <= 1:
            time.sleep(0.05)
        assert await self.storage.storage.keys("%s/*" % limit.namespace) == []

    @pytest.mark.asyncio
    async def test_connectivity(self):
        assert await self.storage.check() is True


@pytest.mark.asynchronous
class TestAsyncRedisStorage(AsyncSharedRedisTests):
    def setup_method(self):
        self.real_storage_url = "redis://localhost:7379"
        self.storage_url = f"async+{self.real_storage_url}"
        self.storage = RedisStorage(self.storage_url)
        redis.from_url(self.real_storage_url).flushall()

    @pytest.mark.asyncio
    async def test_init_options(self, mocker):
        lib = mocker.Mock()
        ping_response = asyncio.Future()
        ping_response.set_result({})
        lib.StrictRedis.from_url.return_value.ping.return_value = ping_response
        mocker.patch("limits.aio.storage.base.get_dependency", return_value=lib)
        assert await storage_from_string(self.storage_url, connection_timeout=1).check()
        assert lib.StrictRedis.from_url.call_args[1]["connection_timeout"] == 1


@pytest.mark.asynchronous
class TestAsyncRedisUnixSocketStorage(AsyncSharedRedisTests):
    def setup_method(self):
        self.storage_url = "async+redis+unix:///tmp/limits.redis.sock"
        self.storage = RedisStorage(self.storage_url)
        redis.from_url("unix:///tmp/limits.redis.sock").flushall()

    @pytest.mark.asyncio
    async def test_init_options(self, mocker):
        lib = mocker.Mock()
        ping_response = asyncio.Future()
        ping_response.set_result({})
        lib.StrictRedis.from_url.return_value.ping.return_value = ping_response
        mocker.patch("limits.aio.storage.base.get_dependency", return_value=lib)
        assert await storage_from_string(self.storage_url, connection_timeout=1).check()
        assert lib.StrictRedis.from_url.call_args[1]["connection_timeout"] == 1


@pytest.mark.asynchronous
class TestAsyncRedisClusterStorage(AsyncSharedRedisTests):
    def setup_method(self):
        rediscluster.RedisCluster("localhost", 7000).flushall()
        self.storage_url = "redis+cluster://localhost:7000"
        self.storage = RedisClusterStorage(f"async+{self.storage_url}")

    @pytest.mark.asyncio
    async def test_init_options(self, mocker):
        lib = mocker.Mock()
        ping_response = asyncio.Future()
        ping_response.set_result({})
        lib.StrictRedisCluster.return_value.ping.return_value = ping_response
        mocker.patch("limits.aio.storage.base.get_dependency", return_value=lib)
        assert await storage_from_string(
            f"async+{self.storage_url}", max_connections=1
        ).check()
        assert lib.StrictRedisCluster.call_args[1]["max_connections"] == 1


class TestAsyncRedisSentinelStorage(AsyncSharedRedisTests):
    def setup_method(self):
        self.storage_url = "redis+sentinel://localhost:26379"
        self.service_name = "localhost-redis-sentinel"
        self.storage = RedisSentinelStorage(
            f"async+{self.storage_url}", service_name=self.service_name
        )
        redis.sentinel.Sentinel([("localhost", 26379)]).master_for(
            self.service_name
        ).flushall()

    @pytest.mark.asyncio
    async def test_init_no_service_name(self, mocker):
        lib = mocker.Mock()
        mocker.patch("limits.aio.storage.base.get_dependency", return_value=lib)
        with pytest.raises(ConfigurationError):
            await storage_from_string(f"async+{self.storage_url}", connection_timeout=1)

    @pytest.mark.asyncio
    async def test_init_options(self, mocker):
        lib = mocker.Mock()
        ping_response = asyncio.Future()
        ping_response.set_result({})
        lib.Sentinel.return_value.slave_for.return_value.ping.return_value = (
            ping_response
        )
        mocker.patch("limits.aio.storage.base.get_dependency", return_value=lib)
        assert await storage_from_string(
            f"async+{self.storage_url}/{self.service_name}", connection_timeout=1
        ).check()
        assert lib.Sentinel.call_args[1]["connection_timeout"] == 1

    @pytest.mark.parametrize(
        "username, password, opts",
        [
            ("", "", {}),
            ("username", "", {"username": "username"}),
            ("", "sekret", {"password": "sekret"}),
        ],
    )
    @pytest.mark.asyncio
    async def test_auth(self, mocker, username, password, opts):
        lib = mocker.Mock()
        mocker.patch("limits.aio.storage.base.get_dependency", return_value=lib)
        storage_url = (
            f"async+redis+sentinel://{username}:{password}@localhost:26379/service_name"
        )
        storage_from_string(storage_url)
        assert lib.Sentinel.call_args[1]["sentinel_kwargs"] == opts

    @pytest.mark.parametrize(
        "username, password, success",
        [
            ("", "", False),
            ("username", "", False),
            ("", "sekret", True),
        ],
    )
    @pytest.mark.asyncio
    async def test_auth_connect(self, username, password, success):
        storage_url = (
            f"async+redis+sentinel://{username}:{password}@localhost:36379/"
            "localhost-redis-sentinel"
        )
        assert success == await storage_from_string(storage_url).check()
