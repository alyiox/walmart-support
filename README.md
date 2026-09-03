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

The two are separate Salesforce orgs running the same custom app: same `AC_*` Apex controllers
taking the same parameters, same `Case_Category__c` schema. So this is one client pointed at two
tenants, not two clients. `--portal` picks one.

## Status

**Walmart** — working end to end: authentication, listing and reading cases, replying, attaching
files, closing, and filing new cases. Each Aura payload was captured from the portal UI and verified
against a real case, so treat behaviour outside the documented commands as unmapped rather than
unsupported.

**Sam's Club** — verified end to end by filing a real case through this CLI: create, attach and
reply, reading each step back. Two divergences worth knowing:

* **API cases file through a different Apex method there** — `saveApiCase`, which is what the
  portal's own form uses. Its `openCase` stores the subject and body HTML-escaped twice, so the two
  are not interchangeable. Case reads undo that escaping where it was applied, so cases filed
  before this was understood still read correctly.
* `--advertisers` reaches **API cases only**. Under any other category the org declares no field
  for the ids, so the CLI warns and drops them; put them in the description body.
* **`cases close` is refused.** `closeCaseSt` resolves there, answers SUCCESS, and leaves the status
  untouched — indistinguishable from a real close without re-reading. Closing is a UI action on that
  portal, and it lands the case on `Canceled` rather than `Closed`.

The mechanics are in [docs/portal-internals.md](docs/portal-internals.md); the day-to-day rules are in
`skills/walmart-support/references/samsclub.md`.

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
every command.

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

The CLI only uploads: removing an attachment is a portal-page action, because
the id an upload returns is not the id the portal's delete actions want.
Attaching *while filing* is not supported yet. Both are portal-side quirks,
described in [docs/portal-internals.md](docs/portal-internals.md).

## Filing a case

Works on both portals, with the `--advertisers` caveat noted under Status for
Sam's Club.

`cases create` prints the exact payload it would send, and the action it would
send it to, and files nothing unless `--submit` is given. That default is
deliberate: a case goes to a real support queue, and a mis-mapped category files
a real but misrouted one.

Categories are resolved by name against the portal's own dropdown data rather
than hardcoded, so `categories list` shows exactly what the UI offers and both
the UI label ("API Support") and the wizard's internal name ("API") match.

`--platform` covers Display and Sponsored Search; the portal collapses Search
onto its "Sponsored Products" ad unit, which is why a Search case shows
Sponsored Products as its platform. Sponsored Brands and Videos are accepted
but the portal publishes no categories for them on a partner account, and the
error says so. Sam's Club publishes no ad-unit channels at all, so it has no
platform picker and `--platform` is refused there rather than ignored.

The action answers with the new case's number. The Walmart path is verified end
to end — filed, replied to and closed — and so is `openCase` on Sam's Club, but
no API case has yet been filed there through `saveApiCase` from this client, so
treat the first one as the proof.

The parameter mapping and the traps behind it are in [docs/portal-internals.md](docs/portal-internals.md),
including one unconfirmed defect in how the portal resolves the additional form
fields. Either way, **put anything that matters in the description body**: it is
stored verbatim, whereas those fields are not reliably addressable.

### Closing and replying

```bash
walmart-support --portal walmart cases reply 10000004 --message-file answer.txt
walmart-support --portal walmart cases close 10000004   # New -> Closed
```

`cases close` verifies the status actually moved and exits 1 if it did not, so
a zero exit is the only evidence that a case really closed. It is Walmart-only;
on Sam's Club it exits 2 without contacting the portal.

Why it is refused on Sam's Club, and what implementing it there would take, is
in [docs/portal-internals.md](docs/portal-internals.md).

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

Authentication goes through the classic Salesforce login form, which is the one step that is not
Aura. Everything else is one POST to `/s/sfsites/aura` per action, carrying a framework context
scraped on each run because it rotates with every Salesforce release. That transport is `aura.py`,
and it is the one module to re-capture against when the contract shifts.

Both portals answer it identically. What differs between them is org configuration, held in
`portals.py` as a `Portal` profile: base URL, ad-unit map, which Apex method files a case, and
whether `closeCaseSt` really closes.

The endpoints, the action-descriptor format, the single-use token cookie, each org's parameter
lists and the traps found by getting them wrong are in [docs/portal-internals.md](docs/portal-internals.md).

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

[LICENSE](MIT)
