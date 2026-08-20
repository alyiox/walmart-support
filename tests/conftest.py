from __future__ import annotations

import json
import urllib.parse

import pytest

# Shape mirrors the live portal bootstrap: several /sfsites/l/{...}/ asset URLs
# where only some carry fwuid. Values are scrubbed.
CONTEXT = {
    "mode": "PROD",
    "dfs": "21",
    "app": "siteforce:communityApp",
    "fwuid": "TEST_FWUID_14.192.8388608",
    "loaded": {"APPLICATION@markup://siteforce:communityApp": "1706_testmarkuphash"},
    "apce": 1,
    "apck": "TEST_APCK",
    "mlr": 1,
    "pathPrefix": "",
    "dns": "c",
    "ls": 1,
    "lrmc": "551347034",
}

STYLE_ONLY_CONTEXT = {
    "mode": "PROD",
    "app": "siteforce:communityApp",
    "styleContext": {"c": "webkit"},
    "pathPrefix": "",
}


def _segment(ctx: dict[str, object]) -> str:
    return urllib.parse.quote(json.dumps(ctx), safe="")


def make_page(*, authenticated: bool) -> str:
    attributes = {
        "schema": "Published",
        "authenticated": "true" if authenticated else "false",
        "language": "en_US",
        "pageId": "5e6e252a-0df2-4fff-9de6-69eea819d64e",
    }
    aura_config = {"attributes": attributes, "ns": {"internal": ["@locker"]}}
    return (
        "<html><head>"
        f'<script src="/s/sfsites/l/{_segment(STYLE_ONLY_CONTEXT)}/resources.js"></script>'
        f'<script src="/s/sfsites/l/{_segment(CONTEXT)}/app.js"></script>'
        f"<script>auraConfig = {json.dumps(aura_config)};</script>"
        "</head><body></body></html>"
    )


@pytest.fixture
def guest_page() -> str:
    return make_page(authenticated=False)


@pytest.fixture
def member_page() -> str:
    return make_page(authenticated=True)
