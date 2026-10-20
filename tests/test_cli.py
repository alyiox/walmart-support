from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from walmart_support import cli as cli_module
from walmart_support.cases import COMMENT_MAX_CHARS
from walmart_support.cli import main
from walmart_support.create import Submission


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
    # argparse refuses this before the config loads, so it raises SystemExit
    # rather than returning a code.
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(_config(tmp_path)), "cases", "list"])

    assert exit_info.value.code == 2
    assert "--portal" in capsys.readouterr().err


def test_the_config_cannot_select_a_portal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A default.portal left in an older config must not redirect anything.
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

    # Naming Walmart reaches Walmart, not the portal the stale key names: the
    # close gate is Walmart's, so the command gets as far as the session.
    monkeypatch.setattr(cli_module, "with_session", lambda cfg, page, run: ("New", "Closed"))
    assert main(["--config", str(path), "--portal", "walmart", "cases", "close", "1"]) == 0
    assert "samsclub" not in capsys.readouterr().err


def test_the_flag_alone_selects_a_portal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_the_dry_run_names_the_portal_it_would_file_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The payload holds the case, never its destination or the action filing it.
    submission = Submission("openCase", {"subject": "Display API: 500", "problem": "..."})
    monkeypatch.setattr(cli_module, "with_session", lambda cfg, page, run: (submission, None))
    body = tmp_path / "body.txt"
    body.write_text("...")

    code = cli_module.main(
        [
            "--config",
            str(_config(tmp_path)),
            "--portal",
            "walmart",
            "cases",
            "create",
            "--subject",
            "Display API: 500",
            "--description-file",
            str(body),
        ]
    )
    out = capsys.readouterr()
    assert code == 0
    assert "would file at Walmart Connect through openCase." in out.out
    assert "nothing was filed" in out.err


def test_the_dry_run_json_carries_the_portal_like_the_filed_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sam's files its API cases through a wrapper, which the dry run unwraps
    # rather than printing one unreadable line.
    wrapper = {"subject": "s", "problem": "p", "advertiserAffected": ""}
    submission = Submission("saveApiCase", {"advWrapper": wrapper})
    body = tmp_path / "body.txt"
    body.write_text("...")

    def run_create(filed: dict[str, object] | None) -> dict[str, object]:
        monkeypatch.setattr(cli_module, "with_session", lambda cfg, page, run: (submission, filed))
        cli_module.main(
            [
                "--config",
                str(_config(tmp_path, "samsclub")),
                "--json",
                "--portal",
                "samsclub",
                "cases",
                "create",
                "--subject",
                "s",
                "--description-file",
                str(body),
            ]
        )
        return json.loads(capsys.readouterr().out)

    dry = run_create(None)
    filed = run_create({"caseNumber": "00010001"})

    assert dry["portal"] == filed["portal"] == "samsclub"
    assert dry["action"] == filed["action"] == "saveApiCase"
    assert dry["dry_run"] == wrapper


def test_submit_is_refused_while_the_form_has_problems(
    cli: Callable[..., int],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Walmart inserts a case its own form would have refused, so the CLI is
    # the only thing standing between a blank required field and a filing.
    submission = Submission("openCase", {"subject": "s"}, ("'SP Endpoint' is required",))
    filed: list[Submission] = []
    monkeypatch.setattr(cli_module, "prepare", lambda session, draft, portal: submission)
    monkeypatch.setattr(cli_module, "submit", lambda session, sub: filed.append(sub) or {})
    monkeypatch.setattr(cli_module, "with_session", lambda cfg, page, run: run(None))
    body = tmp_path / "body.txt"
    body.write_text("...")

    args = ("cases", "create", "--subject", "s", "--description-file", str(body))
    assert cli(*args, "--submit") == 2
    assert filed == []
    assert "'SP Endpoint' is required" in capsys.readouterr().err

    # the dry run names the same problems and still exits cleanly
    assert cli(*args) == 0
    assert "'SP Endpoint' is required" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--field", "no-equals-sign", "LABEL=VALUE"),
        ("--field-file", "Error Message=/nonexistent/error.txt", "Error Message"),
    ],
)
def test_malformed_form_fields_are_usage_errors(
    cli: Callable[..., int],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
    message: str,
) -> None:
    body = tmp_path / "body.txt"
    body.write_text("...")
    args = ("cases", "create", "--subject", "s", "--description-file", str(body))
    assert cli(*args, flag, value) == 2
    assert message in capsys.readouterr().err


def test_a_form_field_given_twice_is_a_usage_error(
    cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = tmp_path / "body.txt"
    body.write_text("...")
    args = ("cases", "create", "--subject", "s", "--description-file", str(body))
    assert cli(*args, "--field", "SP Endpoint=/a", "--field", "sp endpoint=/b") == 2
    assert "given twice" in capsys.readouterr().err
