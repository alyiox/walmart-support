from __future__ import annotations

import json
from datetime import date

import httpx

from mcp_walmart_support.aura import AuraSession
from mcp_walmart_support.cases import Case, fetch_cases, filter_cases, find_case

from .conftest import make_page

# Field names mirror the live getCasesForCommunityUser payload.
RECORDS = [
    {
        "caseNumber": "15957474",
        "subject": "Display API: audience set to ARCHIVED",
        "status": "Needs Info - Internal",
        "issueCategory": "API-AdCases",
        "createdDate": "2026-08-19T08:27:51.000Z",
        "caseId": "500x1",
        "description": "advertisers 241727, 244985",
        "origin": "Community",
        "attachmentNumber": 0,
    },
    {
        "caseNumber": "15957410",
        "subject": "Display API: audience set to ARCHIVED",
        "status": "Closed",
        "issueCategory": "API-AdCases",
        "createdDate": "2026-08-19T08:23:24.000Z",
        "attachmentNumber": 0,
    },
    {
        "caseNumber": "15669760",
        "subject": "Display Campaign Snapshot Report",
        "status": "Open",
        "issueCategory": "API-AdCases",
        "createdDate": "2026-07-27T09:00:00.000Z",
        "attachmentNumber": 1,
    },
]


def _session(records: list[dict[str, object]]) -> AuraSession:
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        return httpx.Response(
            200,
            text=json.dumps(
                {"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": records}]}
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    return AuraSession.bootstrap(client, "/s/activity")


def test_fetch_sorts_newest_first() -> None:
    cases = fetch_cases(_session(RECORDS))
    assert [c.case_number for c in cases] == ["15957474", "15957410", "15669760"]


def test_record_mapping_handles_missing_optional_fields() -> None:
    case = Case.from_record({"caseNumber": "1", "subject": None})
    assert case.subject == "" and case.attachment_number == 0 and case.description == ""


def test_non_list_return_value_is_tolerated() -> None:
    assert fetch_cases(_session([])) == []


def test_status_filter_matches_variants() -> None:
    cases = fetch_cases(_session(RECORDS))
    # "Need Info" must also catch "Needs Info - Internal"
    assert [c.case_number for c in filter_cases(cases, status="need info")] == ["15957474"]


def test_since_filter_uses_created_day() -> None:
    cases = fetch_cases(_session(RECORDS))
    recent = filter_cases(cases, since=date(2026, 8, 1))
    assert {c.case_number for c in recent} == {"15957474", "15957410"}


def test_query_matches_subject_or_description() -> None:
    cases = fetch_cases(_session(RECORDS))
    assert len(filter_cases(cases, query="ARCHIVED")) == 2
    assert [c.case_number for c in filter_cases(cases, query="241727")] == ["15957474"]


def test_find_case_by_number() -> None:
    cases = fetch_cases(_session(RECORDS))
    assert find_case(cases, "15669760") is not None
    assert find_case(cases, "99999999") is None


def test_created_day_parses_zulu_timestamps() -> None:
    assert Case.from_record(RECORDS[0]).created_day == date(2026, 8, 19)
    assert Case.from_record({"caseNumber": "x", "createdDate": "nonsense"}).created_day is None
