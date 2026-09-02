---
name: walmart-support
description: Read, answer, and file advertising support cases on the Walmart Connect and Sam's Club Advertising Help portals with the walmart-support CLI. Use when checking whether support replied on a case, triaging open cases, searching past cases for a Walmart or Sam's Club Ads API problem, filing a new case, attaching a HAR or payload to an existing one, closing one, or re-verifying a fix support says has shipped.
---

# Advertising Help support cases

`walmart-support` drives the Advertising Help portals — Salesforce Experience Cloud sites with no
public API — from the shell. `walmart-support --help` and `walmart-support cases <command> --help`
carry the full flag list; this file covers only what the help text cannot tell you.

Walmart Connect and Sam's Club are two Salesforce orgs running the same app, so every command here
works against either. What genuinely differs per portal lives in a reference file — read the one for
the portal you are working on, because **the status vocabularies overlap only on `Closed`** and only
Walmart can close a case:

- `references/walmart.md` — status vocabulary, filing playbook, `--platform` selection
- `references/samsclub.md` — status vocabulary, category tree, why filing is refused
- `references/verifying-fix-claims.md` — proving a claimed Ads API fix actually shipped

## Before anything

Run `walmart-support --version`. If the command is missing, `uvx walmart-support ...` runs every
command below without installing anything.

Credentials live in `~/.config/walmart-support/config.json`, one section per portal — see
`config.example.json`. That file is the only source; there is no environment fallback, so which
credentials a command used is always answerable by reading one path. `--config` points elsewhere.

Every command logs in on its own and caches the session per portal, so there is nothing to run
first. When a command exits 1 with `portal error:`, `walmart-support auth check` tells you whether
the credentials or the portal is at fault: a refused login is reported with the portal's own
wording, under `error` in `--json`. It is a diagnostic, not a prerequisite, and retrying it in a
loop will not fix a rejected login.

`authenticated False` from that check is usually a **stale cached session, not bad credentials** —
`walmart-support auth logout` and re-running the original command clears it. There is no
`auth login`; every command logs in on its own. Rule this out before reporting the portal as down.

## Choosing a portal

`--portal walmart` or `--portal samsclub`, before the subcommand. Without it the config's
`default.portal` decides, and without that, Walmart.

```bash
walmart-support cases list                        # Walmart, via the default
walmart-support --portal samsclub cases list      # Sam's Club
```

Each portal caches its own session, keyed by host, so switching does not force a re-login. The
default is silent, so **say which portal you acted on** when reporting back — the write commands
name it in their own output, and `--json` carries a `portal` field.

## `--json` is global, so it goes first

```bash
walmart-support --json cases list --since 30d   # correct
walmart-support cases list --json               # error: unrecognized arguments
```

`--portal` is global too, so it goes in the same place.

## Keep case text out of the conversation

An unfiltered `cases list` can run to hundreds of rows, and a case body is long. Filter in the
shell before the output reaches you:

```bash
walmart-support --json cases list --since 30d | jq -r '.[] | select(.status | test("Need")) | .case_number'
```

## Reading a thread

Support's acknowledgement quotes the whole case body back, and later replies quote the ones before
them, so a raw thread is mostly repetition of what you already sent. `--from-support` drops your own
messages:

```bash
walmart-support cases replies 15957474 --from-support
```

**Read the whole thread; never narrow it to the newest message.** Automated queue notices stack on
top of real replies — one landed five minutes after a deployed-fix announcement and buried it for a
day. `--latest N` is for re-reading a message you already know about, never for finding out what
support said. Separate the human replies from the automated ones as you read.

`cases get` prints the case and a reply *count*, not the conversation — use `cases replies` for
that. Each message is attributed `us` or the portal's own name; under `--json` the other side is
always `support`, never a retailer name.

Reads are cheap and time out often, so retry them freely. After a failed **write** (`cases reply`,
`cases attach`), re-read the thread before retrying — the write may have landed, and a blind retry
double-posts.

## Searching

- `--query` alone matches the abbreviated subject and description that the list action returns.
  Good for a case number or a phrase you know is in the subject.
- `--deep` re-reads each candidate in full, replies included, at **one request per case**. Narrow
  with `--status`/`--since` first; past 25 candidates it refuses (exit 2) instead of issuing them.
- `--status` matches on whole words, not substrings. The vocabulary is per-portal and overlaps only
  on `Closed`, so take it from the portal's reference file rather than reusing a filter.
- `--limit` only shrinks the fetch when no filter is present. The portal applies it as a SOQL
  `LIMIT` before filtering, so with a filter it trims the result afterwards and saves nothing.

## Prose goes through a file, never the command line

`cases reply --message-file` and `cases create --description-file` exist so a case body never
passes through shell quoting. Write the text to a file first; do not inline multi-line prose, and
do not try to escape it.

## Filing a case

Works on both portals. The full Walmart playbook — category and issue selection, `--platform`, what
belongs in the description — is in `references/walmart.md`, and `assets/case-description.md` is the
skeleton for the body.

The rule worth repeating here: `cases create` prints its payload and files **nothing** until you
re-run the identical command with `--submit`. Never put `--submit` on the first attempt, and show
the payload to the user before you do.

On Sam's Club, `--advertisers` never reaches the portal — no category there declares form fields, so
the ids are dropped and the CLI warns. Put them in the description body instead; see
`references/samsclub.md`.

## Replying, attaching, closing

Available on both portals. `cases reply` and `cases attach` act on a case that already exists, so
they need no dry run. `cases attach` uploads in chunks and reports the case's attachment count
afterwards — check that the number went up rather than assuming success.

Keep a reply to a few lines: the ask, one or two concrete facts, done. No evidence tables and no
restating the case body — support already has it, and the supporting detail belongs in your own
records, not in the thread.

**A reply body is capped at 4000 characters, counted after HTML rendering** — the portal escapes
the text and turns every newline into `<br>`, so a blank line between paragraphs costs 8, not 2.
`cases reply` measures the rendered length and refuses over the cap (exit 2) before contacting the
portal. If one gets through, the portal answers `portal error: ... STRING_TOO_LONG ...` and
**inserts nothing** — the one failed write that cannot have landed, so trim and resend without
re-reading the thread. The figure was measured against Walmart; Sam's Club runs the same Apex class
but has not been confirmed.

`cases close` is **Walmart only**, and one-way there: the portal offers no reopen, so ask the user
before closing. On Sam's Club it exits 2 — `closeCaseSt` resolves but leaves the status untouched,
so the CLI refuses rather than report a close that did not happen. Closing there is a UI action that
lands the case on `Canceled`; see `references/samsclub.md`.

Even on Walmart, `cases close` now checks that the status actually moved and exits 1 if it did not,
so treat a zero exit as the only evidence of a close.

## Before any outbound write

Binding on `cases create --submit`, `cases reply` and `cases attach`. Each needs an explicit
go-ahead from the user, and a sent message cannot be recalled. Drafts get assembled from internal
notes, so strip them deliberately: re-read the draft against these three lists before it goes.

**Stays** — the retailer-side identifiers support cannot trace without: advertiser, campaign and ad
group ids, `wm_qos.correlation_id`, `wm_consumer.id`, tenant, timestamps, and the raw request and
response bodies.

**Redacted** — `wm_sec.auth_signature` and `Authorization: Bearer` → `<redacted>`.

**Goes** — anything of your own side the case does not need: internal ticket keys and scratch
paths, internal table, pipeline and service names, colleagues and chat references, which customer
is escalating or how many are affected, your roadmap and what the defect blocks. Keep this list
concrete for your own team — a vague one does not survive contact with a real draft.

Attachments take the same pass. A HAR carries internal hosts and other advertisers' traffic.

Check the portal too. Posting a Walmart repro onto a Sam's Club case sends one retailer's
identifiers into another's support queue.

## Exit codes

- `0` — success
- `1` — portal or network failure (expired session, timeout, portal error), or a close the portal
  accepted without moving the status. Report it; do not retry blindly.
- `2` — usage, missing config, an unknown portal, a platform the portal does not offer, an empty or
  over-length reply, a missing attachment file, closing on a portal that does not support it, or
  the deep-search cap. Fix the command, not the connection.
