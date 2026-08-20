"""Portal authentication.

Two ways in, because neither is reliable alone: scripted login through the
community login form (headless, but breaks if MFA or a captcha appears), and a
supplied session cookie (works when login is blocked, but Salesforce ``sid``
cookies are session-scoped, so they die with the browser and expire on their
own). A cookie is used when present and login is the fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from .aura import AuraSession, SessionExpired
from .config import Config

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

# The bootstrap page states plainly whether the visitor is authenticated, which
# is a cheaper and more stable check than calling an Apex action.
_AURA_CONFIG = re.compile(r"auraConfig\s*=\s*\{")

BOOTSTRAP_PAGE = "/s/contact?language=en_US"

# Authentication does not go through Aura at all: the portal still serves the
# classic Salesforce login form, which POSTs to /login and answers with a
# redirect to /secur/frontdoor.jsp?sid=... that mints the session cookies.
LOGIN_PAGE = "/login"
_LOGIN_FORM = re.compile(r'<form name="login".*?</form>', re.DOTALL)
_INPUT = re.compile(r"<input[^>]*>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class AuthState:
    authenticated: bool
    language: str | None = None
    page_id: str | None = None


def build_client(cfg: Config) -> httpx.Client:
    cookies = {}
    if cfg.has_cookie:
        cookies["sid"] = cfg.cookie
    return httpx.Client(
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        cookies=cookies,
    )


def _extract_json_object(html: str, start: int) -> dict[str, object] | None:
    """Read one balanced ``{...}`` beginning at ``start``.

    Regex cannot match the nested ``auraConfig`` literal, and it is far too
    large to hand to a JS parser, so scan for the matching brace.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def read_auth_state(html: str) -> AuthState:
    match = _AURA_CONFIG.search(html)
    if match:
        config = _extract_json_object(html, match.end() - 1)
        attributes = (config or {}).get("attributes")
        if isinstance(attributes, dict):
            return AuthState(
                authenticated=str(attributes.get("authenticated", "")).lower() == "true",
                language=attributes.get("language"),
                page_id=attributes.get("pageId"),
            )
    # Fall back to the raw attribute so a markup change degrades to a guess
    # rather than a crash.
    return AuthState(authenticated='"authenticated":"true"' in html)


def check_auth(client: httpx.Client, page: str = BOOTSTRAP_PAGE) -> AuthState:
    response = client.get(page)
    response.raise_for_status()
    return read_auth_state(response.text)


def _login_form_fields(html: str) -> dict[str, str]:
    """Collect the login form's own hidden fields.

    Copying whatever the form ships (``lt``, ``pqs``, ``display`` …) rather than
    hardcoding a list means a new hidden field is carried along automatically.
    """
    form = _LOGIN_FORM.search(html)
    if not form:
        raise SessionExpired("login", "could not find the login form on the login page")
    fields: dict[str, str] = {}
    for tag in _INPUT.findall(form.group(0)):
        attrs = dict(_ATTR.findall(tag))
        name = attrs.get("name")
        if name and attrs.get("type") != "submit":
            fields[name] = attrs.get("value", "")
    return fields


def login(client: httpx.Client, cfg: Config, page: str = BOOTSTRAP_PAGE) -> AuthState:
    """Authenticate with username and password through the classic login form."""
    if not cfg.has_credentials:
        raise SessionExpired(
            "login",
            "no username/password configured, so the session cannot be renewed",
        )
    form_page = client.get(LOGIN_PAGE, params={"startURL": page})
    form_page.raise_for_status()

    fields = _login_form_fields(form_page.text)
    # ``un`` is what the form's own onsubmit handler copies the username into.
    fields |= {
        "username": cfg.username,
        "un": cfg.username,
        "pw": cfg.password,
        "startURL": page,
        "Login": "Log In",
    }
    # The response redirects through frontdoor.jsp, which sets the session
    # cookies; httpx follows it because the client allows redirects.
    client.post(LOGIN_PAGE, data=fields).raise_for_status()
    return check_auth(client, page)


def authenticated_session(
    cfg: Config, page: str = BOOTSTRAP_PAGE
) -> tuple[httpx.Client, AuraSession]:
    """Return a client and Aura session that are known to be logged in.

    Tries the configured cookie first and logs in when it is absent or stale,
    which is the common case: ``sid`` cookies do not survive a browser restart.
    """
    client = build_client(cfg)
    state = check_auth(client, page)
    if not state.authenticated:
        state = login(client, cfg, page)
        if not state.authenticated:
            client.close()
            raise SessionExpired("login", "portal still reports an unauthenticated session")
    return client, AuraSession.bootstrap(client, page)
