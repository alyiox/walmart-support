from __future__ import annotations

import httpx
import pytest

from mcp_walmart_support.aura import SessionExpired
from mcp_walmart_support.auth import build_client, check_auth, login, read_auth_state
from mcp_walmart_support.config import Config


def _cfg(**kw: object) -> Config:
    base = {
        "base_url": "https://portal.test",
        "username": "",
        "password": "",
        "cookie": "",
        "timeout": 10,
    }
    return Config(**(base | kw))  # type: ignore[arg-type]


def test_reads_authenticated_attribute(member_page: str, guest_page: str) -> None:
    assert read_auth_state(member_page).authenticated is True
    assert read_auth_state(guest_page).authenticated is False
    assert read_auth_state(member_page).language == "en_US"


def test_falls_back_when_aura_config_unparseable() -> None:
    # A markup change should degrade to a guess, not raise.
    assert read_auth_state('junk "authenticated":"true" junk').authenticated is True
    assert read_auth_state("nothing here").authenticated is False


def test_cookie_is_sent_as_sid() -> None:
    client = build_client(_cfg(cookie="sekret"))
    try:
        assert client.cookies.get("sid") == "sekret"
    finally:
        client.close()


def test_check_auth_uses_bootstrap_page(member_page: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/s/contact"
        return httpx.Response(200, text=member_page)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    assert check_auth(client).authenticated is True


def test_login_without_credentials_raises_session_expired(guest_page: str) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=guest_page)),
        base_url="https://portal.test",
    )
    with pytest.raises(SessionExpired, match="no username/password"):
        login(client, _cfg())
