"""Support case reads.

One Apex method backs the portal's whole case list, and it returns every case
in one shot (no server-side paging or filtering), so filtering happens here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .aura import AuraSession

ACTIVITY_PAGE = "/s/activity?language=en_US"

_WORD = re.compile(r"[a-z0-9]+")

_COMPONENT = "AC_Activity"
_LIST_METHOD = "getCasesForCommunityUser"


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


def fetch_cases(session: AuraSession) -> list[Case]:
    """Return every case visible to the logged-in community user, newest first."""
    raw = session.apex(_COMPONENT, _LIST_METHOD, {"casesTofetch": ""})
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


def find_case(cases: list[Case], case_number: str) -> Case | None:
    return next((c for c in cases if c.case_number == case_number), None)
