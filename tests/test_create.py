from __future__ import annotations

import json

import httpx
import pytest

from walmart_support.aura import AuraError, AuraSession
from walmart_support.create import (
    CaseDraft,
    Identity,
    build_payload,
    build_submission,
    fetch_category_tree,
    prepare,
    resolve_categories,
)
from walmart_support.portals import PORTALS, SAMSCLUB, WALMART, Portal, PortalError

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

# Sam's Club populates its advertiser picker from this action rather than
# carrying the account on the contact record.
ADVERTISERS = [{"Id": "0018A00001EXAMPLE", "Name": "Example Agency"}]

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
            },
            {
                "Id": "L2ONB",
                "Front_End_Name__c": "L2_Onboard New API Advertiser_L1_API",
                "Case_Category__c": "L2_Onboard New API Advertiser_L1_API",
                "Case_Category_L2__r": {
                    "Front_End_Name__c": "Onboard New API Advertiser",
                    "Case_Category__c": "Onboard New API Advertiser",
                },
            },
        ],
        "L1BILL": [
            {
                "Id": "L2DISP",
                "Front_End_Name__c": "L2_Dispute_L1_Billing",
                "Case_Category__c": "L2_Dispute_L1_Billing",
                "Case_Category_L2__r": {
                    "Front_End_Name__c": "Dispute",
                    "Case_Category__c": "Dispute",
                },
            }
        ],
    },
}


def _samsclub_tree() -> dict[str, object]:
    """The same tree as Sam's Club serves it.

    Its level-1 label is a bare ``API`` rather than Walmart's ``API Support``,
    which is what that portal's own form branches on, and no category there
    declares ``Support_Form__c`` records.
    """
    tree = json.loads(json.dumps(TREE))
    tree["Level1Categories"][0]["Case_Category_L1__r"]["Front_End_Name__c"] = "API"
    for kids in tree["Level2Categories"].values():
        for kid in kids:
            kid.pop("Support_Forms__r", None)
    return tree


def _selection(portal: Portal, *, category: str = "API", issue: str = "Endpoint"):
    tree = _samsclub_tree() if portal is SAMSCLUB else None
    session = _session(tree=tree)
    return session, resolve_categories(
        session,
        category=category,
        issue=issue,
        ad_unit=portal.ad_unit,
        partner_channel="p",
    )


def _session(*, tree: dict[str, object] | None = None, auth: dict[str, object] | None = None):
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        hint = str(request.url)
        if "getAuthenticationStatus" in hint:
            value: object = auth if auth is not None else AUTH
        elif "getAdvertiserInfo" in hint:
            value = ADVERTISERS
        elif "getCaseCategoriesNew" in hint:
            value = json.dumps(tree if tree is not None else TREE)
        else:
            value = {"caseNumber": "10000005"}
        return httpx.Response(
            200,
            text=json.dumps({"actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": value}]}),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test")
    return AuraSession.bootstrap(client, "/s/contact")


def test_platform_aliases_cover_sponsored_search() -> None:
    # The portal collapses Sponsored Search onto Sponsored Products.
    assert WALMART.resolve_ad_unit("sponsored-search") == "Sponsored Products"
    assert WALMART.resolve_ad_unit("Sponsored Search") == "Sponsored Products"
    assert WALMART.resolve_ad_unit("search") == "Sponsored Products"
    assert WALMART.resolve_ad_unit("display") == "Display"
    # internal name accepted verbatim
    assert WALMART.resolve_ad_unit("Display") == "Display"
    # and no platform at all falls back to the portal's own default
    assert WALMART.resolve_ad_unit(None) == "Display"


def test_unknown_platform_lists_the_valid_ones() -> None:
    with pytest.raises(PortalError, match="unknown platform"):
        WALMART.resolve_ad_unit("carousel")


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


def test_empty_tree_names_the_ad_unit_and_channel_it_tried() -> None:
    with pytest.raises(AuraError, match="SponsoredVideos.*channel 'p'"):
        resolve_categories(
            _session(tree={"Level1Categories": [], "Level2Categories": {}}),
            category="API",
            issue="x",
            ad_unit="SponsoredVideos",
            partner_channel="p",
        )


def test_payload_declares_every_parameter() -> None:
    draft = CaseDraft(subject="s", description="d", advertisers="1, 2")
    submission = prepare(_session(), draft, portal=WALMART)
    payload = submission.params
    assert submission.action == "openCase"
    # Apex binds by name, so a missing parameter is not the same as a blank one.
    assert len(payload) == 31
    assert tuple(payload) == WALMART.open_case_params
    assert payload["problemDomain"] == "API Support"
    assert payload["backendDomain"] == "API-AdCases"
    assert payload["subDomain"] == "Endpoint-specific problem"
    assert payload["lastLevelCategory"] == "L2EP"
    assert payload["IsFeatureRequest"] is False
    assert payload["documentId"] == []


def test_additional_fields_only_carry_declared_ones() -> None:
    draft = CaseDraft(subject="s", description="d", advertisers="1, 2")
    payload = prepare(_session(), draft, portal=WALMART).params
    extra = json.loads(payload["additionalFieldsString"])
    # a JSON list of title/value pairs: Apex deserializes it into a List, and
    # rejects any other key by name
    assert isinstance(extra, list)
    by_title = {item["title"]: item["value"] for item in extra}
    assert by_title["Advertisers Affected"] == "1, 2"
    assert by_title["Description"] == "d"
    # never sent: it collided with "Advertisers Affected" on the portal's side
    assert "Advertiser Account Name" not in by_title


def test_a_category_declaring_no_forms_drops_the_advertisers() -> None:
    # Sam's Club declares no Support_Form__c records on any category, so there
    # is nowhere to put the advertiser ids and they never reach the wire. The
    # filed case came back with Advertisers Affected set from the account name
    # instead, so the CLI warns rather than dropping them silently.
    bare = json.loads(json.dumps(TREE))
    del bare["Level2Categories"]["L1API"][0]["Support_Forms__r"]
    submission = prepare(
        _session(tree=bare),
        CaseDraft(subject="s", description="d", advertisers="12345, 67890"),
        portal=WALMART,
    )
    assert json.loads(submission.params["additionalFieldsString"]) == []
    # which is what the CLI's warning is keyed on
    assert submission.carries_advertisers is False


def test_partnership_fields_are_left_empty() -> None:
    payload = prepare(_session(), CaseDraft(subject="s", description="d"), portal=WALMART).params
    # Partnership__c is a lookup to a Partnership record, not an Account; an
    # account id there fails the insert with FIELD_INTEGRITY_EXCEPTION
    assert payload["partnershipType"] == ""
    assert payload["partnershipId"] == ""
    assert payload["selectedAccountId"] == "0014M00000EXAMPLE"


def test_search_platform_flows_through_to_the_payload() -> None:
    draft = CaseDraft(
        subject="s", description="d", ad_unit=WALMART.resolve_ad_unit("sponsored-search")
    )
    payload = build_payload(
        draft,
        Identity.fetch(_session()),
        resolve_categories(
            _session(), category="API", issue="Endpoint", ad_unit="Display", partner_channel="p"
        ),
        portal=WALMART,
    )
    assert payload["adUnit"] == "Sponsored Products"
    assert payload["product"] == "Sponsored Products"


def test_identity_falls_back_to_the_advertiser_lookup() -> None:
    # Sam's Club leaves AccountId off the contact record and names the
    # advertiser through getAdvertiserInfo instead.
    session = _session(auth={"userAuthenticated": True, "userType": "Advertiser-Ad_Agency"})
    identity = Identity.fetch(session)
    assert identity.account_id == "0018A00001EXAMPLE"
    assert identity.account_name == "Example Agency"


def test_every_portal_declares_parameters_this_build_can_fill() -> None:
    # The profile lists what each org's openCase declares and the builder holds
    # the values; a parameter in one and not the other would go out missing,
    # which Apex does not treat as blank.
    draft = CaseDraft(subject="s", description="d")
    identity = Identity.fetch(_session())
    _, selection = _selection(WALMART)
    for portal in PORTALS.values():
        payload = build_payload(draft, identity, selection, portal=portal)
        assert tuple(payload) == portal.open_case_params, portal.key


def test_samsclub_files_api_cases_the_way_its_own_form_does() -> None:
    # Its AC_OpenCase never calls openCase under the API category: openCase
    # there escapes subject and problem twice and declares no
    # additionalFieldsString, so this is the only path that stores the text as
    # written and the only one the advertiser ids can ride.
    draft = CaseDraft(subject='rejects "App"', description="body", advertisers="12345, 67890")
    session, selection = _selection(SAMSCLUB)
    submission = build_submission(draft, Identity.fetch(session), selection, portal=SAMSCLUB)

    assert submission.action == "saveApiCase"
    assert set(submission.params) == {"advWrapper"}
    wrapper = submission.fields
    assert wrapper["subject"] == 'rejects "App"'
    assert wrapper["problem"] == "body"
    assert wrapper["advertiserAffected"] == "12345, 67890"
    assert submission.carries_advertisers is True
    # The form reads both of each pair off one field of the category record.
    assert wrapper["problemDomain"] == wrapper["backendDomain"] == "API-AdCases"
    assert wrapper["subDomain"] == wrapper["backendSubDomain"] == "Endpoint-specific problem"
    assert wrapper["lastLevelCategory"] == "L2EP"
    assert wrapper["supplierChannel"] == "API"
    assert wrapper["isOnboardingCase"] is False


def test_the_onboarding_issue_carries_the_flag_its_form_sets() -> None:
    session, selection = _selection(SAMSCLUB, issue="Onboard New API Advertiser")
    submission = build_submission(
        CaseDraft(subject="s", description="d"), Identity.fetch(session), selection, portal=SAMSCLUB
    )
    assert submission.fields["isOnboardingCase"] is True


def test_samsclub_sends_only_the_openCase_parameters_it_declares() -> None:
    # Every other category still files through openCase there, but through the
    # 19 parameters that org declares — Aura drops the rest in silence.
    session, selection = _selection(SAMSCLUB, category="Billing", issue="Dispute")
    submission = build_submission(
        CaseDraft(subject="s", description="d", advertisers="1"),
        Identity.fetch(session),
        selection,
        portal=SAMSCLUB,
    )

    assert submission.action == "openCase"
    assert tuple(submission.params) == SAMSCLUB.open_case_params
    assert "additionalFieldsString" not in submission.params
    assert "selectedAccountId" not in submission.params
    # spelled as this org declares it, against Walmart's ArticleNotHelped
    assert "articleNotHelped" in submission.params
    assert submission.params["campaignName"] == ""
    assert submission.params["supplierChannel"] == "API"
    # nowhere to put them here, which is what the CLI warns about
    assert submission.carries_advertisers is False


def test_walmart_has_no_api_case_action_of_its_own() -> None:
    # It declares no saveApiCase at all, so every category goes through openCase.
    draft = CaseDraft(subject="s", description="d")
    session, selection = _selection(WALMART)
    submission = build_submission(draft, Identity.fetch(session), selection, portal=WALMART)
    assert WALMART.api_case_action == ""
    assert submission.action == "openCase"
