"""One-off: strip probe files off closed case 15957410.

Verifying the upload contract attached test files to that case. Deletion is
asymmetric — ``saveChunk`` hands back a ContentVersion id (``068…``) while
``deleteAttachments`` wants the ContentDocument id (``069…``) and silently does
nothing when given the other — so discover the ids from the case payload rather
than reusing what the upload returned.

Run with:  uv run python scripts/cleanup_probe_attachment.py
"""

from __future__ import annotations

from mcp_walmart_support.attachments import document_ids
from mcp_walmart_support.aura import AuraError, AuraSession
from mcp_walmart_support.auth import with_session
from mcp_walmart_support.config import load_config

CASE = "15957410"
CASE_PAGE = f"/s/cases?casenumber={CASE}&language=en_US"


def run(session: AuraSession) -> None:
    raw = session.apex("AC_CaseDetail", "getCaseData", {"casenumber": CASE})
    ids = document_ids(raw)
    print(f"case {CASE}: {raw.get('attachmentNumber')} attachment(s), documents={ids}")

    for document_id in ids:
        try:
            session.apex("AC_CaseDetail", "deleteAttachments", {"filesToDelete": document_id})
            print(f"  deleteAttachments({document_id}) -> ok")
        except AuraError as exc:
            print(f"  deleteAttachments({document_id}) -> ERR {exc.aura_message[:100]}")

    after = session.apex("AC_CaseDetail", "getCaseData", {"casenumber": CASE})
    remaining = document_ids(after)
    print(f"now: {after.get('attachmentNumber')} attachment(s), documents={remaining}")
    if remaining:
        print("still attached — remove them from the case page in the portal")


if __name__ == "__main__":
    with_session(load_config(), CASE_PAGE, run)
