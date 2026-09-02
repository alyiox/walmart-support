from __future__ import annotations

import json
import urllib.parse
from datetime import date
from typing import Any

import httpx
import pytest

from walmart_support.aura import AuraError, AuraSession
from walmart_support.cases import (
    COMMENT_MAX_CHARS,
    Case,
    TooManyCandidates,
    close_case,
    deep_filter,
    fetch_case_detail,
    fetch_cases,
    filter_cases,
    find_case,
    html_to_text,
    post_comment,
    pushdown_limit,
    rendered_length,
)

from .conftest import make_page

# Field names mirror the live getCasesForCommunityUser payload.
RECORDS = [
    {
        "caseNumber": "10000001",
        "subject": "Display API: audience set to ARCHIVED",
        "status": "Needs Info - Internal",
        "issueCategory": "API-AdCases",
        "createdDate": "2026-08-19T08:27:51.000Z",
        "caseId": "500x1",
        "description": "advertisers 111111, 222222",
        "origin": "Community",
        "attachmentNumber": 0,
    },
    {
        "caseNumber": "10000002",
        "subject": "Display API: audience set to ARCHIVED",
        "status": "Closed",
        "issueCategory": "API-AdCases",
        "createdDate": "2026-08-19T08:23:24.000Z",
        "attachmentNumber": 0,
    },
    {
        "caseNumber": "10000003",
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
    assert [c.case_number for c in cases] == ["10000001", "10000002", "10000003"]


def test_record_mapping_handles_missing_optional_fields() -> None:
    case = Case.from_record({"caseNumber": "1", "subject": None})
    assert case.subject == "" and case.attachment_number == 0 and case.description == ""


def test_non_list_return_value_is_tolerated() -> None:
    assert fetch_cases(_session([])) == []


def test_status_filter_matches_variants() -> None:
    cases = fetch_cases(_session(RECORDS))
    # "Need Info" must also catch "Needs Info - Internal"
    assert [c.case_number for c in filter_cases(cases, status="need info")] == ["10000001"]


def test_since_filter_uses_created_day() -> None:
    cases = fetch_cases(_session(RECORDS))
    recent = filter_cases(cases, since=date(2026, 8, 1))
    assert {c.case_number for c in recent} == {"10000001", "10000002"}


def test_query_matches_subject_or_description() -> None:
    cases = fetch_cases(_session(RECORDS))
    assert len(filter_cases(cases, query="ARCHIVED")) == 2
    assert [c.case_number for c in filter_cases(cases, query="111111")] == ["10000001"]


def test_find_case_by_number() -> None:
    cases = fetch_cases(_session(RECORDS))
    assert find_case(cases, "10000003") is not None
    assert find_case(cases, "99999999") is None


def test_created_day_parses_zulu_timestamps() -> None:
    assert Case.from_record(RECORDS[0]).created_day == date(2026, 8, 19)
    assert Case.from_record({"caseNumber": "x", "createdDate": "nonsense"}).created_day is None


# --- detail + conversation -------------------------------------------------

DETAIL = {
    "detail": {
        "Id": "5004M00000EXAMPLE",
        "CaseNumber": "10000001",
        "CreatedDate": "2026-08-19T08:27:51.000Z",
        "Subject": "Display API: audience set to ARCHIVED while still referenced",
        "Description": "Environment: Production, US",
        "Status": "Needs Info - Internal",
        "Priority": "Medium",
        "Issue_Category__c": "API-AdCases",
        "Sub_Category_1__c": "Endpoint-specific problem",
        "CommunityCaseLink__c": "https://portal.test/s/cases?casenumber=10000001",
        "Sponsored_Ad_Contact_Name__c": "Ada Advertiser",
        "Sponsored_Ad_Contact_Email__c": "you@example.com",
    },
    "addnlFlds": [{"fieldLabel": "Contact Name", "fieldValue": "Ada Advertiser"}],
    "attachmentNumber": 2,
    "comments": [
        {
            "createdDate": "2026-08-19T14:07:21.000Z",
            "name": "Advertiser Help",
            "isAdvertiser": False,
            "textbody": "<html><div>We&#39;re looking into this.</div><div>&nbsp;</div></html>",
        },
        {
            "createdDate": "2026-08-19T08:27:51.000Z",
            "name": "Ada Advertiser",
            "isAdvertiser": True,
            "textbody": "Original report",
        },
    ],
    "followers": [],
}


def _detail_session(payload: object) -> AuraSession:
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        fields = urllib.parse.parse_qs(request.content.decode())
        action = json.loads(fields["message"][0])["actions"][0]
        # the portal really spells this parameter all lowercase
        assert action["params"] == {"casenumber": "10000001"}
        assert action["descriptor"] == "apex://AC_CaseDetailController/ACTION$getCaseData"
        return httpx.Response(
            200,
            text=json.dumps(
                {"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": payload}]}
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    return AuraSession.bootstrap(client, "/s/activity")


def test_detail_returns_untruncated_text_and_metadata() -> None:
    detail = fetch_case_detail(_detail_session(DETAIL), "10000001")
    assert detail.subject.endswith("still referenced")
    assert detail.status == "Needs Info - Internal"
    assert detail.priority == "Medium"
    assert detail.attachments == 2
    assert detail.additional_fields == {"Contact Name": "Ada Advertiser"}


def test_comments_are_oldest_first_and_attributed() -> None:
    detail = fetch_case_detail(_detail_session(DETAIL), "10000001")
    assert [c.who for c in detail.comments] == ["us", "walmart"]
    assert detail.comments[0].author == "Ada Advertiser"


def test_comment_html_is_flattened() -> None:
    detail = fetch_case_detail(_detail_session(DETAIL), "10000001")
    walmart = detail.comments[-1]
    # tags stripped, entities decoded, &nbsp; padding collapsed
    assert walmart.body == "We're looking into this."


def test_missing_case_raises() -> None:
    with pytest.raises(AuraError, match="not found"):
        fetch_case_detail(_detail_session({"detail": {}, "comments": []}), "10000001")


def test_html_to_text_keeps_paragraph_breaks() -> None:
    assert html_to_text("<div>one</div><br><div>two</div>") == "one\n\ntwo"


# --- limit pushdown and deep search ---------------------------------------


def test_limit_is_pushed_down_only_when_unfiltered() -> None:
    # the portal applies it as a SOQL LIMIT, i.e. before filtering
    assert pushdown_limit(6, filtered=False) == 6
    assert pushdown_limit(6, filtered=True) is None
    assert pushdown_limit(0, filtered=False) is None


def test_limit_reaches_the_portal_as_a_number() -> None:
    seen: list[dict[str, Any]] = []
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        fields = urllib.parse.parse_qs(request.content.decode())
        seen.append(json.loads(fields["message"][0])["actions"][0]["params"])
        return httpx.Response(
            200,
            text=json.dumps({"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": []}]}),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    session = AuraSession.bootstrap(client, "/s/activity")
    fetch_cases(session, 6)
    fetch_cases(session)
    assert seen == [{"casesTofetch": "6"}, {"casesTofetch": ""}]


def test_deep_filter_reads_full_text_the_list_truncates() -> None:
    # the list abbreviates subject; the full text only appears in the detail
    detail = {
        "detail": {
            "CaseNumber": "10000001",
            "Subject": "Display API: audience ARCHIVED while referenced by a LIVE ad group",
            "Description": "c",
        },
        "comments": [],
    }
    session = _detail_session(detail)
    truncated = Case.from_record(
        {"caseNumber": "10000001", "subject": "Display API: audience ARCH...", "description": "c"}
    )
    assert filter_cases([truncated], query="LIVE ad group") == []
    assert deep_filter(session, [truncated], "LIVE ad group") == [truncated]


def test_deep_filter_searches_replies_too() -> None:
    detail = {
        "detail": {"CaseNumber": "10000001", "Subject": "s", "Description": "d"},
        "comments": [
            {"name": "Advertiser Help", "isAdvertiser": False, "textbody": "notified the Eng team"}
        ],
    }
    case = Case.from_record({"caseNumber": "10000001", "subject": "s", "description": "d"})
    assert deep_filter(_detail_session(detail), [case], "eng team") == [case]


def test_deep_filter_refuses_to_fan_out_past_the_cap() -> None:
    cases = [Case.from_record({"caseNumber": str(n)}) for n in range(30)]
    with pytest.raises(TooManyCandidates, match="cap of 25"):
        deep_filter(_detail_session({}), cases, "anything")


def test_rendered_length_counts_plain_text_verbatim() -> None:
    assert rendered_length("retested in prod") == len("retested in prod")


def test_rendered_length_charges_eight_for_a_paragraph_break() -> None:
    # The portal stores "<br><br>", not the two newlines we sent.
    assert rendered_length("one\n\ntwo") - rendered_length("onetwo") == 8


def test_rendered_length_charges_for_escaped_markup() -> None:
    # A raw response body pasted into a reply is where this bites: every
    # angle bracket and ampersand grows on the way into the portal.
    assert rendered_length('<a href="x">&</a>') > len('<a href="x">&</a>')


def test_rendered_length_can_exceed_the_cap_while_plain_length_does_not() -> None:
    message = "\n\n".join(["x" * 40] * 90)
    assert len(message) < COMMENT_MAX_CHARS < rendered_length(message)


def test_rendered_length_leaves_a_body_at_the_cap_within_it() -> None:
    # The boundary is inclusive: plain text that renders to exactly the cap
    # still posts.
    assert rendered_length("x" * COMMENT_MAX_CHARS) == COMMENT_MAX_CHARS


def test_post_comment_sends_the_portal_shape() -> None:
    seen: list[dict[str, Any]] = []
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        action = json.loads(urllib.parse.parse_qs(request.content.decode())["message"][0])[
            "actions"
        ][0]
        seen.append(action)
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "actions": [
                        {
                            "id": "1;a",
                            "state": "SUCCESS",
                            "returnValue": [
                                {
                                    "createdDate": "2026-08-20T01:00:00.000Z",
                                    "name": "Ada Advertiser",
                                    "isAdvertiser": True,
                                    "textbody": "<div>answer</div>",
                                }
                            ],
                        }
                    ]
                }
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    session = AuraSession.bootstrap(client, "/s/activity")
    comments = post_comment(session, "500KW1", "answer")
    assert seen[0]["descriptor"] == "apex://AC_CaseDetailController/ACTION$saveCaseComment"
    # the portal's comment box submits attachment removals with the text
    assert seen[0]["params"] == {"comment": "answer", "caseID": "500KW1", "filesToDelete": ""}
    assert comments[0].body == "answer" and comments[0].from_advertiser


def test_close_case_returns_the_new_status() -> None:
    page = make_page(authenticated=True)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        body = json.loads(urllib.parse.parse_qs(request.content.decode())["message"][0])
        seen.append(body["actions"][0])
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "actions": [
                        {"id": "1;a", "state": "SUCCESS", "returnValue": {"Status": "Closed"}}
                    ]
                }
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    session = AuraSession.bootstrap(client, "/s/activity")
    assert close_case(session, "500KW1") == "Closed"
    assert seen[0]["descriptor"] == "apex://AC_CaseDetailController/ACTION$closeCaseSt"
    assert seen[0]["params"] == {"caseID": "500KW1"}
