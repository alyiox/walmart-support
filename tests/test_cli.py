from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from walmart_support.cases import COMMENT_MAX_CHARS
from walmart_support.cli import main


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., int]:
    """Run the CLI with throwaway credentials and no config file on disk.

    Both refusals below happen before the first request, so no portal is
    needed; the absent path keeps a developer's real config out of the test.
    """
    monkeypatch.setenv("WALMART_SUPPORT_USERNAME", "tester@example.com")
    monkeypatch.setenv("WALMART_SUPPORT_PASSWORD", "unused")

    def run(*argv: str) -> int:
        return main(["--config", str(tmp_path / "absent.json"), *argv])

    return run


def test_reply_refuses_an_empty_message(
    cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = tmp_path / "reply.txt"
    body.write_text("   \n\n")

    assert cli("cases", "reply", "15957474", "--message-file", str(body)) == 2
    assert "empty reply" in capsys.readouterr().err


def test_reply_refuses_a_body_over_the_portal_cap(
    cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Under the cap as plain text; over it once the portal escapes the markup
    # and expands the paragraph breaks.
    body = tmp_path / "reply.txt"
    body.write_text("\n\n".join(["<observed>" * 4] * 90))
    assert len(body.read_text()) < COMMENT_MAX_CHARS

    assert cli("cases", "reply", "15957474", "--message-file", str(body)) == 2
    err = capsys.readouterr().err
    assert str(COMMENT_MAX_CHARS) in err
    assert "would post nothing" in err
