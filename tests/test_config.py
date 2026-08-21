from __future__ import annotations

import json
from pathlib import Path

import pytest

from walmart_support.config import DEFAULT_BASE_URL, load_config


def _write(tmp_path: Path, data: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_loads_credentials_and_defaults(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {"username": "u@example.com", "password": "p"}))
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.has_credentials


def test_trailing_slash_stripped_from_base_url(tmp_path: Path) -> None:
    cfg = load_config(
        _write(tmp_path, {"base_url": "https://x.test/", "username": "u", "password": "p"})
    )
    assert cfg.base_url == "https://x.test"


def test_a_session_cannot_be_configured_by_hand(tmp_path: Path) -> None:
    # Sessions are managed by the cache; only credentials are configurable, so
    # a stray "cookie" key must not authenticate anything.
    with pytest.raises(RuntimeError, match="No credentials found"):
        load_config(_write(tmp_path, {"cookie": "abc"}))


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALMART_SUPPORT_PASSWORD", "from-env")
    cfg = load_config(_write(tmp_path, {"username": "u", "password": "from-file"}))
    assert cfg.password == "from-env"


def test_env_only_needs_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALMART_SUPPORT_USERNAME", "u@example.com")
    monkeypatch.setenv("WALMART_SUPPORT_PASSWORD", "p")
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.has_credentials


def test_missing_everything_names_the_config_path(tmp_path: Path) -> None:
    target = tmp_path / "absent.json"
    with pytest.raises(RuntimeError, match=str(target)):
        load_config(target)
