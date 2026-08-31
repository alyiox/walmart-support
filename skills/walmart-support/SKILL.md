---
name: walmart-support
description: Read, answer, and file Walmart Connect advertising support cases with the walmart-support CLI. Use when checking whether Walmart support replied on a case, triaging open cases, searching past cases for a Walmart Ads API problem, filing a new case, attaching a HAR or payload to an existing one, closing one, or re-verifying a fix Walmart says has shipped.
---

# Walmart Connect support cases

`walmart-support` drives the Advertising Help portal — a Salesforce Experience Cloud site with no
public API — from the shell. `walmart-support --help` and `walmart-support cases <command> --help`
carry the full flag list; this file covers only what the help text cannot tell you.

## Before anything

Run `walmart-support --version`. If the command is missing, `uvx walmart-support ...` runs every
command below without installing anything.

Credentials live in `~/.config/walmart-support/config.json`, or `WALMART_SUPPORT_USERNAME` /
`WALMART_SUPPORT_PASSWORD`. Every command logs in on its own and caches the session, so there is
nothing to run first. When a command exits 1 with `portal error:`, `walmart-support auth check`
tells you whether the credentials or the portal is at fault: a refused login is reported with the
portal's own wording, under `error` in `--json`. It is a diagnostic, not a prerequisite, and
retrying it in a loop will not fix a rejected login.

`authenticated False` from that check is usually a **stale cached session, not bad credentials** —
`walmart-support auth logout` and re-running the original command clears it. There is no
`auth login`; every command logs in on its own. Rule this out before reporting the portal as down.

## `--json` is global, so it goes first

```bash
walmart-support --json cases list --since 30d   # correct
walmart-support cases list --json               # error: unrecognized arguments
```

## Keep case text out of the conversation

An unfiltered `cases list` can run to hundreds of rows, and a case body is long. Filter in the
shell before the output reaches you:

```bash
walmart-support --json cases list --since 30d | jq -r '.[] | select(.status | test("Need")) | .case_number'
```

## Reading a thread

Support's acknowledgement quotes the whole case body back, and later replies quote the ones before
them, so a raw thread is mostly repetition of what you already sent. `--from-walmart` drops your own
messages:

```bash
walmart-support cases replies 10000001 --from-walmart
```

**Read the whole thread; never narrow it to the newest message.** Automated queue notices stack on
top of real replies — one landed five minutes after a deployed-fix announcement and buried it for a
day. `--latest N` is for re-reading a message you already know about, never for finding out what
Walmart said. Separate the human replies from the automated ones as you read.

`cases get` prints the case and a reply *count*, not the conversation — use `cases replies` for
that. Each message is attributed `us` or `WALMART`.

Reads are cheap and time out often, so retry them freely. After a failed **write** (`cases reply`,
`cases attach`), re-read the thread before retrying — the write may have landed, and a blind retry
double-posts.

## Searching

- `--query` alone matches the abbreviated subject and description that the list action returns.
  Good for a case number or a phrase you know is in the subject.
- `--deep` re-reads each candidate in full, replies included, at **one request per case**. Narrow
  with `--status`/`--since` first; past 25 candidates it refuses (exit 2) instead of issuing them.
- `--status` matches on whole words, so `--status "need info"` catches both `Need Info` and
  `Needs Info - Internal`, which are distinct portal statuses.
- `--limit` only shrinks the fetch when no filter is present. The portal applies it as a SOQL
  `LIMIT` before filtering, so with a filter it trims the result afterwards and saves nothing.

## Case status vocabulary

| Portal status | Meaning | Clock |
| --- | --- | --- |
| `Need Info` | Nominally on you | **3-day auto-resolve.** Any reply clears it |
| `Needs Info - Internal` | With Walmart's own engineering | None |
| `WIP` | In progress after your reply | None |

`Need Info` may follow a real "we fixed it, please retest", or it may be queue automation — read the
message above it to tell which. Either way, reply to stop the clock if the case still matters.

## Prose goes through a file, never the command line

`cases reply --message-file` and `cases create --description-file` exist so a case body never
passes through shell quoting. Write the text to a file first; do not inline multi-line prose, and
do not try to escape it.

## Filing a case

One case per issue.

1. `walmart-support categories list --platform <platform>` — the `--category` and `--issue` names
   must match the portal's own dropdown exactly, and they differ per platform.
2. `walmart-support cases create ...` prints the payload and files **nothing**.
3. Show that payload to the user, then re-run the identical command with `--submit`.

Never add `--submit` to the first attempt. It files a new record into a real support queue.

Pick `--platform` by the product the failing call belongs to, not by who reported it:

| Signal | `--platform` |
| --- | --- |
| `/service/display/…`, display creatives, Moderation Assist | `display` |
| `/service/WAP…`, campaigns, `sba_profile_image_upload` | `sponsored-products` / `-brands` / `-videos`, or `sponsored-search` if unsure |
| brand shops, shelves | `shop-builder` |

`--issue`: **Endpoint-specific problem** for one endpoint misbehaving, **Outage / Downtime** when
many fail at once (e.g. `520 no healthy upstream`). Sandbox access, dummy data and throttling have
their own issues.

### What goes in the description

Raw cURL request(s) and raw response(s), verbatim, then one line stating the expected behaviour.
Support traces from the wire, so paraphrase nothing.

Subject: product + endpoint + symptom + environment, concise. Advertiser ids go in `--advertisers`.

The portal is reachable in a browser too, but there is no reason to drive it: `cases create` takes
every field the form has, without a manual login.

## Replying, attaching, closing

`cases reply` and `cases attach` act on a case that already exists, so they need no dry run.
`cases attach` uploads in chunks and reports the case's attachment count afterwards — check that
the number went up rather than assuming success.

Keep a reply to a few lines: the ask, one or two concrete facts, done. No evidence tables and no
restating the case body — support already has it, and the supporting detail belongs in your own
records, not in the thread.

`cases close` is one-way: the portal offers no reopen. Ask the user before closing.

## Before any outbound write

Binding on `cases create --submit`, `cases reply` and `cases attach`. Each needs an explicit
go-ahead from the user, and a sent message cannot be recalled. Drafts get assembled from internal
notes, so strip them deliberately: re-read the draft against these three lists before it goes.

**Stays** — the Walmart-side identifiers support cannot trace without: advertiser, campaign and ad
group ids, `wm_qos.correlation_id`, `wm_consumer.id`, tenant, timestamps, and the raw request and
response bodies.

**Redacted** — `wm_sec.auth_signature` and `Authorization: Bearer` → `<redacted>`.

**Goes** — anything of your own side the case does not need: internal ticket keys and scratch
paths, internal table, pipeline and service names, colleagues and chat references, which customer
is escalating or how many are affected, your roadmap and what the defect blocks. Keep this list
concrete for your own team — a vague one does not survive contact with a real draft.

Attachments take the same pass. A HAR carries internal hosts and other advertisers' traffic.

## Re-verifying a fix claim

Re-run the repro when Walmart's newest reply demands it: they claim a fix, ask for a retest, or ask
something factual you cannot answer from the record. A quiet "did they reply?" poll does not earn a
repro run — carry the previous verification date forward rather than implying a recheck that never
happened.

- **Fix claims are per-environment and per-variant.** Test the environment the case names, say which
  one you tested, and ask about the other. Re-check every branch the case covered, not just the one
  they mention.
- **`lastUpdatedDate` unchanged** proves nothing shipped to that environment. Changed without a save
  on your side means a server-side batch fix.
- **Prefer non-terminal entities as witnesses.** A COMPLETED ad group or an ARCHIVED audience lets
  the platform dismiss the report. Read the expiry off the **ad group's** own `endDate`, never the
  campaign's, and swap in a fresh witness while drafting the follow-up that cites it.

## Exit codes

- `0` — success
- `1` — portal or network failure (expired session, timeout, portal error). Report it; do not retry
  blindly.
- `2` — usage, missing config, an empty reply, a missing attachment file, or the deep-search cap.
  Fix the command, not the connection.
