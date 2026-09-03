"""Portal profiles.

The Advertising Help portal is two sites, not one: Walmart Connect at
``advertisinghelp.walmart.com`` and Sam's Club at
``advertisinghelp.samsclub.com``. They are separate Salesforce orgs running
the same custom app — same ``AC_*`` Apex controllers taking the same
parameters, same ``Case_Category__c`` schema, same Aura transport — so one
client serves both and only org *configuration* differs.

The differences below were established against both live orgs, including one
case filed end to end on Sam's Club.

That configuration is what this module holds:

* Walmart publishes ad-unit channels through ``fetchChannels`` and its case
  form offers a platform picker. Sam's Club answers that call with an empty
  list and shows no picker, filing everything against Sponsored Products.
* Walmart's ``getAuthenticationStatus`` carries the account on the contact
  record; Sam's Club leaves it off and names the advertiser through
  ``getAdvertiserInfo`` instead.
* Filing is not one method. Walmart declares a 31-parameter ``openCase`` and
  nothing else; Sam's declares 19 parameters on ``openCase`` plus a
  ``saveApiCase`` that its own form uses for every case under the API
  category. Aura drops undeclared parameters in silence, so one shared
  31-parameter call looked like it worked on both while Sam's quietly
  discarded 15 of them — ``additionalFieldsString`` among them, which is the
  only thing carrying the advertiser ids. Both signatures are recorded here so
  each org gets the call it actually declares.
* Sam's ``openCase`` HTML-escapes ``subject`` and ``problem`` **twice** before
  the insert; its ``saveApiCase`` stores the same bytes raw, and so does
  Walmart's ``openCase``. Routing API cases the way the portal's own form does
  therefore fixes the mangling at the source rather than papering over it on
  read.
* ``closeCaseSt`` closes a case on Walmart but is a **silent no-op** on Sam's
  Club: it resolves, answers SUCCESS, and returns the record with its status
  untouched. The portal's own Close Case button reaches some other action and
  lands the case on ``Canceled`` rather than ``Closed``. Closing is therefore
  gated per portal, because a caller cannot tell that failure from success
  without re-reading the case.

Reading, replying and attaching use identical actions on both and need no gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class PortalError(ValueError):
    """A portal was named that does not exist, or asked for something it lacks.

    Distinct from :class:`~walmart_support.aura.AuraError`: nothing has been
    sent to the portal yet, so the caller gave bad input rather than the portal
    refusing good input.
    """


# Walmart's platform picker maps friendly names onto the ad-unit channel names
# ``fetchChannels`` publishes. Sponsored Search collapses onto "Sponsored
# Products" — that is the portal's own behaviour, and why its Platform field
# shows Sponsored Products for any Search case.
_WALMART_PLATFORMS: Mapping[str, str] = MappingProxyType(
    {
        "display": "Display",
        "sponsored-search": "Sponsored Products",
        "search": "Sponsored Products",
        "sponsored-products": "Sponsored Products",
        "sponsored-brands": "Search Brand Amplifier",
        "sponsored-videos": "SponsoredVideos",
        "shop-builder": "ShopBuilder",
    }
)

_NO_PLATFORMS: Mapping[str, str] = MappingProxyType({})

# Each org's ``openCase`` parameters, in the order and spelling it declares
# them — recovered from the live component definition (``getComponentDef`` on
# ``markup://c:AC_OpenCase``), not guessed. Apex binds by name and Aura drops
# names the method does not declare, so the list is what decides whether a
# value reaches the Case at all. The two orgs even spell one parameter
# differently, hence ``ArticleNotHelped`` here against ``articleNotHelped``
# there.
_WALMART_OPEN_CASE: tuple[str, ...] = (
    "partnershipType",
    "partnershipId",
    "selectedAccountId",
    "guestAccountName",
    "guestName",
    "guestEmail",
    "guestSupplierNumber",
    "externalReference",
    "subject",
    "problem",
    "problemDomain",
    "subDomain",
    "subDomain2",
    "subDomain3",
    "backendDomain",
    "backendSubDomain",
    "backendSub2Domain",
    "backendSub3Domain",
    "additionalFieldsString",
    "ArticleNotHelped",
    "caseType",
    "product",
    "serviceNowKA",
    "geo",
    "UserType",
    "IsFeatureRequest",
    "currentExperience",
    "adUnit",
    "lastLevelCategory",
    "documentId",
    "billingDisputeJson",
)

_SAMSCLUB_OPEN_CASE: tuple[str, ...] = (
    "subject",
    "problem",
    "guestAccountName",
    "currentExperience",
    "articleNotHelped",
    "IsFeatureRequest",
    "problemDomain",
    "guestName",
    "subDomain",
    "subDomain2",
    "subDomain3",
    "lastLevelCategory",
    "guestEmail",
    "backendDomain",
    "campaignName",
    "supplierChannel",
    "backendSubDomain",
    "backendSub2Domain",
    "backendSub3Domain",
)

# The level-1 category Sam's own form treats as an API case, matched against
# the label the UI shows rather than the wizard record's internal name.
API_CATEGORY = "API"


def _normalise(name: str) -> str:
    return name.strip().casefold().replace("_", "-").replace(" ", "-")


@dataclass(frozen=True)
class Portal:
    """One Advertising Help site, and how it differs from the other."""

    key: str
    label: str
    # How the other side of a conversation is labelled in human output. The
    # JSON rendering stays portal-neutral; only the terminal view names a brand.
    support_label: str
    base_url: str
    # The ad unit a case is filed against when no platform is named.
    ad_unit: str
    # Which parameters this org's openCase declares, in its own spelling.
    open_case_params: tuple[str, ...]
    platforms: Mapping[str, str] = _NO_PLATFORMS
    # The Apex method this org files API-category cases through, when that is
    # not openCase. Sam's own contact form never calls openCase for anything
    # under API: AC_OpenCase sets isApiCase from the level-1 label and submits
    # saveApiCase(advWrapper) instead. Walmart declares no such method, files
    # everything through openCase, and leaves this empty.
    api_case_action: str = ""
    # What this org's channel picker should carry for the account we file as.
    # Sam's makes the field mandatory for an Advertiser-Ad_Agency user and
    # offers "Self Serve" or "API"; everything filed from here is the API
    # integration. Walmart's openCase declares no channel parameter.
    supplier_channel: str = ""
    # Whether ``closeCaseSt`` actually closes a case on this org. Sam's Club
    # resolves the action and answers SUCCESS with the record unchanged, so a
    # caller cannot tell a refusal from a success without re-reading the case.
    can_close: bool = True
    # Why not, when ``can_close`` is False.
    close_hint: str = ""

    def resolve_ad_unit(self, platform: str | None = None) -> str:
        """Map a ``--platform`` name onto the ad unit the portal expects."""
        if not self.platforms:
            # No picker at all: naming a platform asks for something this
            # portal does not offer, so say so rather than quietly ignoring it.
            if platform:
                raise PortalError(
                    f"{self.label} has no platform picker — it files every case against "
                    f"{self.ad_unit}, so --platform does not apply"
                )
            return self.ad_unit
        if not platform:
            return self.ad_unit
        key = _normalise(platform)
        if key in self.platforms:
            return self.platforms[key]
        # Accept the internal channel names verbatim too.
        if platform in self.platforms.values():
            return platform
        raise PortalError(
            f"unknown platform {platform!r} for {self.label}; expected one of: "
            f"{', '.join(sorted(self.platforms))}"
        )


WALMART = Portal(
    key="walmart",
    label="Walmart Connect",
    support_label="WALMART",
    base_url="https://advertisinghelp.walmart.com",
    ad_unit="Display",
    open_case_params=_WALMART_OPEN_CASE,
    platforms=_WALMART_PLATFORMS,
)

SAMSCLUB = Portal(
    key="samsclub",
    label="Sam's Club",
    support_label="SAM'S CLUB",
    base_url="https://advertisinghelp.samsclub.com",
    ad_unit="Sponsored Products",
    open_case_params=_SAMSCLUB_OPEN_CASE,
    api_case_action="saveApiCase",
    supplier_channel="API",
    # closeCaseSt resolves here and answers SUCCESS with the record untouched —
    # the status never moves. The portal's own "Close Case" button reaches some
    # other action and lands the case on Canceled rather than Closed, which is
    # where this org's Canceled cases come from.
    can_close=False,
    close_hint=(
        "closeCaseSt is a silent no-op on this org — it answers SUCCESS and leaves the status "
        "untouched. Close from the portal's own Close Case button instead, which lands the "
        "case on Canceled rather than Closed."
    ),
)

PORTALS: Mapping[str, Portal] = MappingProxyType({p.key: p for p in (WALMART, SAMSCLUB)})

# Spellings a person would reasonably type for the two brands.
_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "sams": SAMSCLUB.key,
        "sams-club": SAMSCLUB.key,
        "sam's-club": SAMSCLUB.key,
        "sams-club-connect": SAMSCLUB.key,
        "walmart-connect": WALMART.key,
    }
)


def resolve_portal(name: str) -> Portal:
    """Look up a portal by key or by an obvious spelling of its brand."""
    key = _normalise(name)
    portal = PORTALS.get(_ALIASES.get(key, key))
    if portal is None:
        raise PortalError(f"unknown portal {name!r}; expected one of: {', '.join(PORTALS)}")
    return portal
