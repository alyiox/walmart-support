"""Case creation.

The portal's wizard is data-driven: ``getCaseCategoriesNew`` returns
``Case_Category__c`` records for each level, and the level a user lands on
carries the form fields to collect. A filing action then takes flat parameters
that restate that selection in both display and backend form.

Category identifiers are looked up by name at call time rather than hardcoded,
so the mapping follows the portal if a category is re-labelled or re-ided.

**The parameter mapping below is inferred**, from each component's published
method signature plus the category records — not from an observed submit. Only
the identity and category fields are confirmed. That is why the CLI refuses to
submit without an explicit flag: a mis-mapped category would file a real but
misrouted case with support.

Which action files the case is the portal's business, so a draft becomes a
:class:`Submission` — the method name plus its parameters — rather than one
payload shape:

* Walmart: ``openCase`` with the 31 parameters it declares.
* Sam's Club, under the API category: ``saveApiCase`` with a single wrapper
  object, which is what that portal's own form submits there. Nothing else
  reaches those cases correctly — ``openCase`` on that org HTML-escapes
  ``subject`` and ``problem`` twice before the insert, and declares no
  ``additionalFieldsString``, so the advertiser ids had nowhere to go. The
  wrapper takes them as ``advertiserAffected``.
* Sam's Club, every other category: ``openCase`` with the 19 parameters *it*
  declares. Still doubly escaped, which is the portal's own bug; the read side
  undoes it.

Identity diverges too: on Sam's the account arrives through
``getAdvertiserInfo`` rather than the contact record, and no category there
declares ``Support_Forms__r``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .aura import AuraError, AuraSession
from .portals import API_CATEGORY, WALMART, Portal

_COMPONENT = "AC_ContactSupport"
CONTACT_PAGE = "/s/contact?language=en_US"

OPEN_CASE = "openCase"

DEFAULT_GEO = "US"

# The one API issue whose form collects company, billing and admin details this
# CLI does not ask for. Sam's marks its wrapper with a flag of its own, so the
# value is mirrored rather than assumed false.
ONBOARDING_ISSUE = "Onboard New API Advertiser"


def _advertiser_account(session: AuraSession) -> tuple[str, str] | None:
    """Name the advertiser through the portal's own advertiser lookup."""
    raw = session.apex(_COMPONENT, "getAdvertiserInfo", {})
    for record in raw if isinstance(raw, list) else []:
        if isinstance(record, dict) and record.get("Id"):
            return str(record["Id"]), str(record.get("Name") or "")
    return None


@dataclass(frozen=True)
class Identity:
    """Who the case is filed as, per the portal's own auth action."""

    user_type: str
    account_id: str
    account_name: str
    contact_name: str
    contact_email: str

    @classmethod
    def fetch(cls, session: AuraSession) -> Identity:
        raw = session.apex(_COMPONENT, "getAuthenticationStatus", {})
        if not isinstance(raw, dict) or not raw.get("userAuthenticated"):
            raise AuraError("getAuthenticationStatus", "portal reports an unauthenticated user")
        contact = raw.get("userContact") or {}
        account = contact.get("Account") or {}
        account_id = str(contact.get("AccountId") or account.get("Id") or "")
        account_name = str(account.get("Name") or "")
        # Sam's Club leaves the account off the contact record entirely and
        # populates its advertiser picker from a separate action, so fall back
        # to that rather than filing with an empty selectedAccountId.
        if not account_id and (found := _advertiser_account(session)):
            account_id, account_name = found
        return cls(
            user_type=str(raw.get("userType") or ""),
            account_id=account_id,
            account_name=account_name,
            contact_name=str(contact.get("Name") or raw.get("userName") or ""),
            contact_email=str(contact.get("Email") or ""),
        )


@dataclass(frozen=True)
class Category:
    """One level of the wizard.

    Each wizard record links to a plain "Category" record that carries the two
    values that actually matter: the label the UI dropdown shows, and the value
    written to the Case. For API support those are "API Support" and
    "API-AdCases" — the latter is what a filed case reports as its category.
    """

    record_id: str
    label: str
    backend_name: str
    wizard_name: str
    form_fields: list[str] = field(default_factory=list)

    def matches(self, wanted: str) -> bool:
        needle = wanted.casefold()
        return needle in self.label.casefold() or needle in self.wizard_name.casefold()


@dataclass(frozen=True)
class CategorySelection:
    level1: Category
    level2: Category


def _category(record: dict[str, Any], level: int) -> Category:
    forms = (record.get("Support_Forms__r") or {}).get("records") or []
    wizard_name = str(record.get("Front_End_Name__c") or record.get("Case_Category__c") or "")
    linked = record.get(f"Case_Category_L{level}__r")
    linked = linked if isinstance(linked, dict) else {}
    return Category(
        record_id=str(record.get("Id") or ""),
        label=str(linked.get("Front_End_Name__c") or wizard_name),
        backend_name=str(linked.get("Case_Category__c") or record.get("Case_Category__c") or ""),
        wizard_name=wizard_name,
        form_fields=[str(f.get("Name")) for f in forms if f.get("Name")],
    )


def fetch_category_tree(
    session: AuraSession, *, ad_unit: str, partner_channel: str
) -> list[tuple[Category, list[Category]]]:
    """Return the support categories as the UI dropdown presents them.

    ``partner_channel`` is the caller's own user type; without it the portal
    answers with an empty tree rather than an error.
    """
    raw = session.apex(
        _COMPONENT,
        "getCaseCategoriesNew",
        {"partnerChannel": partner_channel, "authenticated": True, "adUnit": ad_unit},
    )
    tree = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(tree, dict):
        raise AuraError("getCaseCategoriesNew", "unexpected category payload")

    level2 = tree.get("Level2Categories") or {}
    out: list[tuple[Category, list[Category]]] = []
    for record in tree.get("Level1Categories") or []:
        if not isinstance(record, dict):
            continue
        children = [
            _category(child, 2)
            for child in level2.get(str(record.get("Id"))) or []
            if isinstance(child, dict)
        ]
        out.append((_category(record, 1), children))
    return out


def resolve_categories(
    session: AuraSession, *, category: str, issue: str, ad_unit: str, partner_channel: str
) -> CategorySelection:
    """Look up the L1/L2 category pair the portal would have selected."""
    tree = fetch_category_tree(session, ad_unit=ad_unit, partner_channel=partner_channel)
    if not tree:
        raise AuraError(
            "getCaseCategoriesNew",
            f"the portal offers no categories for ad unit {ad_unit!r} on channel "
            f"{partner_channel!r}; check the ad unit against 'categories list'",
        )

    match = next(((l1, kids) for l1, kids in tree if l1.matches(category)), None)
    if match is None:
        names = ", ".join(sorted(l1.label for l1, _ in tree))
        raise AuraError("getCaseCategoriesNew", f"no category matching {category!r}; have: {names}")

    parent, children = match
    child = next((c for c in children if c.matches(issue)), None)
    if child is None:
        names = ", ".join(sorted(c.label for c in children)) or "(none)"
        raise AuraError("getCaseCategoriesNew", f"no issue matching {issue!r}; have: {names}")

    return CategorySelection(level1=parent, level2=child)


@dataclass(frozen=True)
class CaseDraft:
    subject: str
    description: str
    advertisers: str = ""
    category: str = "API"
    issue: str = "Endpoint-specific problem"
    # Already resolved by Portal.resolve_ad_unit: which names are accepted is
    # the portal's business, not the draft's.
    ad_unit: str = WALMART.ad_unit
    geo: str = DEFAULT_GEO


def _additional_fields(draft: CaseDraft, identity: Identity, selection: CategorySelection) -> str:
    """The ``additionalFieldsString`` value for a draft.

    Only fields this category actually declares are sent. The shape is a JSON
    list of AdditionalFieldWrapper objects keyed title/value, mirroring the
    ``Support_Form__c`` records' own ``Title__c``: Apex deserializes the string
    into a List (an object gives "Expected '[' at the beginning of List/Set")
    and rejects any other key by name.
    """
    # "Advertiser Account Name" is deliberately absent: it collides with
    # "Advertisers Affected" on the portal's side, which stored the account name
    # under the advertisers field and dropped the ids. The account already
    # reaches the case through selectedAccountId and guestAccountName.
    values = {
        "Name": identity.contact_name,
        "Email": identity.contact_email,
        "Advertisers Affected": draft.advertisers,
        "Description": draft.description,
    }
    declared = set(selection.level2.form_fields)
    return json.dumps(
        [
            {"title": label, "value": value}
            for label, value in values.items()
            if label in declared and value
        ]
    )


def _open_case_values(
    draft: CaseDraft, identity: Identity, selection: CategorySelection, *, portal: Portal
) -> dict[str, Any]:
    """Every value ``openCase`` can take, keyed by parameter name casefolded.

    The two orgs declare overlapping but different parameter lists and spell one
    of them differently, so the values live in one table and the profile's own
    list decides which go out. Lookup is casefolded because Apex binds names
    case-insensitively and only the org's own spelling belongs on the wire.
    """
    values: dict[str, Any] = {
        # Partnership__c is a lookup to a Partnership record and applies to
        # supplier/seller channels (see getPartnershipLabels), not to API
        # partners; an account id here fails with FIELD_INTEGRITY_EXCEPTION.
        "partnershipType": "",
        "partnershipId": "",
        "selectedAccountId": identity.account_id,
        "guestAccountName": identity.account_name,
        "guestName": identity.contact_name,
        "guestEmail": identity.contact_email,
        "guestSupplierNumber": "",
        "externalReference": "",
        "subject": draft.subject,
        "problem": draft.description,
        "problemDomain": selection.level1.label,
        "subDomain": selection.level2.label,
        "subDomain2": "",
        "subDomain3": "",
        "backendDomain": selection.level1.backend_name,
        "backendSubDomain": selection.level2.backend_name,
        "backendSub2Domain": "",
        "backendSub3Domain": "",
        "additionalFieldsString": _additional_fields(draft, identity, selection),
        "ArticleNotHelped": "",
        "caseType": "",
        "product": draft.ad_unit,
        "serviceNowKA": "",
        "geo": draft.geo,
        "UserType": identity.user_type,
        "IsFeatureRequest": False,
        "currentExperience": "",
        "adUnit": draft.ad_unit,
        "lastLevelCategory": selection.level2.record_id,
        "documentId": [],
        "billingDisputeJson": "",
        "campaignName": "",
        "supplierChannel": portal.supplier_channel,
    }
    return {name.casefold(): value for name, value in values.items()}


def build_payload(
    draft: CaseDraft, identity: Identity, selection: CategorySelection, *, portal: Portal
) -> dict[str, Any]:
    """Assemble ``openCase`` parameters for a draft, as ``portal`` declares them.

    Every parameter the org's method declares is sent, empty where it does not
    apply, because Apex binds by name and a missing one is not the same as a
    blank one. Nothing it does not declare is sent, because Aura would drop it
    in silence and the caller would never learn the value went nowhere.
    """
    values = _open_case_values(draft, identity, selection, portal=portal)
    return {name: values[name.casefold()] for name in portal.open_case_params}


def build_api_case(
    draft: CaseDraft, identity: Identity, selection: CategorySelection, *, portal: Portal
) -> dict[str, Any]:
    """Assemble the ``saveApiCase`` wrapper, mirroring the portal's own form.

    Field for field what ``AC_OpenCase.createCase`` sends on its API branch,
    including the ones only the onboarding form fills, because Apex
    deserializes the object into one wrapper class and the form always sends
    the whole shape.

    ``problemDomain`` and ``backendDomain`` carry the *same* value here, and so
    do ``subDomain`` and ``backendSubDomain``: the form reads both of each pair
    off one field of the selected category record, and that field holds the
    backend name.
    """
    return {
        "companyName": "",
        "associatedBrands": "",
        "billingContactJobTitle": "",
        "billingContact": "",
        "billingCompany": "",
        "vendorName": "",
        "agencyName": "",
        "subject": draft.subject,
        "problem": draft.description,
        "problemDomain": selection.level1.backend_name,
        "subDomain": selection.level2.backend_name,
        "subDomain2": "",
        "subDomain3": "",
        "backendDomain": selection.level1.backend_name,
        "backendSubDomain": selection.level2.backend_name,
        "backendSub2Domain": "",
        "backendSub3Domain": "",
        "lastLevelCategory": selection.level2.record_id,
        "supplierChannel": portal.supplier_channel,
        "isOnboardingCase": selection.level2.label.strip() == ONBOARDING_ISSUE,
        # The one place the advertiser ids are addressable on this org.
        "advertiserAffected": draft.advertisers,
        "guestName": identity.contact_name,
        "guestEmail": identity.contact_email,
        "emails": "",
    }


@dataclass(frozen=True)
class Submission:
    """One draft addressed to the action its portal files that case with."""

    action: str
    params: dict[str, Any]

    @property
    def fields(self) -> dict[str, Any]:
        """What a reviewer should read, with a wrapper unwrapped.

        ``saveApiCase`` takes its whole payload under one ``advWrapper`` key,
        which would print as a single unreadable line in the dry run.
        """
        wrapper = self.params.get("advWrapper")
        return dict(wrapper) if isinstance(wrapper, dict) else dict(self.params)

    @property
    def carries_advertisers(self) -> bool:
        """Whether the advertiser ids a draft named actually reach the portal.

        They ride a declared form field under ``openCase`` and a parameter of
        their own under ``saveApiCase``, so neither answer generalises.
        """
        fields = self.fields
        if "advertiserAffected" in fields:
            return bool(fields["advertiserAffected"])
        return "Advertisers Affected" in str(fields.get("additionalFieldsString", ""))


def build_submission(
    draft: CaseDraft, identity: Identity, selection: CategorySelection, *, portal: Portal
) -> Submission:
    """Choose the action ``portal`` files this draft with, and build its params.

    The level-1 label is what Sam's own form branches on, so it is what decides
    here too.
    """
    if portal.api_case_action and selection.level1.label.strip() == API_CATEGORY:
        wrapper = build_api_case(draft, identity, selection, portal=portal)
        return Submission(portal.api_case_action, {"advWrapper": wrapper})
    return Submission(OPEN_CASE, build_payload(draft, identity, selection, portal=portal))


def prepare(session: AuraSession, draft: CaseDraft, *, portal: Portal) -> Submission:
    """Resolve everything needed to file ``draft``, without filing it."""
    identity = Identity.fetch(session)
    selection = resolve_categories(
        session,
        category=draft.category,
        issue=draft.issue,
        ad_unit=draft.ad_unit,
        partner_channel=identity.user_type,
    )
    return build_submission(draft, identity, selection, portal=portal)


def submit(session: AuraSession, submission: Submission) -> dict[str, Any]:
    """File the case. Returns the portal's CaseWrapper.

    Both actions answer with the same wrapper shape, which is why the portal's
    own form reads ``caseNumber`` off either without checking which it called.
    """
    raw = session.apex(_COMPONENT, submission.action, submission.params)
    return raw if isinstance(raw, dict) else {"result": raw}
