"""Unit tests for the on-demand web-search endpoint (api/v1/search.py).

The handler is thin — gate on the feature flag/vertical, dispatch, map the
dispatch status onto an HTTP status — so we call it directly with the dispatcher
and config stubbed (no DB/scraper). End-to-end routing/auth is covered by the
integration suite.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import search as search_mod
from api.websearch import WebSearchResult
from common.model import WebResult, WebResultKind


def _call(q="us iran", vertical="web"):
    return asyncio.run(search_mod.web_search(q=q, vertical=vertical))


def _enable(monkeypatch, enabled=True):
    monkeypatch.setattr(
        search_mod, "scraper_config", SimpleNamespace(web_search_enabled=enabled)
    )


def _stub_dispatch(monkeypatch, result: WebSearchResult):
    async def fake_dispatch(query):
        return result

    monkeypatch.setattr(search_mod, "dispatch_web_search", fake_dispatch)


def _result(status, **kw):
    return WebSearchResult(query="us iran", status=status, **kw)


def test_ok_returns_results(monkeypatch):
    _enable(monkeypatch)
    hit = WebResult.create(
        kind=WebResultKind.ORGANIC, title="Iran–US relations", url="https://x.com/a"
    )
    _stub_dispatch(
        monkeypatch, _result("ok", engine="google", results=[hit], attempts=["google"])
    )
    resp = _call()
    assert resp.status == "ok"
    assert resp.engine == "google"
    assert resp.attempts == ["google"]
    assert [r.title for r in resp.results] == ["Iran–US relations"]


def test_empty_is_200_with_no_results(monkeypatch):
    _enable(monkeypatch)
    _stub_dispatch(monkeypatch, _result("empty", attempts=["google", "bing"]))
    resp = _call()
    assert resp.status == "empty"
    assert resp.results == []


@pytest.mark.parametrize(
    "status,code",
    [
        ("unavailable", 503),
        ("timeout", 504),
        ("blocked", 502),
        ("error", 502),
        ("cooling", 502),
    ],
)
def test_failure_statuses_map_to_http_codes(monkeypatch, status, code):
    _enable(monkeypatch)
    _stub_dispatch(monkeypatch, _result(status, attempts=["google", "bing"]))
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == code
    assert exc.value.detail["status"] == status
    assert exc.value.detail["attempts"] == ["google", "bing"]


def test_disabled_feature_returns_503(monkeypatch):
    _enable(monkeypatch, enabled=False)

    # dispatch must not even be called when the feature is off.
    def boom(query):  # pragma: no cover - must not run
        raise AssertionError("dispatch should not be called when disabled")

    monkeypatch.setattr(search_mod, "dispatch_web_search", boom)
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 503
    assert "disabled" in exc.value.detail


def test_unsupported_vertical_returns_400(monkeypatch):
    _enable(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _call(vertical="news")
    assert exc.value.status_code == 400
