"""Portal authentication and session reuse.

Authentication does not go through Aura: the portal still serves the classic
Salesforce login form, which POSTs to ``/login`` and answers with a redirect
through ``frontdoor.jsp`` that mints the session cookies.

Because each CLI invocation is its own process, those cookies are cached on
disk and reused until the portal rejects them; otherwise every command would
pay a full login.
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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

# Each CLI invocation is its own process, so without a cache every command pays
# a full login — four requests and a Salesforce login event before any real
# work. Session cookies are cached here and reused until the portal rejects
# them. The files hold live session cookies, so they are written 0600.
SESSION_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "walmart-support"


def session_path(base_url: str) -> Path:
    """Where one portal's cached session lives.

    Keyed by host because the portals are separate Salesforce orgs: replaying
    Walmart's ``sid`` against Sam's Club authenticates nothing while still
    looking like a usable cached session, so one shared file would make every
    other command pay a failed round trip before logging in again.
    """
    return SESSION_DIR / f"{urlparse(base_url).hostname or 'portal'}.json"


BOOTSTRAP_PAGE = "/s/contact?language=en_US"

# Authentication does not go through Aura at all: the portal still serves the
# classic Salesforce login form, which POSTs to /login and answers with a
# redirect to /secur/frontdoor.jsp?sid=... that mints the session cookies.
LOGIN_PAGE = "/login"
_LOGIN_FORM = re.compile(r'<form name="login".*?</form>', re.DOTALL)
_INPUT = re.compile(r"<input[^>]*>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')

# A rejected login is answered with the form again rather than a redirect, and
# the reason sits in a ``loginError`` div. The page ships more than one — the
# username chooser has its own, empty and hidden — so every match is read and
# the first one carrying text wins.
_LOGIN_ERROR = re.compile(r'<div[^>]*class="[^"]*loginError[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class AuthState:
    authenticated: bool
    # What the portal said when it refused the login, so a caller can report
    # the reason instead of a bare "not authenticated".
    error: str | None = None


def load_session(path: Path) -> dict[str, str]:
    """Return cached session cookies, or an empty mapping if there are none."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, dict):
        return {}
    return {str(k): str(v) for k, v in cookies.items()}


def save_session(client: httpx.Client, path: Path) -> None:
    """Persist the client's cookies for the next invocation."""
    cookies = {c.name: c.value or "" for c in client.cookies.jar}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": cookies}))
    path.chmod(0o600)


def clear_session(path: Path) -> bool:
    """Delete the cached session. Returns whether a file was removed."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def build_client(cfg: Config, cookies: dict[str, str] | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        cookies=dict(cookies or {}),
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
            authenticated = str(attributes.get("authenticated", "")).lower() == "true"
            return AuthState(authenticated=authenticated)
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


def read_login_error(html: str) -> str | None:
    """Return the portal's stated reason for refusing a login, if it gave one."""
    for match in _LOGIN_ERROR.finditer(html):
        text = html_module.unescape(_TAG.sub("", match.group(1)))
        collapsed = " ".join(text.split())
        if collapsed:
            return collapsed
    return None


def login(client: httpx.Client, cfg: Config, page: str = BOOTSTRAP_PAGE) -> AuthState:
    """Authenticate with username and password through the classic login form."""
    if not cfg.has_credentials:
        raise SessionExpired(
            "login",
            "no username/password configured, so the session cannot be renewed",
        )
    # A rejected session has to be dropped before the form is fetched: with a
    # stale sid still attached the portal replays that same unauthenticated
    # session through frontdoor.jsp instead of minting a new one, so an expired
    # cache could never renew itself and every command failed until the cache
    # was deleted by hand.
    client.cookies.clear()
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
    # A successful login redirects through frontdoor.jsp, which sets the session
    # cookies; httpx follows it because the client allows redirects. A refused
    # one answers 200 with the form again and the reason in it, so read that
    # rather than paying for a bootstrap page that can only say "not logged in".
    response = client.post(LOGIN_PAGE, data=fields)
    response.raise_for_status()
    error = read_login_error(response.text)
    if error:
        return AuthState(authenticated=False, error=error)
    return check_auth(client, page)


def open_session(
    cfg: Config,
    page: str = BOOTSTRAP_PAGE,
    *,
    use_cache: bool = True,
) -> tuple[httpx.Client, AuraSession, str]:
    """Return a client, an Aura session, and how the session was obtained.

    A cached session is reused when the portal still accepts it, which keeps the
    common case down to two requests and avoids a login event per command.
    """
    cache = session_path(cfg.base_url)
    cached = load_session(cache) if use_cache else {}
    client = build_client(cfg, cached)
    source = "cache" if cached else "none"

    state = check_auth(client, page)
    if not state.authenticated:
        state = login(client, cfg, page)
        source = "login"
        if not state.authenticated:
            client.close()
            raise SessionExpired(
                "login", state.error or "portal still reports an unauthenticated session"
            )

    if source == "login":
        save_session(client, cache)

    return client, AuraSession.bootstrap(client, page), source


def with_session[T](
    cfg: Config,
    page: str,
    action: Callable[[AuraSession], T],
    *,
    use_cache: bool = True,
) -> T:
    """Run ``action`` against an authenticated session.

    A cached session can expire between commands, and Salesforce signals that
    mid-request rather than up front, so retry once from a clean login instead
    of surfacing an error the user can do nothing about.
    """
    client, session, source = open_session(cfg, page, use_cache=use_cache)
    try:
        return action(session)
    except SessionExpired:
        if source != "cache":
            raise
    finally:
        client.close()

    clear_session(session_path(cfg.base_url))
    client, session, _ = open_session(cfg, page, use_cache=False)
    try:
        return action(session)
    finally:
        client.close()
