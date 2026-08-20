from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from mcp_walmart_support import auth
from mcp_walmart_support.aura import AuraSession, SessionExpired
from mcp_walmart_support.config import Config

from .conftest import make_login_form, make_page


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "session.json"
    monkeypatch.setattr(auth, "SESSION_PATH", path)
    return path


def _cfg(**kw: object) -> Config:
    base = {
        "base_url": "https://portal.test",
        "username": "u@example.com",
        "password": "pw",
        "cookie": "",
        "timeout": 10,
    }
    return Config(**(base | kw))  # type: ignore[arg-type]


def test_session_round_trip_is_private(isolated_cache: Path) -> None:
    client = httpx.Client(base_url="https://portal.test")
    client.cookies.set("sid", "abc", domain="portal.test")
    auth.save_session(client)
    assert auth.load_session() == {"sid": "abc"}
    # live session cookies must not be world-readable
    assert stat.S_IMODE(isolated_cache.stat().st_mode) == 0o600


def test_load_session_tolerates_missing_and_corrupt_files(isolated_cache: Path) -> None:
    assert auth.load_session() == {}
    isolated_cache.write_text("not json")
    assert auth.load_session() == {}
    isolated_cache.write_text(json.dumps({"cookies": "wrong type"}))
    assert auth.load_session() == {}


def test_clear_session_reports_whether_it_removed_anything(isolated_cache: Path) -> None:
    isolated_cache.write_text(json.dumps({"cookies": {"sid": "x"}}))
    assert auth.clear_session() is True
    assert auth.clear_session() is False


def _transport(calls: list[str], *, cookie_is_valid: bool) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/login":
            # GET serves the form; POST completes the login
            if request.method == "GET":
                return httpx.Response(200, text=make_login_form())
            return httpx.Response(200, text=make_page(authenticated=True))
        authed = cookie_is_valid or "POST /login" in calls
        return httpx.Response(200, text=make_page(authenticated=authed))

    return httpx.MockTransport(handler)


def test_cached_session_skips_login(isolated_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolated_cache.write_text(json.dumps({"cookies": {"sid": "still-good"}}))
    calls: list[str] = []
    monkeypatch.setattr(
        auth,
        "build_client",
        lambda cfg, cookies=None: httpx.Client(
            transport=_transport(calls, cookie_is_valid=True), base_url="https://portal.test"
        ),
    )
    _, _, source = auth.open_session(_cfg(), "/s/activity")
    assert source == "cache"
    assert not any(c.startswith("POST /login") for c in calls)


def test_stale_cache_falls_back_to_login(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_cache.write_text(json.dumps({"cookies": {"sid": "expired"}}))
    calls: list[str] = []
    monkeypatch.setattr(
        auth,
        "build_client",
        lambda cfg, cookies=None: httpx.Client(
            transport=_transport(calls, cookie_is_valid=False), base_url="https://portal.test"
        ),
    )
    _, _, source = auth.open_session(_cfg(), "/s/activity")
    assert source == "login"
    assert "POST /login" in calls
    # a fresh session replaces the stale one
    assert auth.load_session() != {"sid": "expired"}


def test_with_session_retries_once_when_the_cache_has_died(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_cache.write_text(json.dumps({"cookies": {"sid": "expired"}}))
    sources: list[str] = []
    monkeypatch.setattr(
        auth,
        "open_session",
        lambda cfg, page, use_cache=True: (
            httpx.Client(base_url="https://portal.test"),
            object(),
            sources.append("cache" if use_cache else "login")
            or ("cache" if use_cache else "login"),
        ),
    )
    attempts: list[int] = []

    def action(_session: AuraSession) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise SessionExpired("cases", "expired")
        return "second attempt worked"

    assert auth.with_session(_cfg(), "/s/activity", action) == "second attempt worked"
    assert sources == ["cache", "login"]
    assert not isolated_cache.exists()  # the dead session was discarded


def test_with_session_does_not_retry_a_fresh_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "open_session",
        lambda cfg, page, use_cache=True: (
            httpx.Client(base_url="https://portal.test"),
            object(),
            "login",
        ),
    )

    def action(_session: AuraSession) -> str:
        raise SessionExpired("cases", "expired")

    # Retrying a login that just succeeded would loop; surface the error.
    with pytest.raises(SessionExpired):
        auth.with_session(_cfg(), "/s/activity", action)
