from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import pytest

from walmart_support.attachments import (
    CHUNK_CHARS,
    chunk,
    content_type_for,
    upload_file,
)
from walmart_support.aura import AuraError, AuraSession

from .conftest import make_page


def test_chunking_splits_and_never_returns_empty() -> None:
    assert chunk("abcdef", 2) == ["ab", "cd", "ef"]
    assert chunk("abc", 10) == ["abc"]
    # an empty file still needs one call, or nothing is created
    assert chunk("", 10) == [""]
    assert CHUNK_CHARS > 100_000


def test_content_type_is_guessed_with_a_fallback(tmp_path: Path) -> None:
    assert content_type_for(tmp_path / "a.txt") == "text/plain"
    assert content_type_for(tmp_path / "a.json") == "application/json"
    assert content_type_for(tmp_path / "a.weirdext") == "application/octet-stream"


def _session(calls: list[dict[str, Any]], ids: list[str | None]) -> AuraSession:
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        action = json.loads(urllib.parse.parse_qs(request.content.decode())["message"][0])[
            "actions"
        ][0]
        calls.append(dict(action["params"]))
        value = ids[len(calls) - 1] if len(calls) <= len(ids) else None
        return httpx.Response(
            200,
            text=json.dumps({"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": value}]}),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    return AuraSession.bootstrap(client, "/s/activity")


def test_upload_sends_base64_and_returns_the_version_id(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_bytes(b"hello")
    calls: list[dict[str, Any]] = []
    upload = upload_file(_session(calls, ["068ABC"]), "500XYZ", path)

    assert upload.content_version_id == "068ABC"
    assert upload.byte_size == 5 and upload.content_type == "text/plain"
    assert calls[0]["parentId"] == "500XYZ"
    assert calls[0]["fileName"] == "report.txt"
    assert base64.b64decode(str(calls[0]["base64Data"])) == b"hello"


def test_later_chunks_carry_the_id_the_first_call_minted(tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(b"\x00" * 90)  # 120 base64 chars
    calls: list[dict[str, Any]] = []
    upload = upload_file(_session(calls, ["068ABC", None, None]), "500XYZ", path, chunk_chars=50)

    assert len(calls) == 3
    # the first call mints the id, the rest append to it
    assert [c["fileId"] for c in calls] == ["", "068ABC", "068ABC"]
    assert upload.content_version_id == "068ABC"
    rejoined = "".join(str(c["base64Data"]) for c in calls)
    assert base64.b64decode(rejoined) == b"\x00" * 90


def test_empty_parent_is_refused_before_any_request(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("x")
    calls: list[dict[str, Any]] = []
    with pytest.raises(AuraError, match="parentId is required"):
        upload_file(_session(calls, []), "", path)
    assert calls == []
