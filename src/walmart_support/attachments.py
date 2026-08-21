"""File uploads.

The portal uploads in base64 chunks through ``saveChunk``: the first call for a
file passes an empty ``fileId`` and gets back a ContentVersion id, and later
chunks pass that id to append. The record it hangs the file on comes from
``parentId``, which is typed ``Id`` and rejects an empty string.

Deletion is asymmetric and worth knowing about: ``saveChunk`` returns a
ContentVersion id (``068…``), while ``deleteAttachments`` and
``deleteContentDoc`` both want the ContentDocument id (``069…``) and quietly do
nothing when handed the other. The ContentDocument id is recoverable from
``getCaseData``.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from .aura import AuraError, AuraSession

_UPLOAD_COMPONENT = "AC_ContactSupport"
_UPLOAD_METHOD = "saveChunk"

# Aura caps request size; this many base64 characters is ~550 KB of file and
# stays well inside it.
CHUNK_CHARS = 750_000

DEFAULT_CONTENT_TYPE = "application/octet-stream"

# ContentDocument ids, as embedded in a case's detail payload.
_DOCUMENT_ID = re.compile(r"\b(069[A-Za-z0-9]{12,15})\b")


@dataclass(frozen=True)
class Upload:
    file_name: str
    content_type: str
    byte_size: int
    content_version_id: str


def content_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or DEFAULT_CONTENT_TYPE


def chunk(data: str, size: int = CHUNK_CHARS) -> list[str]:
    return [data[i : i + size] for i in range(0, len(data), size)] or [""]


def upload_file(
    session: AuraSession,
    parent_id: str,
    path: Path,
    *,
    chunk_chars: int = CHUNK_CHARS,
) -> Upload:
    """Upload one file against ``parent_id`` and return its ContentVersion id."""
    if not parent_id:
        raise AuraError(_UPLOAD_METHOD, "parentId is required; the portal rejects an empty id")
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode()
    content_type = content_type_for(path)

    file_id = ""
    for piece in chunk(encoded, chunk_chars):
        returned = session.apex(
            _UPLOAD_COMPONENT,
            _UPLOAD_METHOD,
            {
                "parentId": parent_id,
                "fileName": path.name,
                "base64Data": piece,
                "contentType": content_type,
                "fileId": file_id,
            },
        )
        # The first call mints the id; the rest append to it.
        file_id = str(returned or file_id)

    if not file_id:
        raise AuraError(_UPLOAD_METHOD, f"upload of {path.name} returned no id")
    return Upload(
        file_name=path.name,
        content_type=content_type,
        byte_size=len(raw),
        content_version_id=file_id,
    )


def document_ids(payload: object) -> list[str]:
    """Pull ContentDocument ids out of a raw case payload.

    Needed because the id required to delete an attachment is not the one the
    upload hands back.
    """
    import json

    return sorted(set(_DOCUMENT_ID.findall(json.dumps(payload))))
