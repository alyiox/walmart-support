from __future__ import annotations

import json
import urllib.parse

import httpx
import pytest

from mcp_walmart_support.aura import (
    AuraContext,
    AuraError,
    AuraSession,
    SessionExpired,
    extract_token,
)

from .conftest import make_page


def test_context_skips_segments_without_fwuid(guest_page: str) -> None:
    ctx = AuraContext.from_html(guest_page)
    assert ctx.fwuid == "TEST_FWUID_14.192.8388608"


def test_context_payload_adds_client_populated_keys(guest_page: str) -> None:
    payload = AuraContext.from_html(guest_page).payload()
    assert payload["fwuid"] == "TEST_FWUID_14.192.8388608"
    assert payload["dn"] == [] and payload["globals"] == {} and payload["uad"] is False
    # asset-loading keys are not part of action dispatch
    for key in ("styleContext", "dfs", "apck", "lrmc"):
        assert key not in payload


def test_context_missing_raises_with_hint() -> None:
    with pytest.raises(AuraError, match="no Aura context"):
        AuraContext.from_html("<html></html>")


def _session(handler: object, page: str) -> AuraSession:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.Client(transport=transport, base_url="https://portal.test")
    return AuraSession.bootstrap(client, "/s/contact")


def test_apex_returns_value(member_page: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/s/contact":
            return httpx.Response(200, text=member_page)
        fields = urllib.parse.parse_qs(request.content.decode())
        action = json.loads(fields["message"][0])["actions"][0]
        # the Apex class carries a Controller suffix the URL hint drops
        assert (
            action["descriptor"] == "apex://AC_ActivityController/ACTION$getCasesForCommunityUser"
        )
        assert action["callingDescriptor"] == "markup://c:AC_Activity"
        assert "other.AC_Activity.getCasesForCommunityUser" in str(request.url)
        return httpx.Response(
            200,
            text=json.dumps(
                {"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": {"ok": 1}}]}
            ),
        )

    assert _session(handler, member_page).apex("AC_Activity", "getCasesForCommunityUser") == {
        "ok": 1
    }


def test_guarded_error_envelope_is_unwrapped(member_page: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/s/contact":
            return httpx.Response(200, text=member_page)
        return httpx.Response(200, text='*/{"actions":[]}/*ERROR*/')

    with pytest.raises(AuraError, match="dropped by Aura"):
        _session(handler, member_page).apex("AC_Activity", "nope")


def test_action_error_surfaces_apex_message(member_page: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/s/contact":
            return httpx.Response(200, text=member_page)
        return httpx.Response(
            200,
            text=json.dumps(
                {"actions": [{"id": "1;a", "state": "ERROR", "error": [{"message": "boom"}]}]}
            ),
        )

    with pytest.raises(AuraError, match="boom"):
        _session(handler, member_page).apex("AC_Activity", "getCasesForCommunityUser")


def test_invalid_session_is_distinguishable(member_page: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/s/contact":
            return httpx.Response(200, text=member_page)
        return httpx.Response(
            200,
            text=json.dumps({"event": {"descriptor": "markup://aura:invalidSession"}}),
        )

    with pytest.raises(SessionExpired):
        _session(handler, member_page).apex("AC_Activity", "getCasesForCommunityUser")


def test_non_json_response_is_reported(member_page: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/s/contact":
            return httpx.Response(200, text=make_page(authenticated=True))
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(AuraError, match="non-JSON"):
        _session(handler, member_page).apex("AC_Activity", "getCasesForCommunityUser")


def test_context_payload_is_the_minimal_slice(guest_page: str) -> None:
    payload = AuraContext.from_html(guest_page).payload()
    # The browser echoes only these keys back when dispatching an action.
    assert set(payload) == {"mode", "fwuid", "app", "loaded", "dn", "globals", "uad"}


def test_token_read_from_the_cookie_the_page_names() -> None:
    html = '<script>auraConfig = {"eikoocnekot":"__Host-ERIC_PROD-123"};</script>'
    cookies = httpx.Cookies()
    cookies.set("__Host-ERIC_PROD-123", "eyJub25jZSI6dG9rZW4")
    assert extract_token(html, cookies) == "eyJub25jZSI6dG9rZW4"


def test_token_absent_for_guest_pages(guest_page: str) -> None:
    # Unauthenticated pages run in csrfV2 mode and name no token cookie.
    assert extract_token(guest_page, httpx.Cookies()) == ""
