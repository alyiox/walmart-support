from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_walmart_support.config import DEFAULT_BASE_URL, load_config


def _write(tmp_path: Path, data: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_loads_credentials_and_defaults(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {"username": "u@example.com", "password": "p"}))
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.has_credentials and not cfg.has_cookie


def test_trailing_slash_stripped_from_base_url(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {"base_url": "https://x.test/", "cookie": "abc"}))
    assert cfg.base_url == "https://x.test"


def test_cookie_alone_is_enough(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, {"cookie": "abc"}))
    assert cfg.has_cookie and not cfg.has_credentials


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALMART_SUPPORT_PASSWORD", "from-env")
    cfg = load_config(_write(tmp_path, {"username": "u", "password": "from-file"}))
    assert cfg.password == "from-env"


def test_env_only_needs_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALMART_SUPPORT_COOKIE", "c")
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.cookie == "c"


def test_missing_everything_names_the_config_path(tmp_path: Path) -> None:
    target = tmp_path / "absent.json"
    with pytest.raises(RuntimeError, match=str(target)):
        load_config(target)
