# Walmart Connect Advertising Support Cases

[![CI](https://github.com/alyiox/walmart-support/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/alyiox/walmart-support/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/walmart-support.svg)](https://pypi.org/project/walmart-support/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

CLI for reading and filing [Walmart Connect](https://advertisinghelp.walmart.com) advertising
support cases without driving a browser.

The Advertising Help portal is a Salesforce Experience Cloud site with no public API. Every click in
its UI is one POST to `/s/sfsites/aura` naming an `@AuraEnabled` Apex method, so the flow is
scriptable — which matters, because submitting a case body through a headless browser is slow and,
in some environments, unreliable enough to leave you unsure whether a case was actually filed.

## Status

Working end to end: authentication, listing and reading cases, replying, attaching files, closing,
and filing new cases. Each Aura payload was captured from the portal UI and verified against a real
case, so treat behaviour outside the documented commands as unmapped rather than unsupported.

## Requirements

- Python 3.13+, or [uv](https://docs.astral.sh/uv/)
- A Walmart Advertising Help portal account (the Partners → Help Site login)

## Quick start

There is nothing to install — `uvx` fetches and runs it:

```bash
mkdir -p ~/.config/walmart-support
cp config.example.json ~/.config/walmart-support/config.json
$EDITOR ~/.config/walmart-support/config.json

uvx walmart-support auth check
uvx walmart-support cases list --status "need info"
uvx walmart-support cases get 10000001
uvx walmart-support cases replies 10000001 --from-walmart

uvx walmart-support cases create \
  --platform display \
  --category "API Support" --issue "Endpoint-specific problem" \
  --subject "Display API: ..." --advertisers "111111, 222222" \
  --description-file ./body.txt                # prints the payload
uvx walmart-support cases create ... --submit   # actually files it

# where the --category and --issue names come from
uvx walmart-support categories list --platform sponsored-search
```

Reaching for it daily, or working offline? `uv tool install walmart-support`
puts it on `PATH` and starts faster; `walmart-support --version` reports which
build you are on either way. Inside a clone of this repo, use `uv run
walmart-support ...` to exercise your working tree.

## Replying and attaching

`cases reply` posts a comment on an existing case, and `cases attach` uploads
files to one:

```bash
walmart-support cases reply 10000001 --message-file answer.txt
walmart-support cases attach 10000001 ./har.json ./adgroup.json
```

Neither is gated behind a confirmation flag, unlike `cases create`: they act on
a case you already own, with no category mapping to get wrong. `create` files a
*new* record into the support queue, which is the thing worth a second look.

Both are writes, so **do not wrap them in a retry loop.** A network error after
the write lands looks identical to one before it, and retrying uploads the file
or posts the comment twice — the portal has no idempotency key.

Deletion is asymmetric and this bites: an upload returns a **ContentVersion**
id (`068…`), while the portal's delete actions want the **ContentDocument** id
(`069…`) and silently do nothing when handed the other. The `069` ids are
recoverable from a case's detail payload; `attachments.document_ids()` extracts
them.

Attaching *while filing* is not supported yet: `openCase` accepts a
`documentId` list, but `saveChunk` requires a `parentId` and the case does not
exist yet, so it is unclear what the portal passes at that point.

## Filing a case

`cases create` prints the exact `openCase` payload and files nothing unless
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

`openCase` returns the new case's number, and the whole path is verified
end to end — filed, replied to and closed. Three things had to be right, none
of which the method signature revealed:

* `additionalFieldsString` is a JSON **list** of `{title, value}` objects, not
  an object. Apex deserializes it into a `List` and names any unexpected key.
* `partnershipType`/`partnershipId` must be **empty**. `Partnership__c` is a
  lookup to a Partnership record for supplier and seller channels, so an
  account id there fails the insert with `FIELD_INTEGRITY_EXCEPTION`.
* Category routing comes from the resolved level-1/level-2 pair, and a filed
  case reports `API-AdCases` / `Endpoint-specific problem` back.

> **Known defect:** the portal resolves additional fields loosely against the
> `title` we send. On the first CLI-filed case, `Advertiser Account Name` and
> `Advertisers Affected` collided on their shared prefix and the account name
> was stored under the advertisers field, dropping the ids. The colliding field
> is no longer sent, which should fix it, but that is **unconfirmed until the
> next filing** — and the wrapper evidently carries an identifier beyond
> `title`/`value` (its deserializer also accepts `fieldName`).
>
> Either way, put anything that matters in the description body: it is stored
> verbatim, whereas these fields are not reliably addressable.

### Closing and replying

```bash
walmart-support cases reply 10000004 --message-file answer.txt
walmart-support cases close 10000004   # New -> Closed
```

## Sessions

Each invocation is its own process, so session cookies are cached in
`$XDG_CACHE_HOME/walmart-support/session.json` (mode `0600`) and reused
until the portal rejects them. Without that, every command would pay a full
login — four requests and a Salesforce login event before doing any work; with
it, a warm command is roughly twice as fast and logs in only when it must.

A session that dies mid-command is retried once from a clean login, because
Salesforce reports an invalid session in the middle of a request rather than up
front. `walmart-support auth logout` discards the cached session.

## Configuration

`~/.config/walmart-support/config.json`:

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
walmart-support cases list --query "targeting of a LIVE ad group"

# finds both cases
walmart-support cases list --query "targeting of a LIVE ad group" --since 2026-08-01 --deep
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
walmart-support cases replies 10000001                  # whole thread
walmart-support cases replies 10000001 --from-walmart   # skip our own posts
walmart-support cases replies 10000001 --latest 1       # just the newest
```

Support's acknowledgement mails quote the entire case body back, and later
replies quote the ones before them, so an unfiltered thread is mostly repetition
of what you already sent — `--from-walmart --latest 1` is usually what you want.

## Claude Code plugin

The repo doubles as a [Claude Code](https://claude.com/claude-code) plugin, so the workflow
knowledge travels with the CLI instead of living in one person's `~/.claude`:

```
/plugin marketplace add alyiox/walmart-support
/plugin install walmart-support@walmart-support
```

That installs a skill covering the parts the `--help` output cannot express — that `--deep` costs
one request per case, that support's replies quote the whole thread back, that `cases create` files
nothing without `--submit`, and that `cases close` is one-way. The plugin does not install the CLI;
`walmart-support --version` tells you whether you still need to.

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
