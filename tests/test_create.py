from __future__ import annotations

import json

import httpx
import pytest

from mcp_walmart_support.aura import AuraError, AuraSession
from mcp_walmart_support.create import (
    CaseDraft,
    Identity,
    build_payload,
    fetch_category_tree,
    prepare,
    resolve_categories,
    resolve_platform,
)

from .conftest import make_page

AUTH = {
    "userAuthenticated": True,
    "userName": "Ada Advertiser",
    "userType": "Advertiser-WAP_Partner",
    "userContact": {
        "Name": "Ada Advertiser",
        "Email": "you@example.com",
        "AccountId": "0014M00000EXAMPLE",
        "Account": {"Id": "0014M00000EXAMPLE", "Name": "Example Advertiser"},
    },
}

# Mirrors the live shape: the wizard record's own name is internal, and the
# linked record carries the UI label plus the value written to the Case.
TREE = {
    "Level1Categories": [
        {
            "Id": "L1API",
            "Front_End_Name__c": "API",
            "Case_Category__c": "L1_API",
            "Case_Category_L1__r": {
                "Front_End_Name__c": "API Support",
                "Case_Category__c": "API-AdCases",
            },
        },
        {
            "Id": "L1BILL",
            "Front_End_Name__c": "Billing",
            "Case_Category__c": "L1_Billing",
            "Case_Category_L1__r": {
                "Front_End_Name__c": "Billing",
                "Case_Category__c": "Billing-AdCases",
            },
        },
    ],
    "Level2Categories": {
        "L1API": [
            {
                "Id": "L2EP",
                "Front_End_Name__c": "L2_Endpoint-specific problem_L1_API",
                "Case_Category__c": "L2_Endpoint-specific problem_L1_API",
                "Case_Category_L2__r": {
                    "Front_End_Name__c": "Endpoint-specific problem",
                    "Case_Category__c": "Endpoint-specific problem",
                },
                "Support_Forms__r": {
                    "records": [
                        {"Name": "Name"},
                        {"Name": "Email"},
                        {"Name": "Advertisers Affected"},
                        {"Name": "Description"},
                    ]
                },
            }
        ]
    },
}


def _session(*, tree: dict[str, object] | None = None, auth: dict[str, object] | None = None):
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        hint = str(request.url)
        if "getAuthenticationStatus" in hint:
            value: object = auth if auth is not None else AUTH
        elif "getCaseCategoriesNew" in hint:
            value = json.dumps(tree if tree is not None else TREE)
        else:
            value = {"caseNumber": "15999999"}
        return httpx.Response(
            200,
            text=json.dumps({"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": value}]}),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    return AuraSession.bootstrap(client, "/s/contact")


def test_platform_aliases_cover_sponsored_search() -> None:
    # The portal collapses Sponsored Search onto Sponsored Products.
    assert resolve_platform("sponsored-search") == "Sponsored Products"
    assert resolve_platform("Sponsored Search") == "Sponsored Products"
    assert resolve_platform("search") == "Sponsored Products"
    assert resolve_platform("display") == "Display"
    assert resolve_platform("Display") == "Display"  # internal name accepted verbatim


def test_unknown_platform_lists_the_valid_ones() -> None:
    with pytest.raises(AuraError, match="unknown platform"):
        resolve_platform("carousel")


def test_identity_from_auth_payload() -> None:
    identity = Identity.fetch(_session())
    assert identity.user_type == "Advertiser-WAP_Partner"
    assert identity.account_name == "Example Advertiser"
    assert identity.contact_email == "you@example.com"


def test_identity_rejects_unauthenticated() -> None:
    with pytest.raises(AuraError, match="unauthenticated"):
        Identity.fetch(_session(auth={"userAuthenticated": False}))


def test_tree_uses_the_linked_records_labels() -> None:
    tree = fetch_category_tree(_session(), ad_unit="Display", partner_channel="p")
    l1, kids = tree[0]
    # the UI dropdown label, not the wizard record's internal name
    assert l1.label == "API Support"
    assert l1.wizard_name == "API"
    assert l1.backend_name == "API-AdCases"
    assert kids[0].label == "Endpoint-specific problem"
    assert kids[0].form_fields == ["Name", "Email", "Advertisers Affected", "Description"]


def test_categories_match_by_ui_label_or_internal_name() -> None:
    session = _session()
    for name in ("API Support", "api", "API"):
        chosen = resolve_categories(
            session,
            category=name,
            issue="Endpoint-specific",
            ad_unit="Display",
            partner_channel="p",
        )
        assert chosen.level1.backend_name == "API-AdCases"


def test_unknown_category_reports_the_available_ones() -> None:
    with pytest.raises(AuraError, match="Billing"):
        resolve_categories(
            _session(), category="Nonsense", issue="x", ad_unit="Display", partner_channel="p"
        )


def test_unknown_issue_reports_the_available_ones() -> None:
    with pytest.raises(AuraError, match="Endpoint-specific problem"):
        resolve_categories(
            _session(), category="API", issue="Nonsense", ad_unit="Display", partner_channel="p"
        )


def test_empty_tree_explains_which_platforms_work() -> None:
    with pytest.raises(AuraError, match="Sponsored Products"):
        resolve_categories(
            _session(tree={"Level1Categories": [], "Level2Categories": {}}),
            category="API",
            issue="x",
            ad_unit="SponsoredVideos",
            partner_channel="p",
        )


def test_payload_declares_every_parameter() -> None:
    payload = prepare(_session(), CaseDraft(subject="s", description="d", advertisers="1, 2"))
    # Apex binds by name, so a missing parameter is not the same as a blank one.
    assert len(payload) == 31
    assert payload["problemDomain"] == "API Support"
    assert payload["backendDomain"] == "API-AdCases"
    assert payload["subDomain"] == "Endpoint-specific problem"
    assert payload["lastLevelCategory"] == "L2EP"
    assert payload["IsFeatureRequest"] is False
    assert payload["documentId"] == []


def test_additional_fields_only_carry_declared_ones() -> None:
    payload = prepare(_session(), CaseDraft(subject="s", description="d", advertisers="1, 2"))
    extra = json.loads(payload["additionalFieldsString"])
    # a JSON list of title/value pairs: Apex deserializes it into a List, and
    # rejects any other key by name
    assert isinstance(extra, list)
    by_title = {item["title"]: item["value"] for item in extra}
    assert by_title["Advertisers Affected"] == "1, 2"
    assert by_title["Description"] == "d"
    # never sent: it collided with "Advertisers Affected" on the portal's side
    assert "Advertiser Account Name" not in by_title


def test_partnership_fields_are_left_empty() -> None:
    payload = prepare(_session(), CaseDraft(subject="s", description="d"))
    # Partnership__c is a lookup to a Partnership record, not an Account; an
    # account id there fails the insert with FIELD_INTEGRITY_EXCEPTION
    assert payload["partnershipType"] == ""
    assert payload["partnershipId"] == ""
    assert payload["selectedAccountId"] == "0014M00000EXAMPLE"


def test_search_platform_flows_through_to_the_payload() -> None:
    draft = CaseDraft(subject="s", description="d", platform="sponsored-search")
    payload = build_payload(
        draft,
        Identity.fetch(_session()),
        resolve_categories(
            _session(), category="API", issue="Endpoint", ad_unit="Display", partner_channel="p"
        ),
    )
    assert payload["adUnit"] == "Sponsored Products"
    assert payload["product"] == "Sponsored Products"
