"""Minimal client for the Salesforce Aura endpoint behind the support portal.

This is the only module that depends on Walmart's Experience Cloud internals.
The portal has no public API: every click in its UI is one POST to
``/s/sfsites/aura`` naming an ``@AuraEnabled`` Apex method. Three details were
recovered by observing the portal and none of them are guessable:

* An action's descriptor is ``apex://<Component>Controller/ACTION$<method>``
  while the query-string hint drops the ``Controller`` suffix, so the case list
  is ``other.AC_Activity.getCasesForCommunityUser`` on the wire but
  ``apex://AC_ActivityController/ACTION$getCasesForCommunityUser`` inside.
* Only a minimal slice of the page's framework context is echoed back.
* ``aura.token`` is mandatory and arrives as a **single-use cookie**: the page
  HTML names it in ``eikoocnekot`` ("tokencookie" reversed), the browser's JS
  reads it and deletes it. The name changes per response, so the token must be
  read from the very response that named it.

Aura rejects calls whose context does not match the deployed build, and the
context rotates on every Salesforce release, so it is scraped per run and never
hardcoded. When the contract shifts, failures surface as :class:`AuraError`
naming the action and echoing Aura's own message.
"""

from __future__ import annotations

import json
import re
import secrets
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

# Aura guards JSON responses against script inclusion; errors arrive wrapped in
# ``*/{...}/*ERROR*/`` and some responses carry a ``while(1);`` prefix.
_GUARD_PREFIX = re.compile(r"^\s*(?:while\s*\(1\)\s*;)?\s*\*/")
_GUARD_SUFFIX = re.compile(r"/\*ERROR\*/\s*$")

_CONTEXT_SEGMENT = re.compile(r"/sfsites/l/(%7B[^/]+)/")
_TOKEN_COOKIE_NAME = re.compile(r'"eikoocnekot"\s*:\s*"([^"]+)"')

# The browser echoes only these context keys when dispatching an action; the
# rest of the scraped blob (styleContext, apck, lrmc …) drives asset loading.
_CONTEXT_KEYS = ("mode", "fwuid", "app", "loaded")

_RECAPTURE_HINT = (
    "The Aura contract may have changed with a Salesforce release; "
    "re-capture the action payload from the portal UI."
)


class AuraError(RuntimeError):
    """An Aura action did not return SUCCESS."""

    def __init__(self, action: str, message: str, *, hint: str | None = None) -> None:
        self.action = action
        self.aura_message = message
        parts = [f"{action}: {message}"]
        if hint:
            parts.append(hint)
        super().__init__(" ".join(parts))


class SessionExpired(AuraError):
    """The portal session is missing or no longer valid.

    Salesforce raises ``aura:invalidSession`` rather than a 401, and an expired
    session looks identical to never having logged in. Callers re-authenticate
    once and retry.
    """


@dataclass(frozen=True)
class AuraContext:
    """The framework context a page load hands to its XHRs."""

    fwuid: str
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_html(cls, html: str) -> AuraContext:
        """Extract the context from a rendered portal page.

        A page references several ``/sfsites/l/{...}/`` asset URLs and only some
        carry ``fwuid``; the others describe styling.
        """
        for segment in _CONTEXT_SEGMENT.findall(html):
            try:
                candidate = json.loads(urllib.parse.unquote(segment))
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "fwuid" in candidate:
                return cls(fwuid=str(candidate["fwuid"]), raw=candidate)
        raise AuraError(
            "bootstrap",
            "no Aura context with an fwuid found in the page HTML",
            hint=_RECAPTURE_HINT,
        )

    def payload(self) -> dict[str, Any]:
        ctx = {k: self.raw[k] for k in _CONTEXT_KEYS if k in self.raw}
        # Aura fills these in client-side; the server expects them present.
        return ctx | {"dn": [], "globals": {}, "uad": False}


def extract_token(html: str, cookies: httpx.Cookies) -> str:
    """Read the single-use CSRF token that a page response delivered.

    Returns an empty string when the page names no token cookie, which is the
    guest case: unauthenticated pages run in ``csrfV2`` mode and need no token.
    """
    match = _TOKEN_COOKIE_NAME.search(html)
    if not match:
        return ""
    name = match.group(1)
    return cookies.get(name) or ""


def _unwrap(text: str) -> dict[str, Any]:
    body = _GUARD_SUFFIX.sub("", _GUARD_PREFIX.sub("", text)).strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AuraError("response", f"non-JSON Aura response: {body[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AuraError("response", f"unexpected Aura response shape: {body[:200]!r}")
    return parsed


def _is_invalid_session(envelope: dict[str, Any]) -> bool:
    event = envelope.get("event")
    if isinstance(event, dict) and "invalidSession" in str(event.get("descriptor", "")):
        return True
    return "invalidSession" in json.dumps(envelope.get("exceptionEvent", ""))


class AuraSession:
    """Dispatches Aura actions against one portal page's context."""

    def __init__(
        self,
        client: httpx.Client,
        page_uri: str,
        context: AuraContext,
        token: str = "",
    ) -> None:
        self._client = client
        self._page_uri = page_uri
        self._context = context
        self._token = token
        self._page_scope_id = str(uuid.uuid4())
        self._counter = 0

    @classmethod
    def bootstrap(cls, client: httpx.Client, page_uri: str) -> AuraSession:
        """Load ``page_uri`` and build a session from that one response.

        Context and token must come from the same response: the token cookie is
        single-use and its name changes each time.
        """
        response = client.get(page_uri)
        response.raise_for_status()
        return cls(
            client,
            page_uri,
            AuraContext.from_html(response.text),
            extract_token(response.text, response.cookies),
        )

    def apex(
        self,
        component: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        controller: str | None = None,
    ) -> Any:
        """Call an ``@AuraEnabled`` Apex method and return its ``returnValue``.

        ``component`` is the Aura component name as it appears in the portal's
        own URLs (e.g. ``AC_Activity``); the Apex class is that plus
        ``Controller`` unless ``controller`` overrides it.
        """
        return self._dispatch(
            action_hint=f"other.{component}.{method}",
            descriptor=f"apex://{controller or component + 'Controller'}/ACTION${method}",
            calling_descriptor=f"markup://c:{component}",
            params=params or {},
        )

    def _dispatch(
        self,
        *,
        action_hint: str,
        descriptor: str,
        calling_descriptor: str,
        params: dict[str, Any],
    ) -> Any:
        self._counter += 1
        rid = self._counter
        message = {
            "actions": [
                {
                    "id": f"{rid};a",
                    "descriptor": descriptor,
                    "callingDescriptor": calling_descriptor,
                    "params": params,
                }
            ]
        }
        response = self._client.post(
            "/s/sfsites/aura",
            params={"r": rid, action_hint: 1},
            data={
                "message": json.dumps(message),
                "aura.context": json.dumps(self._context.payload()),
                "aura.pageURI": self._page_uri,
                "aura.token": self._token or "undefined",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-SFDC-Page-Scope-Id": self._page_scope_id,
                "X-SFDC-Request-Id": secrets.token_hex(9),
                "Referer": f"{self._client.base_url}{self._page_uri}",
            },
        )
        envelope = _unwrap(response.text)

        if _is_invalid_session(envelope):
            raise SessionExpired(action_hint, "portal session is invalid or expired")

        actions = envelope.get("actions")
        if not actions:
            # Aura silently drops actions whose descriptor does not resolve.
            raise AuraError(
                action_hint,
                f"action was dropped by Aura (http {response.status_code}); "
                f"descriptor {descriptor} did not resolve",
                hint=_RECAPTURE_HINT,
            )

        action = actions[0]
        state = action.get("state")
        if state != "SUCCESS":
            errors = action.get("error") or []
            detail = "; ".join(str(e.get("message", e)) for e in errors) or f"state={state}"
            raise AuraError(action_hint, detail, hint=_RECAPTURE_HINT)
        return action.get("returnValue")
