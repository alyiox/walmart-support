from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from walmart_support.cases import COMMENT_MAX_CHARS
from walmart_support.cli import main


def _config(tmp_path: Path, portal: str = "walmart") -> Path:
    """A throwaway config, so a developer's real one stays out of the tests."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"portals": {portal: {"username": "tester@example.com", "password": "unused"}}})
    )
    return path


@pytest.fixture
def cli(tmp_path: Path) -> Callable[..., int]:
    """Run the CLI against a throwaway Walmart config.

    Every refusal below happens before the first request, so no portal is
    actually reached.
    """
    config = _config(tmp_path)

    def run(*argv: str) -> int:
        return main(["--config", str(config), "--portal", "walmart", *argv])

    return run


def test_reply_refuses_an_empty_message(
    cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = tmp_path / "reply.txt"
    body.write_text("   \n\n")

    assert cli("cases", "reply", "10000001", "--message-file", str(body)) == 2
    assert "empty reply" in capsys.readouterr().err


def test_reply_refuses_a_body_over_the_portal_cap(
    cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Under the cap as plain text; over it once the portal escapes the markup
    # and expands the paragraph breaks.
    body = tmp_path / "reply.txt"
    body.write_text("\n\n".join(["<observed>" * 4] * 90))
    assert len(body.read_text()) < COMMENT_MAX_CHARS

    assert cli("cases", "reply", "10000001", "--message-file", str(body)) == 2
    err = capsys.readouterr().err
    assert str(COMMENT_MAX_CHARS) in err
    assert "would post nothing" in err


def test_close_refuses_a_portal_where_it_is_a_silent_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Refused before a session is opened. closeCaseSt answers SUCCESS on Sam's
    # Club and leaves the status untouched, so firing it would read as a closed
    # case to anyone who does not re-read the status afterwards.
    code = main(
        [
            "--config",
            str(_config(tmp_path, "samsclub")),
            "--portal",
            "samsclub",
            "cases",
            "close",
            "00010001",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "not supported" in err
    assert "Close Case" in err


def test_omitting_the_portal_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # There is no default portal anywhere, so a command that does not name one
    # cannot run. argparse refuses it, which is exit 2 like every usage fault.
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(_config(tmp_path)), "cases", "list"])

    assert exit_info.value.code == 2
    assert "--portal" in capsys.readouterr().err


def test_the_config_cannot_select_a_portal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A default.portal left over from an older config is inert: the flag is
    # the only thing that chooses, so the stale key cannot redirect a write.
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "default": {"portal": "samsclub"},
                "portals": {"walmart": {"username": "u", "password": "p"}},
            }
        )
    )

    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(path), "cases", "list"])
    assert exit_info.value.code == 2

    # And naming Walmart reaches Walmart, not the portal the stale key names.
    assert main(["--config", str(path), "--portal", "walmart", "cases", "close", "1"]) == 1
    assert "samsclub" not in capsys.readouterr().err


def test_the_flag_alone_selects_a_portal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing in the config chooses a portal, so the flag has to be enough.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"portals": {"samsclub": {"username": "u", "password": "p"}}}))

    assert main(["--config", str(path), "--portal", "samsclub", "cases", "close", "1"]) == 2
    assert "not supported" in capsys.readouterr().err


def test_an_unknown_portal_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--config", str(_config(tmp_path)), "--portal", "target", "cases", "list"])
    assert code == 2
    assert "unknown portal" in capsys.readouterr().err
