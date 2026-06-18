"""Unit tests for the rate limiter's client-IP resolution and eviction."""

import asyncio
import time
from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import Response

from api.main import RateLimitMiddleware


def _mw(**kwargs):
    return RateLimitMiddleware(app=lambda *a, **k: None, **kwargs)


# Fixture client IPs — hardcoded test-only values, never routed in production.
_PEER_IP = "9.9.9.9"  # NOSONAR — test fixture IP
_IP_A = "1.1.1.1"  # NOSONAR — test fixture IP
_IP_B = "2.2.2.2"  # NOSONAR — test fixture IP
_DEFAULT_PEER_IP = "203.0.113.9"  # NOSONAR — RFC 5737 TEST-NET-3


def _req(xff=None, host=_DEFAULT_PEER_IP):
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


async def _ok(_request: Request) -> Response:  # NOSONAR — must be a coroutine fn; dispatch awaits call_next
    """A no-op ``call_next`` handler for the dispatch tests."""
    return Response("ok")


class TestClientIp:
    def test_direct_uses_peer_ip_and_ignores_xff(self):
        mw = _mw(trusted_proxies=0)
        assert mw._client_ip(_req(xff=_IP_A, host=_PEER_IP)) == _PEER_IP  # type: ignore[arg-type]

    def test_one_proxy_takes_rightmost_entry(self):
        mw = _mw(trusted_proxies=1)
        assert mw._client_ip(_req(xff="real-client", host="proxy")) == "real-client"  # type: ignore[arg-type]

    def test_one_proxy_is_spoof_resistant(self):
        # Client prepends a fake entry; the trusted proxy appends the real IP.
        mw = _mw(trusted_proxies=1)
        assert (
            mw._client_ip(_req(xff="6.6.6.6, real-client", host="proxy"))  # type: ignore[arg-type]  # NOSONAR — spoofed XFF test entry
            == "real-client"
        )

    def test_two_proxies_take_second_from_right(self):
        mw = _mw(trusted_proxies=2)
        ip = mw._client_ip(_req(xff="spoof, real-client, proxyA", host="proxyB"))  # type: ignore[arg-type]
        assert ip == "real-client"

    def test_falls_back_to_peer_when_no_xff(self):
        mw = _mw(trusted_proxies=1)
        assert mw._client_ip(_req(xff=None, host="proxy")) == "proxy"  # type: ignore[arg-type]

    def test_short_chain_is_clamped(self):
        mw = _mw(trusted_proxies=2)
        assert mw._client_ip(_req(xff="only-one", host="proxy")) == "only-one"  # type: ignore[arg-type]


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

        codes = [
            asyncio.run(mw.dispatch(_req(host=_PEER_IP), _ok)).status_code  # type: ignore[arg-type]
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]

    def test_separate_ips_have_separate_buckets(self):
        mw = _mw(calls=1, period=60)

        assert asyncio.run(mw.dispatch(_req(host=_IP_A), _ok)).status_code == 200  # type: ignore[arg-type]
        assert asyncio.run(mw.dispatch(_req(host=_IP_B), _ok)).status_code == 200  # type: ignore[arg-type]
        assert asyncio.run(mw.dispatch(_req(host=_IP_A), _ok)).status_code == 429  # type: ignore[arg-type]
