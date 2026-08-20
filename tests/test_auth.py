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


def test_cached_cookies_are_sent() -> None:
    client = build_client(_cfg(), {"sid": "from-cache"})
    try:
        assert client.cookies.get("sid") == "from-cache"
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


def test_login_form_fields_are_copied_from_the_page() -> None:
    from mcp_walmart_support.auth import _login_form_fields

    html = (
        '<form name="login" method="post" action="/login">'
        '<input type="hidden" name="lt" value="standard"/>'
        '<input type="hidden" name="pqs" value="?startURL=%2Fs%2Factivity"/>'
        '<input type="email" name="username" value=""/>'
        '<input type="password" name="pw" value=""/>'
        '<input type="submit" name="Login" value="Log In"/>'
        "</form>"
    )
    fields = _login_form_fields(html)
    # hidden state is carried along; the submit button is not a field
    assert fields["lt"] == "standard"
    assert fields["pqs"] == "?startURL=%2Fs%2Factivity"
    assert "Login" not in fields


def test_login_form_missing_raises() -> None:
    from mcp_walmart_support.auth import _login_form_fields

    with pytest.raises(SessionExpired, match="could not find the login form"):
        _login_form_fields("<html>no form here</html>")
