from __future__ import annotations

import pytest

from walmart_support.portals import (
    DEFAULT_PORTAL,
    PORTALS,
    SAMSCLUB,
    WALMART,
    PortalError,
    resolve_portal,
)


def test_walmart_is_the_default() -> None:
    assert DEFAULT_PORTAL == WALMART.key
    assert set(PORTALS) == {"walmart", "samsclub"}


def test_the_two_portals_are_distinct_hosts() -> None:
    # They are separate Salesforce orgs, which is what makes a per-host session
    # cache necessary rather than tidy.
    assert WALMART.host == "advertisinghelp.walmart.com"
    assert SAMSCLUB.host == "advertisinghelp.samsclub.com"


def test_brand_spellings_resolve() -> None:
    for name in ("samsclub", "sams", "sams-club", "Sam's Club", "SAMSCLUB"):
        assert resolve_portal(name) is SAMSCLUB
    for name in ("walmart", "Walmart", "walmart-connect"):
        assert resolve_portal(name) is WALMART


def test_an_unknown_portal_lists_the_valid_ones() -> None:
    with pytest.raises(PortalError, match="unknown portal"):
        resolve_portal("target")


def test_walmart_publishes_a_platform_picker() -> None:
    assert WALMART.has_platform_picker
    assert WALMART.resolve_ad_unit("shop-builder") == "ShopBuilder"
    assert WALMART.resolve_ad_unit(None) == "Display"


def test_samsclub_has_no_platform_picker() -> None:
    # fetchChannels answers with an empty list and the contact form shows no
    # picker; every case is filed against Sponsored Products.
    assert not SAMSCLUB.has_platform_picker
    assert SAMSCLUB.resolve_ad_unit(None) == "Sponsored Products"
    assert SAMSCLUB.resolve_ad_unit("") == "Sponsored Products"


def test_naming_a_platform_on_samsclub_is_refused() -> None:
    with pytest.raises(PortalError, match="no platform picker"):
        SAMSCLUB.resolve_ad_unit("display")


def test_both_portals_can_file_a_case() -> None:
    # Sam's Club creation was confirmed by filing case 00019047 through the CLI:
    # openCase is accepted and routes to the category it was given.
    assert WALMART.can_create
    assert SAMSCLUB.can_create


def test_only_walmart_can_close_a_case() -> None:
    # closeCaseSt resolves on Sam's Club, answers SUCCESS and leaves the status
    # untouched, so the CLI must refuse rather than report a close that did not
    # happen.
    assert WALMART.can_close
    assert not SAMSCLUB.can_close
    # A refusal has to say what to do instead.
    assert "Close Case" in SAMSCLUB.close_hint
