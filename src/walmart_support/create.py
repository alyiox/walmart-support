"""Case creation.

The portal's wizard is data-driven, and the two orgs drive it differently:

* Walmart serves the category tree through ``getCategoryTree``, and the issue a
  user lands on names a form config that ``getFormForConfig`` expands into
  typed fields, each bound to a Case field. Those fields travel back in
  ``additionalFieldsString``, whole, the way the form's own controller sends
  them.
* Sam's Club serves ``Case_Category__c`` records through
  ``getCaseCategoriesNew``, and no category there declares form fields.

Category identifiers are looked up by name at call time rather than hardcoded,
so the mapping follows the portal if a category is re-labelled or re-ided.

**The parameter mapping below is taken from each form's own controller**, not
from an observed submit. That is why the CLI refuses to submit without an
explicit flag: a mis-mapped category would file a real but misrouted case with
support. Walmart validates its form fields only in the browser — its
``openCase`` inserts a case with a required field left blank — so the checks
the form would have run are made here instead, as :attr:`Submission.problems`.

Which action files the case is the portal's business, so a draft becomes a
:class:`Submission` — the method name plus its parameters — rather than one
payload shape:

* Walmart: ``openCase`` with the 19 parameters it declares.
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
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .aura import AuraError, AuraSession
from .portals import API_CATEGORY, WALMART, Portal

_COMPONENT = "AC_ContactSupport"
CONTACT_PAGE = "/s/contact?language=en_US"

OPEN_CASE = "openCase"

DEFAULT_GEO = "US"

# The category selector renames a few ad units before asking for their tree;
# the unit itself still reaches openCase under its channel name.
_TREE_PLATFORMS: Mapping[str, str] = {
    "Display": "Onsite Display",
    "ShopBuilder": "BrandShops",
    "Store Ads": "In-Store Ads",
}

# getFormForConfig names field types one way and the openCase wrapper expects
# the older names, which the form translates before sending.
_LEGACY_FIELD_TYPES: Mapping[str, str] = {
    "Picklist": "SinglePicklist",
    "MultiPicklist": "MultiSelectPicklist",
    "Radio": "RadioGroup",
    "File": "File Upload",
}

_FILE_UPLOAD = "File Upload"
_ADVERTISER_GROUP = "advertiser"
# The titles the form fills from its advertiser picker, matched by name as the
# form matches them; a few forms declare the id outside the advertiser group.
_ADVERTISER_ID = "walmart ad center advertiser id"
_ADVERTISER_NAME = "walmart advertiser name"


class FormError(ValueError):
    """A draft names form fields the selected issue does not have.

    Raised before anything is filed, so the caller fixes the command rather
    than the connection.
    """


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

    On Sam's Club each wizard record links to a plain "Category" record that
    carries the two values that actually matter: the label the UI dropdown
    shows, and the value written to the Case. For API support those are "API"
    and "API-AdCases" — the latter is what a filed case reports as its
    category. Walmart's tree has one name per node, so both are that label,
    and an issue carries the ``config_id`` its form is built from.
    """

    record_id: str
    label: str
    backend_name: str
    wizard_name: str
    form_fields: list[str] = field(default_factory=list)
    config_id: str = ""

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


def _node(node: dict[str, Any]) -> Category:
    label = str(node.get("label") or "")
    return Category(
        record_id=str(node.get("id") or ""),
        label=label,
        backend_name=label,
        wizard_name=label,
        config_id=str(node.get("configId") or ""),
    )


def _fetch_form_tree(
    session: AuraSession, *, ad_unit: str, partner_channel: str
) -> list[tuple[Category, list[Category]]]:
    """Walmart's tree: nested nodes, the issues a level down from each category."""
    raw = session.apex(
        _COMPONENT,
        "getCategoryTree",
        {
            "platform": _TREE_PLATFORMS.get(ad_unit, ad_unit),
            "userType": partner_channel,
            "auth": "Authenticated",
        },
    )
    tree = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(tree, list):
        raise AuraError("getCategoryTree", "unexpected category payload")
    return [
        (_node(l1), [_node(l2) for l2 in l1.get("children") or [] if isinstance(l2, dict)])
        for l1 in tree
        if isinstance(l1, dict)
    ]


def fetch_category_tree(
    session: AuraSession, *, ad_unit: str, partner_channel: str, portal: Portal
) -> list[tuple[Category, list[Category]]]:
    """Return the support categories as the UI dropdown presents them.

    ``partner_channel`` is the caller's own user type; without it the portal
    answers with an empty tree rather than an error.
    """
    if portal.form_engine:
        return _fetch_form_tree(session, ad_unit=ad_unit, partner_channel=partner_channel)
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
    session: AuraSession,
    *,
    category: str,
    issue: str,
    ad_unit: str,
    partner_channel: str,
    portal: Portal,
) -> CategorySelection:
    """Look up the L1/L2 category pair the portal would have selected."""
    tree = fetch_category_tree(
        session, ad_unit=ad_unit, partner_channel=partner_channel, portal=portal
    )
    action = "getCategoryTree" if portal.form_engine else "getCaseCategoriesNew"
    if not tree:
        raise AuraError(
            action,
            f"the portal offers no categories for ad unit {ad_unit!r} on channel "
            f"{partner_channel!r}; check the ad unit against 'categories list'",
        )

    match = next(((l1, kids) for l1, kids in tree if l1.matches(category)), None)
    if match is None:
        names = ", ".join(sorted(l1.label for l1, _ in tree))
        raise AuraError(action, f"no category matching {category!r}; have: {names}")

    parent, children = match
    child = next((c for c in children if c.matches(issue)), None)
    if child is None:
        names = ", ".join(sorted(c.label for c in children)) or "(none)"
        raise AuraError(action, f"no issue matching {issue!r}; have: {names}")

    return CategorySelection(level1=parent, level2=child)


@dataclass(frozen=True)
class CaseDraft:
    subject: str
    description: str
    advertisers: str = ""
    category: str = "API"
    issue: str = "Endpoint-Specific"
    # Already resolved by Portal.resolve_ad_unit: which names are accepted is
    # the portal's business, not the draft's.
    ad_unit: str = WALMART.ad_unit
    geo: str = DEFAULT_GEO
    # Values for the issue's own form fields, keyed by the label the form shows.
    fields: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FormField:
    """One field of a Walmart issue form, as ``getFormForConfig`` declares it."""

    record_id: str
    label: str
    # The Case field the value lands in; empty for a file upload.
    case_field: str
    # Already translated to the name the openCase wrapper expects.
    field_type: str
    required: bool
    options: tuple[str, ...] = ()
    help_text: str = ""
    validation: str = ""
    validation_message: str = ""
    # Rendered by the form itself rather than as a plain input.
    custom_ui: bool = False
    read_only: bool = False
    group: str = ""
    depends_on: str = ""
    depends_on_values: str = ""
    default: str = ""

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> FormField:
        kind = str(record.get("fieldType") or "")
        return cls(
            record_id=str(record.get("id") or ""),
            label=str(record.get("label") or ""),
            case_field=str(record.get("caseField") or ""),
            field_type=_LEGACY_FIELD_TYPES.get(kind, kind),
            required=record.get("required") == "Always",
            options=tuple(
                str(o.get("label"))
                for o in record.get("options") or []
                if isinstance(o, dict) and o.get("label")
            ),
            help_text=str(record.get("helpText") or ""),
            validation=str(record.get("validationPattern") or ""),
            validation_message=str(record.get("validationMessage") or ""),
            custom_ui=record.get("customUi") is True,
            read_only=record.get("readOnly") is True,
            group=str(record.get("fieldGroup") or ""),
            depends_on=str(record.get("dependsOnFieldId") or ""),
            depends_on_values=str(record.get("dependsOnValue") or ""),
            default=str(record.get("value") or ""),
        )

    @property
    def is_description(self) -> bool:
        return "description" in (self.label.strip().casefold(), self.case_field.casefold())

    @property
    def in_advertiser_group(self) -> bool:
        return self.group.casefold() == _ADVERTISER_GROUP

    @property
    def is_advertiser_id(self) -> bool:
        label = self.label.strip().casefold()
        return label == _ADVERTISER_ID or (self.in_advertiser_group and "advertiser id" in label)

    @property
    def is_advertiser_name(self) -> bool:
        label = self.label.strip().casefold()
        return label == _ADVERTISER_NAME or (
            self.in_advertiser_group and "advertiser name" in label
        )

    @property
    def is_file(self) -> bool:
        return self.field_type == _FILE_UPLOAD

    @property
    def skip(self) -> bool:
        """The form's own ``skip`` flag: fields it fills or renders itself.

        The advertiser group is drawn as a picker of its own, and the
        description is the case body.
        """
        if self.custom_ui or self.is_description or self.in_advertiser_group:
            return True
        if self.read_only:
            return False
        return not self.is_file and not self.case_field

    @property
    def settable(self) -> bool:
        """Whether a draft supplies this field through ``fields``.

        The advertiser name is typed by hand whenever the account has no
        advertiser records to pick from, which is the case for an API partner.
        """
        if self.is_file or self.is_description or self.is_advertiser_id:
            return False
        return self.is_advertiser_name or not self.skip

    def condition_met(self, values: Mapping[str, str]) -> bool:
        """Whether the field it depends on holds one of the values it shows for."""
        if not self.depends_on:
            return True
        allowed = [v.strip() for v in self.depends_on_values.split(",") if v.strip()]
        if not allowed:
            return True
        current = values.get(self.depends_on, "")
        return any(v.strip() in allowed for v in current.split(";"))


@dataclass(frozen=True)
class Form:
    """A Walmart issue's form: the fields to fill and the template they came from."""

    template_id: str
    fields: tuple[FormField, ...]
    submission_disabled: bool = False

    @property
    def settable(self) -> tuple[FormField, ...]:
        return tuple(f for f in self.fields if f.settable)


def fetch_form(session: AuraSession, config_id: str) -> Form:
    """Expand an issue's form config into its fields, deduplicated as the form does."""
    raw = session.apex(_COMPONENT, "getFormForConfig", {"configId": config_id})
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise AuraError("getFormForConfig", "unexpected form payload")
    config = payload.get("config") or {}
    seen: set[tuple[str, ...]] = set()
    fields: list[FormField] = []
    for record in payload.get("fields") or []:
        if not isinstance(record, dict):
            continue
        item = FormField.from_record(record)
        key = (item.label.strip(), item.case_field, item.field_type)
        key = tuple(part.casefold() for part in key)
        if key not in seen:
            seen.add(key)
            fields.append(item)
    return Form(
        template_id=str(config.get("Form_Template__c") or ""),
        fields=tuple(fields),
        submission_disabled=config.get("Disable_Submission__c") is True,
    )


def _supplied_values(draft: CaseDraft, form: Form) -> dict[str, str]:
    """Match the draft's field values to the form, by the label it shows.

    A label the form does not offer is refused rather than dropped, and so is
    one the draft already fills another way.
    """
    by_label = {f.label.strip().casefold(): f for f in form.fields}
    values: dict[str, str] = {}
    for label, value in draft.fields.items():
        target = by_label.get(label.strip().casefold())
        if target is None or not target.settable:
            if target is not None and target.is_description:
                raise FormError(f"{target.label!r} is the case body; pass it as --description-file")
            if target is not None and target.is_advertiser_id:
                raise FormError(f"{target.label!r} is filled from --advertisers")
            offered = ", ".join(repr(f.label) for f in form.settable) or "(none)"
            raise FormError(f"this issue has no form field {label!r}; it offers: {offered}")
        values[target.record_id] = value
    return values


def _form_values(draft: CaseDraft, form: Form) -> dict[str, str]:
    """Each field's value by record id, before dependencies blank any out."""
    values = {f.record_id: f.default for f in form.fields}
    values |= _supplied_values(draft, form)
    for f in form.fields:
        if f.is_description:
            values[f.record_id] = draft.description
        elif f.is_advertiser_id:
            values[f.record_id] = draft.advertisers
    return values


def form_entries(draft: CaseDraft, form: Form) -> list[dict[str, Any]]:
    """Every form field with its value, shaped as the form's own controller sends it.

    A field hidden by its dependency goes out blank, as the form sends it.
    """
    values = _form_values(draft, form)
    return [
        {
            "title": f.label,
            "required": f.required,
            "value": values[f.record_id] if f.condition_met(values) else "",
            "fieldName": f.case_field or None,
            "fieldType": f.field_type,
            "options": list(f.options) or None,
            "helpText": f.help_text or None,
            "validations": f.validation or None,
            "validationErrorMessage": f.validation_message or None,
            "skip": f.skip,
        }
        for f in form.fields
    ]


def form_problems(draft: CaseDraft, form: Form) -> list[str]:
    """What the portal's own form would refuse to submit.

    The server accepts all of it, so these are the only checks a filing gets.
    """
    if form.submission_disabled:
        return ["the portal does not accept new cases under this issue"]
    values = _form_values(draft, form)
    problems: list[str] = []
    for f in form.fields:
        if f.is_advertiser_id and f.required and not draft.advertisers:
            problems.append(f"{f.label!r} is required: pass --advertisers")
        if f.is_description and f.required and not draft.description.strip():
            problems.append("the description is required and its file is empty")
        if not f.settable or not f.condition_met(values):
            continue
        value = values[f.record_id]
        if f.required and not value:
            hint = f"; one of: {', '.join(f.options)}" if f.options else ""
            problems.append(f"{f.label!r} is required{hint}")
        elif value and f.field_type == "SinglePicklist" and f.options and value not in f.options:
            problems.append(f"{f.label!r} must be one of: {', '.join(f.options)}")
        elif value and f.validation and not re.search(f.validation, value):
            problems.append(f.validation_message or f"{f.label!r} does not match {f.validation}")
    return problems


def _open_case_values(
    draft: CaseDraft,
    identity: Identity,
    selection: CategorySelection,
    *,
    portal: Portal,
    form: Form | None = None,
) -> dict[str, Any]:
    """Every value ``openCase`` can take, keyed by parameter name casefolded.

    The two orgs declare overlapping but different parameter lists and spell one
    of them differently, so the values live in one table and the profile's own
    list decides which go out. Lookup is casefolded because Apex binds names
    case-insensitively and only the org's own spelling belongs on the wire.
    """
    entries = form_entries(draft, form) if form else []
    values: dict[str, Any] = {
        "selectedAccountId": identity.account_id,
        "guestAccountName": identity.account_name,
        "guestName": identity.contact_name,
        "guestEmail": identity.contact_email,
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
        "additionalFieldsString": json.dumps(entries),
        "ArticleNotHelped": "",
        "geo": draft.geo,
        "UserType": identity.user_type,
        "IsFeatureRequest": False,
        "currentExperience": "",
        "adUnit": draft.ad_unit,
        "lastLevelCategory": selection.level2.record_id,
        "documentId": [],
        "advertiserId": draft.advertisers,
        "formTemplateId": form.template_id if form else "",
        "campaignName": "",
        "supplierChannel": portal.supplier_channel,
    }
    return {name.casefold(): value for name, value in values.items()}


def build_payload(
    draft: CaseDraft,
    identity: Identity,
    selection: CategorySelection,
    *,
    portal: Portal,
    form: Form | None = None,
) -> dict[str, Any]:
    """Assemble ``openCase`` parameters for a draft, as ``portal`` declares them.

    Every parameter the org's method declares is sent, empty where it does not
    apply, because Apex binds by name and a missing one is not the same as a
    blank one. Nothing it does not declare is sent, because Aura would drop it
    in silence and the caller would never learn the value went nowhere.
    """
    values = _open_case_values(draft, identity, selection, portal=portal, form=form)
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
    # What the portal's own form would have refused; filing waits on none.
    problems: tuple[str, ...] = ()

    @property
    def fields(self) -> dict[str, Any]:
        """What a reviewer should read, with a wrapper unwrapped.

        ``saveApiCase`` takes its whole payload under one ``advWrapper`` key,
        and Walmart's form fields travel as one JSON string; either would print
        as a single unreadable line in the dry run. A form field is listed by
        its label, and the description field is left out because it repeats
        ``problem``.
        """
        wrapper = self.params.get("advWrapper")
        if isinstance(wrapper, dict):
            return dict(wrapper)
        view = {k: v for k, v in self.params.items() if k != "additionalFieldsString"}
        for entry in json.loads(self.params.get("additionalFieldsString") or "[]"):
            if entry.get("value") and str(entry.get("fieldName") or "").casefold() != "description":
                view[f"form: {entry.get('title')}"] = entry["value"]
        return view

    @property
    def carries_advertisers(self) -> bool:
        """Whether the advertiser ids a draft named actually reach the portal.

        They ride a parameter of their own under either action, but Sam's
        ``openCase`` declares none.
        """
        fields = self.fields
        return bool(fields.get("advertiserAffected") or fields.get("advertiserId"))


def build_submission(
    draft: CaseDraft,
    identity: Identity,
    selection: CategorySelection,
    *,
    portal: Portal,
    form: Form | None = None,
) -> Submission:
    """Choose the action ``portal`` files this draft with, and build its params.

    The level-1 label is what Sam's own form branches on, so it is what decides
    here too.
    """
    if portal.api_case_action and selection.level1.label.strip() == API_CATEGORY:
        wrapper = build_api_case(draft, identity, selection, portal=portal)
        return Submission(portal.api_case_action, {"advWrapper": wrapper})
    params = build_payload(draft, identity, selection, portal=portal, form=form)
    problems = tuple(form_problems(draft, form)) if form else ()
    return Submission(OPEN_CASE, params, problems)


def prepare(session: AuraSession, draft: CaseDraft, *, portal: Portal) -> Submission:
    """Resolve everything needed to file ``draft``, without filing it."""
    if draft.fields and not portal.form_engine:
        raise FormError(f"{portal.label} declares no form fields, so --field does not apply")
    identity = Identity.fetch(session)
    selection = resolve_categories(
        session,
        category=draft.category,
        issue=draft.issue,
        ad_unit=draft.ad_unit,
        partner_channel=identity.user_type,
        portal=portal,
    )
    form = None
    if portal.form_engine:
        if not selection.level2.config_id:
            raise AuraError("getCategoryTree", f"{selection.level2.label!r} names no form config")
        form = fetch_form(session, selection.level2.config_id)
    return build_submission(draft, identity, selection, portal=portal, form=form)


def submit(session: AuraSession, submission: Submission) -> dict[str, Any]:
    """File the case. Returns the portal's CaseWrapper.

    Both actions answer with the same wrapper shape, which is why the portal's
    own form reads ``caseNumber`` off either without checking which it called.
    """
    raw = session.apex(_COMPONENT, submission.action, submission.params)
    return raw if isinstance(raw, dict) else {"result": raw}
