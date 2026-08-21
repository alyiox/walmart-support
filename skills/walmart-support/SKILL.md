---
name: walmart-support
description: Read, answer, and file Walmart Connect advertising support cases with the walmart-support CLI. Use when checking whether Walmart support replied on a case, triaging open cases, searching past cases for a Walmart Ads API problem, filing a new case, attaching a HAR or payload to an existing one, or closing one.
---

# Walmart Connect support cases

`walmart-support` drives the Advertising Help portal — a Salesforce Experience Cloud site with no
public API — from the shell. `walmart-support --help` and `walmart-support cases <command> --help`
carry the full flag list; this file covers only what the help text cannot tell you.

## Before anything

Run `walmart-support --version`. If the command is missing, `uvx walmart-support ...` runs every
command below without installing anything.

Credentials live in `~/.config/walmart-support/config.json`, or `WALMART_SUPPORT_USERNAME` /
`WALMART_SUPPORT_PASSWORD`. `walmart-support auth check` confirms the session; it exits 1 when it
cannot log in, which is a credential or portal problem — do not retry it in a loop.

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
them, so a raw thread is mostly repetition of what you already sent:

```bash
walmart-support cases replies 10000001 --from-walmart --latest 1
```

`cases get` prints the case and a reply *count*, not the conversation — use `cases replies` for
that. Each message is attributed `us` or `WALMART`.

## Searching

- `--query` alone matches the abbreviated subject and description that the list action returns.
  Good for a case number or a phrase you know is in the subject.
- `--deep` re-reads each candidate in full, replies included, at **one request per case**. Narrow
  with `--status`/`--since` first; past 25 candidates it refuses (exit 2) instead of issuing them.
- `--status` matches on whole words, so `--status "need info"` catches both `Need Info` and
  `Needs Info - Internal`, which are distinct portal statuses.
- `--limit` only shrinks the fetch when no filter is present. The portal applies it as a SOQL
  `LIMIT` before filtering, so with a filter it trims the result afterwards and saves nothing.

## Prose goes through a file, never the command line

`cases reply --message-file` and `cases create --description-file` exist so a case body never
passes through shell quoting. Write the text to a file first; do not inline multi-line prose, and
do not try to escape it.

## Filing a case

1. `walmart-support categories list --platform <platform>` — the `--category` and `--issue` names
   must match the portal's own dropdown, and they differ per platform.
2. `walmart-support cases create ...` prints the payload and files **nothing**.
3. Show that payload to the user, then re-run the identical command with `--submit`.

Never add `--submit` to the first attempt. It files a new record into a real support queue.

## Replying, attaching, closing

`cases reply` and `cases attach` act on a case that already exists, so they need no dry run.
`cases attach` uploads in chunks and reports the case's attachment count afterwards — check that
the number went up rather than assuming success.

`cases close` is one-way: the portal offers no reopen. Ask the user before closing.

## Exit codes

- `0` — success
- `1` — portal or network failure (expired session, timeout, portal error). Report it; do not retry
  blindly.
- `2` — usage, missing config, an empty reply, a missing attachment file, or the deep-search cap.
  Fix the command, not the connection.
