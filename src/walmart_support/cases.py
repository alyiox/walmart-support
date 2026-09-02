"""Support case reads.

Two actions back the portal's case views. The list returns every case in one
shot (no server-side paging or filtering), so filtering happens here — but it
abbreviates ``subject`` and ``description``. The detail action returns the full
text of one case plus its conversation, so it is what ``get``/``replies`` use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from html import escape, unescape
from typing import Any

from .aura import AuraError, AuraSession

ACTIVITY_PAGE = "/s/activity?language=en_US"

_WORD = re.compile(r"[a-z0-9]+")

_COMPONENT = "AC_Activity"
_LIST_METHOD = "getCasesForCommunityUser"

_DETAIL_COMPONENT = "AC_CaseDetail"
_DETAIL_METHOD = "getCaseData"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]*\n\s*\n\s*")


@dataclass(frozen=True)
class Case:
    case_number: str
    subject: str
    status: str
    issue_category: str
    created_date: str
    case_id: str = ""
    description: str = ""
    origin: str = ""
    attachment_number: int = 0

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Case:
        return cls(
            case_number=str(record.get("caseNumber", "")),
            subject=str(record.get("subject") or ""),
            status=str(record.get("status") or ""),
            issue_category=str(record.get("issueCategory") or ""),
            created_date=str(record.get("createdDate") or ""),
            case_id=str(record.get("caseId") or ""),
            description=str(record.get("description") or ""),
            origin=str(record.get("origin") or ""),
            attachment_number=int(record.get("attachmentNumber") or 0),
        )

    @property
    def created_day(self) -> date | None:
        if not self.created_date:
            return None
        try:
            return datetime.fromisoformat(self.created_date.replace("Z", "+00:00")).date()
        except ValueError:
            return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_number": self.case_number,
            "created_date": self.created_date,
            "status": self.status,
            "issue_category": self.issue_category,
            "subject": self.subject,
            "attachments": self.attachment_number,
            "origin": self.origin,
            "description": self.description,
        }


def _status_words(status: str) -> frozenset[str]:
    """Reduce a status to comparable word stems, ignoring a trailing plural."""
    return frozenset(w.rstrip("s") or w for w in _WORD.findall(status.casefold()))


def html_to_text(html: str) -> str:
    """Flatten a comment body to readable text.

    Portal replies arrive as HTML fragments (letterhead tables, ``&nbsp;``
    padding), which are unreadable in a terminal and pointless to keep.
    """
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = _TAG.sub("", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    return _WS.sub("\n\n", "\n".join(lines)).strip()


@dataclass(frozen=True)
class Comment:
    """One message in a case's conversation."""

    created_date: str
    author: str
    from_advertiser: bool
    body: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Comment:
        return cls(
            created_date=str(record.get("createdDate") or ""),
            author=str(record.get("name") or ""),
            from_advertiser=bool(record.get("isAdvertiser")),
            body=html_to_text(str(record.get("textbody") or "")),
        )

    @property
    def who(self) -> str:
        return "us" if self.from_advertiser else "walmart"

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_date": self.created_date,
            "from": self.who,
            "author": self.author,
            "body": self.body,
        }


@dataclass(frozen=True)
class CaseDetail:
    """One case with its untruncated text and conversation."""

    case_id: str
    case_number: str
    subject: str
    description: str
    status: str
    priority: str
    issue_category: str
    sub_category: str
    created_date: str
    link: str
    contact_name: str
    contact_email: str
    attachments: int
    additional_fields: dict[str, str]
    comments: list[Comment]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CaseDetail:
        detail = payload.get("detail")
        detail = detail if isinstance(detail, dict) else {}
        extras = {
            str(f.get("fieldLabel")): str(f.get("fieldValue") or "")
            for f in payload.get("addnlFlds") or []
            if isinstance(f, dict) and f.get("fieldLabel")
        }
        comments = [
            Comment.from_record(c) for c in payload.get("comments") or [] if isinstance(c, dict)
        ]
        return cls(
            case_id=str(detail.get("Id") or ""),
            case_number=str(detail.get("CaseNumber") or ""),
            subject=str(detail.get("Subject") or ""),
            description=str(detail.get("Description") or ""),
            status=str(detail.get("Status") or ""),
            priority=str(detail.get("Priority") or ""),
            issue_category=str(detail.get("Issue_Category__c") or ""),
            sub_category=str(detail.get("Sub_Category_1__c") or ""),
            created_date=str(detail.get("CreatedDate") or ""),
            link=str(detail.get("CommunityCaseLink__c") or ""),
            contact_name=str(detail.get("Sponsored_Ad_Contact_Name__c") or ""),
            contact_email=str(detail.get("Sponsored_Ad_Contact_Email__c") or ""),
            attachments=int(payload.get("attachmentNumber") or 0),
            additional_fields=extras,
            comments=sorted(comments, key=lambda c: c.created_date),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_number": self.case_number,
            "created_date": self.created_date,
            "status": self.status,
            "priority": self.priority,
            "issue_category": self.issue_category,
            "sub_category": self.sub_category,
            "subject": self.subject,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "attachments": self.attachments,
            "link": self.link,
            "additional_fields": self.additional_fields,
            "description": self.description,
            "comments": [c.as_dict() for c in self.comments],
        }


def fetch_case_detail(session: AuraSession, case_number: str) -> CaseDetail:
    """Return one case with full text and its conversation.

    The parameter really is spelled ``casenumber``, all lowercase.
    """
    raw = session.apex(_DETAIL_COMPONENT, _DETAIL_METHOD, {"casenumber": case_number})
    if not isinstance(raw, dict) or not (raw.get("detail") or {}).get("CaseNumber"):
        raise AuraError(_DETAIL_METHOD, f"case {case_number} not found")
    return CaseDetail.from_payload(raw)


DEEP_SEARCH_CAP = 25


def pushdown_limit(limit: int, *, filtered: bool) -> int | None:
    """Decide whether ``limit`` can be handed to the server.

    The portal applies it as a SOQL ``LIMIT``, i.e. *before* any filtering, so
    pushing it down alongside a filter would narrow within an arbitrary slice
    rather than returning the newest matches.
    """
    if limit > 0 and not filtered:
        return limit
    return None


COMMENT_MAX_CHARS = 4000


def rendered_length(message: str) -> int:
    """Length of ``message`` as the portal stores it.

    ``saveCaseComment`` escapes the text and turns every newline into ``<br>``,
    then measures *that* against its own cap, so a blank line between
    paragraphs costs 8 characters rather than 2.
    """
    return len(escape(message).replace("\n", "<br>"))


def post_comment(session: AuraSession, case_id: str, message: str) -> list[Comment]:
    """Post a reply on a case and return its conversation as the portal sees it.

    The portal's own comment box submits attachment removals alongside the text,
    hence ``filesToDelete``; sending it empty posts the comment alone.
    """
    raw = session.apex(
        _DETAIL_COMPONENT,
        "saveCaseComment",
        {"comment": message, "caseID": case_id, "filesToDelete": ""},
    )
    records = raw if isinstance(raw, list) else []
    comments = [Comment.from_record(r) for r in records if isinstance(r, dict)]
    return sorted(comments, key=lambda c: c.created_date)


def close_case(session: AuraSession, case_id: str) -> str:
    """Close a case and return the status the portal reports afterwards."""
    raw = session.apex(_DETAIL_COMPONENT, "closeCaseSt", {"caseID": case_id})
    if isinstance(raw, dict):
        return str(raw.get("Status") or "")
    return ""


def fetch_cases(session: AuraSession, limit: int | None = None) -> list[Case]:
    """Return cases visible to the logged-in community user, newest first.

    ``limit`` becomes the portal's ``casesTofetch``. That value is interpolated
    straight into its SOQL, so it is coerced to an int and never passed through
    as caller-supplied text.
    """
    requested = "" if limit is None else str(int(limit))
    raw = session.apex(_COMPONENT, _LIST_METHOD, {"casesTofetch": requested})
    records = raw if isinstance(raw, list) else []
    cases = [Case.from_record(r) for r in records if isinstance(r, dict)]
    return sorted(cases, key=lambda c: c.created_date, reverse=True)


def filter_cases(
    cases: list[Case],
    *,
    status: str | None = None,
    since: date | None = None,
    query: str | None = None,
) -> list[Case]:
    """Narrow a case list.

    ``status`` matches on words rather than as a substring, because the portal
    mixes close variants — "Need Info" and "Needs Info - Internal" are distinct
    statuses — and neither an exact match nor a substring match finds both
    ("need info" is not a substring of "needs info - internal").
    """
    result = cases
    if status:
        wanted = _status_words(status)
        result = [c for c in result if wanted <= _status_words(c.status)]
    if since:
        result = [c for c in result if (day := c.created_day) and day >= since]
    if query:
        needle = query.casefold()
        result = [
            c
            for c in result
            if needle in c.subject.casefold() or needle in c.description.casefold()
        ]
    return result


class TooManyCandidates(RuntimeError):
    """A deep search would need more detail requests than the cap allows."""


def deep_filter(
    session: AuraSession,
    cases: list[Case],
    query: str,
    *,
    cap: int = DEEP_SEARCH_CAP,
) -> list[Case]:
    """Match ``query`` against each case's full text, one request per case.

    The list action abbreviates subject and description, so a plain filter can
    miss a match that is present in the real body. This reads each candidate's
    detail instead — including the conversation, so support's own replies are
    searchable too. Costs one request per candidate, hence the cap.
    """
    if len(cases) > cap:
        raise TooManyCandidates(
            f"{len(cases)} candidates exceeds the deep-search cap of {cap}; "
            "narrow with --status/--since or raise --limit"
        )
    needle = query.casefold()
    matched: list[Case] = []
    for case in cases:
        detail = fetch_case_detail(session, case.case_number)
        haystack = "\n".join(
            [detail.subject, detail.description, *(c.body for c in detail.comments)]
        )
        if needle in haystack.casefold():
            matched.append(case)
    return matched


def find_case(cases: list[Case], case_number: str) -> Case | None:
    return next((c for c in cases if c.case_number == case_number), None)
