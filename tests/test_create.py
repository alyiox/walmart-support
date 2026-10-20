from __future__ import annotations

import json

import httpx
import pytest

from walmart_support.aura import AuraError, AuraSession
from walmart_support.create import (
    CaseDraft,
    FormError,
    Identity,
    Submission,
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


# Walmart's tree as getCategoryTree serves it: one label per node, and an
# issue names the form config its fields come from.
WALMART_TREE = [
    {
        "label": "API",
        "id": "L1API",
        "configId": None,
        "children": [
            {"label": "Endpoint-Specific", "id": "L2EP", "configId": "CFGEP", "children": []},
            {"label": "Throttling", "id": "L2THR", "configId": "CFGTHR", "children": []},
        ],
    },
    {
        "label": "Billing",
        "id": "L1BILL",
        "configId": None,
        "children": [{"label": "Disputes", "id": "L2DISP", "configId": "CFGDISP"}],
    },
]


def _field(label: str, **extra: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": f"F-{label}",
        "label": label,
        "caseField": None,
        "fieldType": "Text",
        "required": "Never",
        "options": [],
        "customUi": False,
        "readOnly": False,
        "fieldGroup": None,
        "dependsOnFieldId": None,
        "dependsOnValue": None,
        "validationPattern": None,
        "value": None,
    }
    return record | extra


# Mirrors the live Endpoint-Specific form: the advertiser id is drawn by the
# form itself, the description is the case body, and the file input binds no
# Case field.
FORM = {
    "config": {"Form_Template__c": "TPL1", "Disable_Submission__c": False},
    "fields": [
        _field(
            "Walmart Ad Center Advertiser ID",
            caseField="WAC_Advertiser_Id__c",
            customUi=True,
            fieldGroup="Advertiser",
        ),
        _field(
            "SP Endpoint",
            caseField="API_Endpoint__c",
            fieldType="Picklist",
            required="Always",
            options=[{"label": "/api/v1/stats", "value": "/api/v1/stats"}],
        ),
        _field(
            "Request / Response Details",
            caseField="Request_Response_Details__c",
            fieldType="TextArea",
            required="Always",
        ),
        _field("Description", caseField="Description", fieldType="TextArea", required="Always"),
        _field("Screenshot/Document", fieldType="File"),
    ],
}

FILLED = {"SP Endpoint": "/api/v1/stats", "Request / Response Details": "curl ..."}


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
        portal=portal,
    )


def _session(
    *,
    tree: object | None = None,
    auth: dict[str, object] | None = None,
    form: dict[str, object] | None = None,
    sent: list[dict[str, object]] | None = None,
):
    page = make_page(authenticated=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aura" not in request.url.path:
            return httpx.Response(200, text=page)
        hint = str(request.url)
        if sent is not None:
            message = json.loads(dict(httpx.QueryParams(request.content.decode()))["message"])
            sent.append(message["actions"][0])
        if "getAuthenticationStatus" in hint:
            value: object = auth if auth is not None else AUTH
        elif "getAdvertiserInfo" in hint:
            value = ADVERTISERS
        elif "getCaseCategoriesNew" in hint:
            value = json.dumps(tree if tree is not None else TREE)
        elif "getCategoryTree" in hint:
            value = json.dumps(tree if tree is not None else WALMART_TREE)
        elif "getFormForConfig" in hint:
            value = json.dumps(form if form is not None else FORM)
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
    assert WALMART.resolve_ad_unit("display") == "Onsite Display"
    # internal name accepted verbatim
    assert WALMART.resolve_ad_unit("Onsite Display") == "Onsite Display"
    # and no platform at all falls back to the portal's own default
    assert WALMART.resolve_ad_unit(None) == "Onsite Display"


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
    tree = fetch_category_tree(
        _session(), ad_unit="Sponsored Products", partner_channel="p", portal=SAMSCLUB
    )
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
            ad_unit="Sponsored Products",
            partner_channel="p",
            portal=SAMSCLUB,
        )
        assert chosen.level1.backend_name == "API-AdCases"


def test_unknown_category_reports_the_available_ones() -> None:
    with pytest.raises(AuraError, match="Billing"):
        resolve_categories(
            _session(),
            category="Nonsense",
            issue="x",
            ad_unit="Onsite Display",
            partner_channel="p",
            portal=WALMART,
        )


def test_unknown_issue_reports_the_available_ones() -> None:
    with pytest.raises(AuraError, match="Endpoint-Specific, Throttling"):
        resolve_categories(
            _session(),
            category="API",
            issue="Nonsense",
            ad_unit="Onsite Display",
            partner_channel="p",
            portal=WALMART,
        )


def test_empty_tree_names_the_ad_unit_and_channel_it_tried() -> None:
    with pytest.raises(AuraError, match="SponsoredVideos.*channel 'p'"):
        resolve_categories(
            _session(tree=[]),
            category="API",
            issue="x",
            ad_unit="SponsoredVideos",
            partner_channel="p",
            portal=WALMART,
        )


def test_walmart_tree_names_each_issues_form() -> None:
    sent: list[dict[str, object]] = []
    tree = fetch_category_tree(
        _session(sent=sent), ad_unit="ShopBuilder", partner_channel="p", portal=WALMART
    )
    l1, kids = tree[0]
    assert (l1.label, l1.backend_name) == ("API", "API")
    assert [(k.label, k.config_id) for k in kids] == [
        ("Endpoint-Specific", "CFGEP"),
        ("Throttling", "CFGTHR"),
    ]
    # the category selector asks for some ad units under another name
    asked = next(a for a in sent if "getCategoryTree" in str(a["descriptor"]))
    assert asked["params"] == {"platform": "BrandShops", "userType": "p", "auth": "Authenticated"}


def test_payload_declares_every_parameter() -> None:
    draft = CaseDraft(subject="s", description="d", advertisers="111", fields=FILLED)
    submission = prepare(_session(), draft, portal=WALMART)
    payload = submission.params
    assert submission.action == "openCase"
    # Apex binds by name, so a missing parameter is not the same as a blank one.
    assert len(payload) == 19
    assert tuple(payload) == WALMART.open_case_params
    assert payload["problemDomain"] == "API"
    assert payload["subDomain"] == "Endpoint-Specific"
    assert payload["lastLevelCategory"] == "L2EP"
    assert payload["selectedAccountId"] == "0014M00000EXAMPLE"
    assert payload["advertiserId"] == "111"
    assert payload["formTemplateId"] == "TPL1"
    assert payload["documentId"] == []
    assert submission.problems == ()


def test_form_fields_travel_whole_as_the_form_sends_them() -> None:
    draft = CaseDraft(subject="s", description="body", advertisers="111", fields=FILLED)
    payload = prepare(_session(), draft, portal=WALMART).params
    extra = json.loads(payload["additionalFieldsString"])
    # a JSON list carrying every field, bound to its Case field by fieldName
    assert [e["title"] for e in extra] == [f["label"] for f in FORM["fields"]]
    by_title = {e["title"]: e for e in extra}
    assert by_title["SP Endpoint"]["value"] == "/api/v1/stats"
    assert by_title["SP Endpoint"]["fieldName"] == "API_Endpoint__c"
    assert by_title["SP Endpoint"]["fieldType"] == "SinglePicklist"
    assert by_title["SP Endpoint"]["options"] == ["/api/v1/stats"]
    # filled the way the form fills them, and skipped as it skips them
    assert by_title["Walmart Ad Center Advertiser ID"]["value"] == "111"
    assert by_title["Walmart Ad Center Advertiser ID"]["skip"] is True
    assert by_title["Description"]["value"] == "body"
    assert by_title["Description"]["skip"] is True
    assert by_title["Screenshot/Document"]["fieldType"] == "File Upload"
    assert by_title["Screenshot/Document"]["fieldName"] is None


def test_missing_required_fields_are_problems_not_errors() -> None:
    # The portal inserts a case with these blank, so the dry run names them
    # and the CLI refuses --submit on them.
    submission = prepare(_session(), CaseDraft(subject="s", description="d"), portal=WALMART)
    assert submission.problems == (
        "'SP Endpoint' is required; one of: /api/v1/stats",
        "'Request / Response Details' is required",
    )


def test_a_picklist_value_must_be_one_the_form_offers() -> None:
    # labels match regardless of case, as a person would type them
    fields = {"Request / Response Details": "x", "sp endpoint": "/nope"}
    submission = prepare(
        _session(), CaseDraft(subject="s", description="d", fields=fields), portal=WALMART
    )
    assert submission.problems == ("'SP Endpoint' must be one of: /api/v1/stats",)


def test_an_unknown_field_label_lists_the_offered_ones() -> None:
    draft = CaseDraft(subject="s", description="d", fields={"Endpoint": "/x"})
    with pytest.raises(FormError, match="'SP Endpoint', 'Request / Response Details'"):
        prepare(_session(), draft, portal=WALMART)


def test_fields_filled_another_way_are_refused() -> None:
    for label, flag in (
        ("Description", "--description-file"),
        ("Walmart Ad Center Advertiser ID", "--advertisers"),
    ):
        draft = CaseDraft(subject="s", description="d", fields={label: "x"})
        with pytest.raises(FormError, match=flag):
            prepare(_session(), draft, portal=WALMART)


def test_a_required_advertiser_id_needs_advertisers() -> None:
    form = json.loads(json.dumps(FORM))
    form["fields"][0]["required"] = "Always"
    submission = prepare(
        _session(form=form), CaseDraft(subject="s", description="d", fields=FILLED), portal=WALMART
    )
    assert submission.problems == (
        "'Walmart Ad Center Advertiser ID' is required: pass --advertisers",
    )


def test_a_field_hidden_by_its_dependency_goes_out_blank() -> None:
    form = json.loads(json.dumps(FORM))
    form["fields"].append(
        _field(
            "Rate Limit",
            caseField="Rate__c",
            required="Always",
            dependsOnFieldId="F-SP Endpoint",
            dependsOnValue="/api/v1/other",
        )
    )
    draft = CaseDraft(subject="s", description="d", fields=FILLED | {"Rate Limit": "5/s"})
    submission = prepare(_session(form=form), draft, portal=WALMART)
    extra = json.loads(submission.params["additionalFieldsString"])
    assert {e["title"]: e["value"] for e in extra}["Rate Limit"] == ""
    # and hidden, it is not required either
    assert submission.problems == ()


def test_the_dry_run_lists_form_fields_by_label() -> None:
    draft = CaseDraft(subject="s", description="d", advertisers="111", fields=FILLED)
    fields = prepare(_session(), draft, portal=WALMART).fields
    assert "additionalFieldsString" not in fields
    assert fields["form: SP Endpoint"] == "/api/v1/stats"
    # the description field repeats problem, so it is left out
    assert "form: Description" not in fields


def test_advertisers_ride_their_own_parameter() -> None:
    submission = Submission("openCase", {"advertiserId": "111", "additionalFieldsString": "[]"})
    assert submission.carries_advertisers is True
    assert Submission("openCase", {"advertiserId": ""}).carries_advertisers is False


def test_the_advertiser_name_is_typed_and_the_id_follows_advertisers() -> None:
    # An API partner has no advertiser records, so the form's picker falls
    # back to two inputs; a few forms also declare the id outside its group.
    form = json.loads(json.dumps(FORM))
    form["fields"][0] |= {"customUi": False, "fieldGroup": None, "required": "Always"}
    form["fields"].append(
        _field(
            "Walmart Advertiser Name",
            caseField="WAC_Advertiser_Name__c",
            customUi=True,
            fieldGroup="Advertiser",
            required="Always",
        )
    )
    bare = prepare(
        _session(form=form), CaseDraft(subject="s", description="d", fields=FILLED), portal=WALMART
    )
    assert bare.problems == (
        "'Walmart Ad Center Advertiser ID' is required: pass --advertisers",
        "'Walmart Advertiser Name' is required",
    )

    draft = CaseDraft(
        subject="s",
        description="d",
        advertisers="111",
        fields=FILLED | {"Walmart Advertiser Name": "Example Brand"},
    )
    filled = prepare(_session(form=form), draft, portal=WALMART)
    assert filled.problems == ()
    assert filled.fields["form: Walmart Ad Center Advertiser ID"] == "111"
    assert filled.fields["form: Walmart Advertiser Name"] == "Example Brand"


def test_a_disabled_issue_is_a_problem() -> None:
    form = json.loads(json.dumps(FORM))
    form["config"]["Disable_Submission__c"] = True
    submission = prepare(
        _session(form=form), CaseDraft(subject="s", description="d"), portal=WALMART
    )
    assert submission.problems == ("the portal does not accept new cases under this issue",)


def test_samsclub_refuses_form_fields() -> None:
    draft = CaseDraft(subject="s", description="d", fields={"x": "y"})
    with pytest.raises(FormError, match="no form fields"):
        prepare(_session(tree=_samsclub_tree()), draft, portal=SAMSCLUB)


def test_search_platform_flows_through_to_the_payload() -> None:
    draft = CaseDraft(
        subject="s", description="d", ad_unit=WALMART.resolve_ad_unit("sponsored-search")
    )
    payload = prepare(_session(), draft, portal=WALMART).params
    assert payload["adUnit"] == "Sponsored Products"


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
