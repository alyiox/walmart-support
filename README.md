# Walmart Connect & Sam's Club Advertising Support Cases

[![CI](https://github.com/alyiox/walmart-support/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/alyiox/walmart-support/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/walmart-support.svg)](https://pypi.org/project/walmart-support/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

CLI for reading and filing advertising support cases on both
[Walmart Connect](https://advertisinghelp.walmart.com) and
[Sam's Club](https://advertisinghelp.samsclub.com), without driving a browser.

The Advertising Help portals are Salesforce Experience Cloud sites with no public API. Every click
in the UI is one POST to `/s/sfsites/aura` naming an `@AuraEnabled` Apex method, so the flow is
scriptable — which matters, because submitting a case body through a headless browser is slow and,
in some environments, unreliable enough to leave you unsure whether a case was actually filed.

The two are separate Salesforce orgs (`00D0000000000AA` and `00D0000000000BB`) running the same
custom app: same `AC_*` Apex controllers taking the same parameters, same `Case_Category__c` schema.
So this is one client pointed at two tenants, not two clients. `--portal` picks one.

## Status

**Walmart** — working end to end: authentication, listing and reading cases, replying, attaching
files, closing, and filing new cases. Each Aura payload was captured from the portal UI and verified
against a real case, so treat behaviour outside the documented commands as unmapped rather than
unsupported.

**Sam's Club** — verified end to end by filing case `00010001` through this CLI: create, attach and
reply, reading each step back. Two divergences worth knowing:

* `--advertisers` never reaches this org. No Sam's category declares any `Support_Form__c` records,
  so `additionalFieldsString` goes out empty and its Apex fills Advertisers Affected from the
  account name instead. The CLI warns; put the ids in the description body.
* **`cases close` is refused.** `closeCaseSt` resolves there, answers SUCCESS, and leaves the status
  untouched — indistinguishable from a real close without re-reading. Closing is a UI action on that
  portal, and it lands the case on `Canceled` rather than `Closed`.

See `skills/walmart-support/references/samsclub.md`.

## Requirements

- Python 3.13+, or [uv](https://docs.astral.sh/uv/)
- An Advertising Help portal account for whichever portal you are using (the Partners → Help
  Site login for Walmart; the Advertiser Community login for Sam's Club)

## Quick start

There is nothing to install — `uvx` fetches and runs it:

```bash
mkdir -p ~/.config/walmart-support
cp config.example.json ~/.config/walmart-support/config.json
$EDITOR ~/.config/walmart-support/config.json

uvx walmart-support --portal walmart auth check
uvx walmart-support --portal walmart cases list --status "need info"
uvx walmart-support --portal walmart cases get 10000001
uvx walmart-support --portal walmart cases replies 10000001 --from-support

# the same commands against Sam's Club
uvx walmart-support --portal samsclub cases list --limit 5
uvx walmart-support --portal samsclub cases get 00010002

uvx walmart-support --portal walmart cases create \
  --platform display \
  --category "API Support" --issue "Endpoint-specific problem" \
  --subject "Display API: ..." --advertisers "111111, 222222" \
  --description-file ./body.txt                # prints the payload
uvx walmart-support --portal walmart cases create ... --submit   # actually files it

# where the --category and --issue names come from
uvx walmart-support --portal walmart categories list --platform sponsored-search
```

`--portal` and `--json` are global, so they go before the subcommand. `--portal` is required on
every command: nothing in the config selects a portal, so each invocation says which retailer it
acts on rather than inheriting a choice made elsewhere.

Reaching for it daily, or working offline? `uv tool install walmart-support`
puts it on `PATH` and starts faster; `walmart-support --version` reports which
build you are on either way. Inside a clone of this repo, use `uv run
walmart-support ...` to exercise your working tree.

## Replying and attaching

`cases reply` posts a comment on an existing case, and `cases attach` uploads
files to one:

```bash
walmart-support --portal walmart cases reply 10000001 --message-file answer.txt
walmart-support --portal walmart cases attach 10000001 ./har.json ./adgroup.json
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

Works on both portals, with the `--advertisers` caveat noted under Status for
Sam's Club.

`cases create` prints the exact `openCase` payload and files nothing unless
`--submit` is given. That default is deliberate: a case goes to a real support
queue, and a mis-mapped category files a real but misrouted one.

Categories are resolved by name against the portal's own dropdown data rather
than hardcoded, so `categories list` shows exactly what the UI offers and both
the UI label ("API Support") and the wizard's internal name ("API") match.

`--platform` covers Display and Sponsored Search; the portal collapses Search
onto its "Sponsored Products" ad unit, which is why a Search case shows
Sponsored Products as its platform. Sponsored Brands and Videos are accepted
but the portal publishes no categories for them on a partner account, and the
error says so. Sam's Club publishes no ad-unit channels at all, so it has no
platform picker and `--platform` is refused there rather than ignored.

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
walmart-support --portal walmart cases reply 10000004 --message-file answer.txt
walmart-support --portal walmart cases close 10000004   # New -> Closed
```

`cases close` verifies the status actually moved and exits 1 if it did not, so
a zero exit is the only evidence that a case really closed. It is Walmart-only;
on Sam's Club it exits 2 without contacting the portal.

> **Open issue — `cases close` is unimplemented for Sam's Club.**
>
> `closeCaseSt` is what closes a Walmart case. On the Sam's Club org it
> resolves, answers `SUCCESS`, and returns the case with its status
> **untouched** — a silent no-op, indistinguishable from a real close without
> re-reading the case. `closeCase` and `closeCaseStatus` do not exist there.
> The command is gated (`Portal.can_close`) rather than left to report a close
> that never happened.
>
> The portal's own **Close Case** button does work: case page → confirmation
> modal → OK, landing the case on **`Canceled`**, not `Closed`. That is where
> the org's `Canceled` cases come from, and it is the workaround to point users
> at meanwhile.
>
> *To resolve:* capture the Aura action behind that button. The page reloads on
> confirm, which wiped the request log on the attempt made here, so it needs a
> capture that survives the reload (request interception, or `Preserve log`).
> Once the action and its parameters are known, implement it behind
> `can_close` and decide whether `Canceled` should be reported as a close or as
> its own outcome.

## Sessions

Each invocation is its own process, so session cookies are cached in
`$XDG_CACHE_HOME/walmart-support/<portal-host>.json` (mode `0600`) and reused
until the portal rejects them. Without that, every command would pay a full
login — four requests and a Salesforce login event before doing any work; with
it, a warm command is roughly twice as fast and logs in only when it must.

The cache is keyed by host because the portals are different Salesforce orgs:
replaying Walmart's `sid` against Sam's Club authenticates nothing while still
looking like a usable cached session, so one shared file would make every other
command pay a failed round trip before logging in again.

A session that dies mid-command is retried once from a clean login, because
Salesforce reports an invalid session in the middle of a request rather than up
front. `walmart-support --portal walmart auth logout` discards the cached session.

## Configuration

`~/.config/walmart-support/config.json` holds one section per portal:

```json
{
  "default": { "timeout": 60 },
  "portals": {
    "walmart":  { "username": "you@example.com", "password": "..." },
    "samsclub": { "username": "you@example.com", "password": "..." }
  }
}
```

| Key | Required | Description |
| --- | --- | --- |
| `default.timeout` | no | Per-request timeout in seconds (default 60). |
| `portals.<key>.username` | yes | Portal login email. |
| `portals.<key>.password` | yes | Portal password. |
| `portals.<key>.base_url` | no | Overrides the portal's own origin — a sandbox, say. |
| `portals.<key>.timeout` | no | Overrides `default.timeout` for that portal. |

Configure only the portals you use; a command naming one with no section refuses rather than
falling back, so one org's password is never sent to the other.

This file is the only source of credentials — there is no environment fallback, so which
credentials a command used is always answerable by reading one path. `--config` points somewhere
else, which is how CI supplies a file written from a secret.

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
walmart-support --portal walmart cases list --query "targeting of a LIVE ad group"

# finds both cases
walmart-support --portal walmart cases list --query "targeting of a LIVE ad group" --since 2026-08-01 --deep
```

Because it fans out, `--deep` refuses to run on more candidates than its cap
(25) and asks you to narrow with `--status`/`--since`/`--limit` rather than
firing a request per case in the account.

`--limit` on its own is handed to the portal as a SOQL `LIMIT`. Combined with a
filter it stays client-side, since the server would otherwise apply it *before*
filtering and return matches from an arbitrary slice.

`cases replies` shows the case conversation, flattened from the HTML the portal
stores, with each message attributed to `us` or the portal's own name (`WALMART`,
`SAM'S CLUB`). Under `--json` the other side is always `support`, so a consumer
does not have to know which portal it read:

```bash
walmart-support --portal walmart cases replies 10000001                  # whole thread
walmart-support --portal walmart cases replies 10000001 --from-support   # skip our own posts
walmart-support --portal walmart cases replies 10000001 --latest 1       # just the newest
```

Support's acknowledgement mails quote the entire case body back, and later
replies quote the ones before them, so an unfiltered thread is mostly repetition
of what you already sent — `--from-support --latest 1` is usually what you want.

## Agent plugins (Claude Code, Cursor, Codex)

The repo doubles as an [Agent Plugin](https://agent-plugins.org), so workflow knowledge travels
with the CLI instead of living in one person's local config.

### Claude Code

```
/plugin marketplace add alyiox/walmart-support
/plugin install walmart-support@walmart-support
```

### Cursor

Install via Cursor Plugins (`/add-plugin walmart-support` or from the Cursor Marketplace / `.cursor-plugin`).
For local development, link this repo into `~/.cursor/plugins/local/walmart-support` or copy `.cursor/skills/`.

### Other Agent Plugins / Codex

Conforms to the **Agent Plugins 1.0** specification via the root `plugin.json` and `.codex-plugin/plugin.json`,
making the `walmart-support` skill portable across ChatGPT/Codex, GitHub Copilot, and VS Code.

---

Installing the plugin installs a skill covering what `--help` cannot express — that `--deep` costs
one request per case, that support's replies quote the whole thread back, that `cases create` files
nothing without `--submit`, and that `cases close` is one-way. The plugin does not install the CLI;
`walmart-support --version` tells you whether you still need to.

The plugin and the CLI share one version and ship on one tag. A skill-only change is still a
release, so some PyPI versions carry byte-identical code to the one before them — the number tracks
the repo, not the Python package. That is deliberate: it keeps the guarantee worth having, which is
that the skill you install always describes the CLI released alongside it.

## How it works

```
GET  /s/contact                     scrape the Aura context (fwuid, apck, lrmc, markup hash)
POST /s/sfsites/aura?r=N&...login   authenticate via LightningLoginFormController
POST /s/sfsites/aura?r=N&other....  call @AuraEnabled Apex methods through ApexActionController
```

Aura rejects any call whose framework context does not match the deployed build, and that context
rotates with every Salesforce release, so it is scraped on each run and never hardcoded. All of this
lives in `aura.py`; when the contract shifts, that is the one module to re-capture against.

Both portals answer this identically — same endpoint, same descriptor format, same single-use
`eikoocnekot` token cookie. What differs between them is org configuration, and that lives in
`portals.py` as a `Portal` profile: base URL, ad-unit map, and whether `openCase` is mapped.

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
