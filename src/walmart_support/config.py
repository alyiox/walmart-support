"""Portal connection settings.

One config file describes every portal the CLI can reach. It is the *only*
source: there is no environment fallback, so "which credentials did that
command use?" is always answerable by reading one file, and adding a portal
does not double an environment-variable surface.

``--config`` points at a different file, which is how CI supplies credentials
written from a secret.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .portals import PORTALS, Portal, resolve_portal

CONFIG_PATH = Path.home() / ".config" / "walmart-support" / "config.json"

DEFAULT_TIMEOUT = 60

EXPECTED_SHAPE = """{
  "default": {"timeout": 60},
  "portals": {
    "walmart":  {"username": "...", "password": "..."},
    "samsclub": {"username": "...", "password": "..."}
  }
}"""


@dataclass(frozen=True)
class Config:
    """One portal's settings, resolved.

    Username and password are the only way in: they are the only credential
    that can renew an expired session, and sessions themselves are managed by
    the on-disk cache rather than configured by hand.
    """

    portal: Portal
    base_url: str
    username: str
    password: str
    timeout: int

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _timeout(*candidates: object) -> int:
    """First usable timeout among ``candidates``, else the default.

    A portal section may override ``default.timeout``, so the caller passes
    them most-specific first.
    """
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return int(value)
            except ValueError:
                continue
    return DEFAULT_TIMEOUT


def load_config(path: Path = CONFIG_PATH, *, portal: str) -> Config:
    """Load settings for one portal.

    ``portal`` is the ``--portal`` flag, and the only thing that selects one.
    """
    if not path.exists():
        raise RuntimeError(
            f"No config at {path}. Create it based on config.example.json:\n\n{EXPECTED_SHAPE}"
        )
    loaded = json.loads(path.read_text())
    raw: dict[str, Any] = loaded if isinstance(loaded, dict) else {}

    defaults = _mapping(raw, "default")
    if not portal:
        # The CLI makes --portal required, so this guards other callers.
        raise RuntimeError(f"no portal named. Pass one of: {', '.join(PORTALS)}.")
    selected = resolve_portal(portal)

    portals = _mapping(raw, "portals")
    section = _mapping(portals, selected.key)
    if not section:
        configured = ", ".join(sorted(portals)) or "(none)"
        raise RuntimeError(
            f"No {selected.key!r} section in {path} (configured: {configured}). "
            f"Expected shape:\n\n{EXPECTED_SHAPE}"
        )

    # A portal may point somewhere else — a sandbox — but normally the
    # profile owns the URL.
    base_url = str(section.get("base_url") or selected.base_url)
    cfg = Config(
        portal=selected,
        base_url=base_url.rstrip("/"),
        username=str(section.get("username") or ""),
        password=str(section.get("password") or ""),
        timeout=_timeout(section.get("timeout"), defaults.get("timeout")),
    )

    if not cfg.has_credentials:
        raise RuntimeError(
            f"No credentials found for the {selected.label} portal: set username and "
            f"password under portals.{selected.key} in {path}."
        )
    return cfg
