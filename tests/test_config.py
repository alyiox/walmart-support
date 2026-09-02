from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from walmart_support.config import DEFAULT_TIMEOUT, load_config
from walmart_support.portals import SAMSCLUB, WALMART, PortalError


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def _both(**default: Any) -> dict[str, Any]:
    return {
        "default": default,
        "portals": {
            "walmart": {"username": "w@example.com", "password": "wp"},
            "samsclub": {"username": "s@example.com", "password": "sp"},
        },
    }


def test_defaults_to_walmart_without_a_named_portal(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _both()))
    assert cfg.portal is WALMART
    assert cfg.base_url == WALMART.base_url
    assert cfg.timeout == DEFAULT_TIMEOUT
    assert cfg.has_credentials


def test_the_config_default_selects_a_portal(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _both(portal="samsclub")))
    assert cfg.portal is SAMSCLUB
    assert cfg.username == "s@example.com"
    assert cfg.base_url == SAMSCLUB.base_url


def test_the_argument_beats_the_config_default(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _both(portal="samsclub")), portal="walmart")
    assert cfg.portal is WALMART
    assert cfg.username == "w@example.com"


def test_brand_spellings_resolve(tmp_path: Path) -> None:
    path = _write(tmp_path, _both())
    assert load_config(path, portal="Sam's Club").portal is SAMSCLUB
    assert load_config(path, portal="sams").portal is SAMSCLUB
    assert load_config(path, portal="walmart-connect").portal is WALMART


def test_an_unknown_portal_lists_the_valid_ones(tmp_path: Path) -> None:
    with pytest.raises(PortalError, match="unknown portal"):
        load_config(_write(tmp_path, _both()), portal="target")


def test_a_portal_section_overrides_the_default_timeout(tmp_path: Path) -> None:
    raw = _both(timeout=15)
    raw["portals"]["samsclub"]["timeout"] = 90
    path = _write(tmp_path, raw)
    assert load_config(path, portal="walmart").timeout == 15
    assert load_config(path, portal="samsclub").timeout == 90


def test_a_portal_may_override_its_base_url(tmp_path: Path) -> None:
    # Pointing one portal at a sandbox, while the profile supplies the rest.
    raw = _both()
    raw["portals"]["walmart"]["base_url"] = "https://sandbox.test/"
    assert load_config(_write(tmp_path, raw)).base_url == "https://sandbox.test"


def test_a_missing_section_names_what_is_configured(tmp_path: Path) -> None:
    raw = {"portals": {"walmart": {"username": "u", "password": "p"}}}
    with pytest.raises(RuntimeError, match="samsclub"):
        load_config(_write(tmp_path, raw), portal="samsclub")


def test_one_portals_credentials_are_never_reused_for_the_other(tmp_path: Path) -> None:
    # Sending Walmart's password to a different Salesforce org would be a real
    # credential leak, so an absent section refuses rather than falling back.
    raw = {"portals": {"walmart": {"username": "w@example.com", "password": "wp"}}}
    with pytest.raises(RuntimeError):
        load_config(_write(tmp_path, raw), portal="samsclub")


def test_a_session_cannot_be_configured_by_hand(tmp_path: Path) -> None:
    # Sessions are managed by the cache; only credentials are configurable, so
    # a stray "cookie" key must not authenticate anything.
    raw = {"portals": {"walmart": {"cookie": "abc"}}}
    with pytest.raises(RuntimeError, match="No credentials found"):
        load_config(_write(tmp_path, raw))


def test_a_missing_file_names_the_config_path(tmp_path: Path) -> None:
    target = tmp_path / "absent.json"
    with pytest.raises(RuntimeError, match=re.escape(str(target))):
        load_config(target)


def test_the_environment_cannot_supply_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The file is the only source, so which credentials a command used is
    # always answerable by reading one path.
    monkeypatch.setenv("WALMART_SUPPORT_USERNAME", "env@example.com")
    monkeypatch.setenv("WALMART_SUPPORT_PASSWORD", "env-pw")
    with pytest.raises(RuntimeError):
        load_config(tmp_path / "absent.json")
