"""Case creation.

The portal's wizard is data-driven: ``getCaseCategoriesNew`` returns
``Case_Category__c`` records for each level, and the level a user lands on
carries the form fields to collect. ``openCase`` then takes 31 flat parameters
that restate that selection in both display and backend form.

Category identifiers are looked up by name at call time rather than hardcoded,
so the mapping follows the portal if Walmart re-labels or re-ids a category.

**The parameter mapping below is inferred**, from the component's published
method signature plus the category records — not from an observed submit. Only
the identity and category fields are confirmed. That is why the CLI refuses to
submit without an explicit flag: a mis-mapped category would file a real but
misrouted case with Walmart support.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .aura import AuraError, AuraSession

_COMPONENT = "AC_ContactSupport"
CONTACT_PAGE = "/s/contact?language=en_US"

DEFAULT_GEO = "US"

# The portal's platform picker maps friendly names onto the ad-unit channel
# names ``fetchChannels`` publishes. Sponsored Search collapses onto "Sponsored
# Products" — that is the portal's own behaviour, and why its Platform field
# shows Sponsored Products for any Search case.
PLATFORMS: dict[str, str] = {
    "display": "Display",
    "sponsored-search": "Sponsored Products",
    "search": "Sponsored Products",
    "sponsored-products": "Sponsored Products",
    "sponsored-brands": "Search Brand Amplifier",
    "sponsored-videos": "SponsoredVideos",
    "shop-builder": "ShopBuilder",
}
DEFAULT_PLATFORM = "display"


def resolve_platform(name: str) -> str:
    """Map a platform name onto the ad-unit channel the portal expects."""
    key = name.strip().casefold().replace("_", "-").replace(" ", "-")
    if key in PLATFORMS:
        return PLATFORMS[key]
    # Accept the internal channel names verbatim too.
    if name in PLATFORMS.values():
        return name
    raise AuraError(
        "platform",
        f"unknown platform {name!r}; expected one of: {', '.join(sorted(PLATFORMS))}",
    )


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
        return cls(
            user_type=str(raw.get("userType") or ""),
            account_id=str(contact.get("AccountId") or account.get("Id") or ""),
            account_name=str(account.get("Name") or ""),
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
            f"{partner_channel!r}; Display and Sponsored Products are the ones it populates",
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
    platform: str = DEFAULT_PLATFORM
    geo: str = DEFAULT_GEO

    @property
    def ad_unit(self) -> str:
        return resolve_platform(self.platform)


def build_payload(
    draft: CaseDraft, identity: Identity, selection: CategorySelection
) -> dict[str, Any]:
    """Assemble ``openCase`` parameters for a draft.

    Every parameter the method declares is sent, empty where it does not apply,
    because Apex binds by name and a missing one is not the same as a blank one.
    """
    values = {
        "Name": identity.contact_name,
        "Email": identity.contact_email,
        "Advertisers Affected": draft.advertisers,
        "Advertiser Account Name": identity.account_name,
        "Description": draft.description,
    }
    # Only send fields this category actually declares. The shape is a JSON
    # list of AdditionalFieldWrapper objects keyed title/value, mirroring the
    # Support_Form__c records' own Title__c: Apex deserializes the string into a
    # List (an object gives "Expected '[' at the beginning of List/Set") and
    # rejects any other key by name.
    declared = set(selection.level2.form_fields)
    additional = [
        {"title": label, "value": value}
        for label, value in values.items()
        if label in declared and value
    ]

    return {
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
        "additionalFieldsString": json.dumps(additional),
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
    }


def prepare(session: AuraSession, draft: CaseDraft) -> dict[str, Any]:
    """Resolve everything needed to file ``draft``, without filing it."""
    identity = Identity.fetch(session)
    selection = resolve_categories(
        session,
        category=draft.category,
        issue=draft.issue,
        ad_unit=draft.ad_unit,
        partner_channel=identity.user_type,
    )
    return build_payload(draft, identity, selection)


def submit(session: AuraSession, payload: dict[str, Any]) -> dict[str, Any]:
    """File the case. Returns the portal's CaseWrapper."""
    raw = session.apex(_COMPONENT, "openCase", payload)
    return raw if isinstance(raw, dict) else {"result": raw}
