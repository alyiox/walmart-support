# Walmart Connect Advertising Support Cases

[![CI](https://github.com/alyiox/mcp-walmart-support/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/alyiox/mcp-walmart-support/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<!-- mcp-name: io.github.alyiox/mcp-walmart-support -->

CLI for reading and filing [Walmart Connect](https://advertisinghelp.walmart.com) advertising
support cases without driving a browser.

The Advertising Help portal is a Salesforce Experience Cloud site with no public API. Every click in
its UI is one POST to `/s/sfsites/aura` naming an `@AuraEnabled` Apex method, so the flow is
scriptable — which matters, because submitting a case body through a headless browser is slow and,
in some environments, unreliable enough to leave you unsure whether a case was actually filed.

## Status

Early. `auth check` works; case read and create commands are landing as their Aura payloads are
captured from the portal UI.

## Requirements

- Python 3.13+
- A Walmart Advertising Help portal account (the Partners → Help Site login)

## Quick start

```bash
mkdir -p ~/.config/mcp-walmart-support
cp config.example.json ~/.config/mcp-walmart-support/config.json
$EDITOR ~/.config/mcp-walmart-support/config.json

uv run walmart-case auth check
uv run walmart-case cases list --status "need info"
uv run walmart-case cases get 10000001
uv run walmart-case cases replies 10000001 --from-walmart

# discover what to file under, then file
uv run walmart-case categories list --platform sponsored-search
uv run walmart-case case create \
  --platform display \
  --category "API Support" --issue "Endpoint-specific problem" \
  --subject "Display API: ..." --advertisers "111111, 222222" \
  --description-file ./body.txt          # prints the payload
uv run walmart-case case create ... --submit   # actually files it
```

## Filing a case

`case create` prints the exact `openCase` payload and files nothing unless
`--submit` is given. That default is deliberate: a case goes to Walmart's
support queue, and a mis-mapped category files a real but misrouted one.

Categories are resolved by name against the portal's own dropdown data rather
than hardcoded, so `categories list` shows exactly what the UI offers and both
the UI label ("API Support") and the wizard's internal name ("API") match.

`--platform` covers Display and Sponsored Search; the portal collapses Search
onto its "Sponsored Products" ad unit, which is why a Search case shows
Sponsored Products as its platform. Sponsored Brands and Videos are accepted
but the portal publishes no categories for them on a partner account, and the
error says so.

> **Unverified:** the identity and category parameters are confirmed — the
> resolved `API-AdCases` backend value matches what a filed case reports — but
> the remaining `openCase` parameters are inferred from the published method
> signature, not from an observed submit. Compare the payload printed without
> `--submit` against a real submit before trusting `--submit`.

## Sessions

Each invocation is its own process, so session cookies are cached in
`$XDG_CACHE_HOME/mcp-walmart-support/session.json` (mode `0600`) and reused
until the portal rejects them. Without that, every command would pay a full
login — four requests and a Salesforce login event before doing any work; with
it, a warm command is roughly twice as fast and logs in only when it must.

A session that dies mid-command is retried once from a clean login, because
Salesforce reports an invalid session in the middle of a request rather than up
front. `walmart-case auth logout` discards the cached session.

## Configuration

`~/.config/mcp-walmart-support/config.json`:

| Key | Required | Description |
| --- | --- | --- |
| `base_url` | no | Portal origin. Defaults to `https://advertisinghelp.walmart.com`. |
| `username` | yes | Portal login email. |
| `password` | yes | Portal password. |
| `timeout` | no | Per-request timeout in seconds (default 60). |

Every key can be overridden by an environment variable — `WALMART_SUPPORT_USERNAME`,
`WALMART_SUPPORT_PASSWORD`, `WALMART_SUPPORT_BASE_URL`, `WALMART_SUPPORT_TIMEOUT` — so CI needs no
file on disk.

Credentials are the only way in, deliberately. A session cookie cannot be configured by hand:
Salesforce `sid` cookies are session-scoped, expire on their own, and cannot renew themselves, so a
configured one becomes a stale secret that fails in a way the tool can do nothing about. Sessions
are managed by the cache below instead.

## Reading a case

`cases list` is backed by one action that returns every case at once, but it
**abbreviates** `subject` and `description` — the trailing `...` comes from
Walmart. `cases get` uses the detail action instead, which returns the full text
plus status, priority, category and the submitted form fields.

### Searching

`cases list --query` filters on the text the list action returns — and that text
is **abbreviated**: subjects are cut to roughly 46 characters, and some cases
carry almost no description at all. So a plain `--query` can miss a case whose
real body contains the term.

`--deep` re-reads each candidate's full text *and its replies* instead, at one
request per candidate:

```bash
# finds nothing: the term sits past where the list truncates the subject
walmart-case cases list --query "targeting of a LIVE ad group"

# finds both cases
walmart-case cases list --query "targeting of a LIVE ad group" --since 2026-08-01 --deep
```

Because it fans out, `--deep` refuses to run on more candidates than its cap
(25) and asks you to narrow with `--status`/`--since`/`--limit` rather than
firing a request per case in the account.

`--limit` on its own is handed to the portal as a SOQL `LIMIT`. Combined with a
filter it stays client-side, since the server would otherwise apply it *before*
filtering and return matches from an arbitrary slice.

`cases replies` shows the case conversation, flattened from the HTML the portal
stores, with each message attributed to `us` or `WALMART`:

```bash
walmart-case cases replies 10000001                      # whole thread
walmart-case cases replies 10000001 --from-walmart        # skip our own posts
walmart-case cases replies 10000001 --latest 1            # just the newest
```

Support's acknowledgement mails quote the entire case body back, and later
replies quote the ones before them, so an unfiltered thread is mostly repetition
of what you already sent — `--from-walmart --latest 1` is usually what you want.

## How it works

```
GET  /s/contact                     scrape the Aura context (fwuid, apck, lrmc, markup hash)
POST /s/sfsites/aura?r=N&...login   authenticate via LightningLoginFormController
POST /s/sfsites/aura?r=N&other....  call @AuraEnabled Apex methods through ApexActionController
```

Aura rejects any call whose framework context does not match the deployed build, and that context
rotates with every Salesforce release, so it is scraped on each run and never hardcoded. All of this
lives in `aura.py`; when Walmart's contract shifts, that is the one module to re-capture against.

## Development

```bash
uv sync --group dev
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright
uv run pytest tests/ -v
```

Tests are offline: they exercise recorded page shapes and Aura envelopes through `httpx.MockTransport`.

## License

MIT
