"""Unit tests for the rate limiter's client-IP resolution and eviction."""

import asyncio
import time
from types import SimpleNamespace

from starlette.responses import Response

from api.main import RateLimitMiddleware


def _mw(**kwargs):
    return RateLimitMiddleware(app=lambda *a, **k: None, **kwargs)


def _req(xff=None, host="203.0.113.9"):
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


class TestClientIp:
    def test_direct_uses_peer_ip_and_ignores_xff(self):
        mw = _mw(trusted_proxies=0)
        assert mw._client_ip(_req(xff="1.1.1.1", host="9.9.9.9")) == "9.9.9.9"

    def test_one_proxy_takes_rightmost_entry(self):
        mw = _mw(trusted_proxies=1)
        assert mw._client_ip(_req(xff="real-client", host="proxy")) == "real-client"

    def test_one_proxy_is_spoof_resistant(self):
        # Client prepends a fake entry; the trusted proxy appends the real IP.
        mw = _mw(trusted_proxies=1)
        assert (
            mw._client_ip(_req(xff="6.6.6.6, real-client", host="proxy"))
            == "real-client"
        )

    def test_two_proxies_take_second_from_right(self):
        mw = _mw(trusted_proxies=2)
        ip = mw._client_ip(_req(xff="spoof, real-client, proxyA", host="proxyB"))
        assert ip == "real-client"

    def test_falls_back_to_peer_when_no_xff(self):
        mw = _mw(trusted_proxies=1)
        assert mw._client_ip(_req(xff=None, host="proxy")) == "proxy"

    def test_short_chain_is_clamped(self):
        mw = _mw(trusted_proxies=2)
        assert mw._client_ip(_req(xff="only-one", host="proxy")) == "only-one"


class TestEviction:
    def test_evicts_only_stale_ips(self):
        mw = _mw()
        now = time.time()
        mw._requests = {
            "stale": [now - 120.0],  # newest hit outside window
            "active": [now],
        }
        mw._evict_stale(window_start=now - 60)
        assert "stale" not in mw._requests
        assert "active" in mw._requests


class TestDispatch:
    def test_blocks_after_limit_per_ip(self):
        mw = _mw(calls=2, period=60)

        async def ok(_request):
            return Response("ok")

        codes = [
            asyncio.run(mw.dispatch(_req(host="9.9.9.9"), ok)).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]

    def test_separate_ips_have_separate_buckets(self):
        mw = _mw(calls=1, period=60)

        async def ok(_request):
            return Response("ok")

        assert asyncio.run(mw.dispatch(_req(host="1.1.1.1"), ok)).status_code == 200
        assert asyncio.run(mw.dispatch(_req(host="2.2.2.2"), ok)).status_code == 200
        assert asyncio.run(mw.dispatch(_req(host="1.1.1.1"), ok)).status_code == 429
