from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "mcp-walmart-support" / "config.json"

DEFAULT_BASE_URL = "https://advertisinghelp.walmart.com"
DEFAULT_TIMEOUT = 60

_ENV_PREFIX = "WALMART_SUPPORT_"


@dataclass(frozen=True)
class Config:
    """Portal connection settings.

    Username and password are the only way in: they are the only credential
    that can renew an expired session, and sessions themselves are managed by
    the on-disk cache rather than configured by hand.
    """

    base_url: str
    username: str
    password: str
    timeout: int

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


def _env(name: str) -> str | None:
    return os.environ.get(_ENV_PREFIX + name)


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load config from ``path``, with ``WALMART_SUPPORT_*`` env overrides.

    The file is optional when the environment alone carries enough to
    authenticate, which keeps CI from needing a config file on disk.
    """
    raw: dict[str, object] = {}
    if path.exists():
        raw = json.loads(path.read_text())

    base_url = _env("BASE_URL") or str(raw.get("base_url") or DEFAULT_BASE_URL)
    username = _env("USERNAME") or str(raw.get("username") or "")
    password = _env("PASSWORD") or str(raw.get("password") or "")
    timeout_raw = _env("TIMEOUT") or raw.get("timeout") or DEFAULT_TIMEOUT
    timeout = int(timeout_raw) if isinstance(timeout_raw, str | int) else DEFAULT_TIMEOUT

    cfg = Config(
        base_url=base_url.rstrip("/"),
        username=username,
        password=password,
        timeout=timeout,
    )

    if not cfg.has_credentials:
        raise RuntimeError(
            f"No credentials found. Create {path} based on config.example.json, "
            f"or set {_ENV_PREFIX}USERNAME and {_ENV_PREFIX}PASSWORD."
        )
    return cfg
