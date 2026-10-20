---
name: walmart-support
description: Read, answer, and file advertising support cases on the Walmart Connect and Sam's Club Advertising Help portals with the walmart-support CLI. Use when checking whether support replied on a case, triaging open cases, searching past cases for a Walmart or Sam's Club Ads API problem, filing a new case, attaching a HAR or payload to an existing one, closing one, or re-verifying a fix support says has shipped.
---

# Advertising Help support cases

`walmart-support` drives the Walmart Connect and Sam's Club Advertising Help portals from the
shell. `--help` on any command lists its flags; this file holds what the help text does not.

Read the reference for the portal you act on before filing, closing, or filtering by status — the
portals share only the `Closed` status, and filing and closing differ:

- `references/walmart.md` — statuses, filing playbook, form fields, `--platform`
- `references/samsclub.md` — statuses, categories, filing actions, closing
- `references/verifying-fix-claims.md` — proving a claimed Ads API fix shipped

## Invocation

- Check `walmart-support --version` first. If missing, prefix every command with `uvx`.
- `--portal` and `--json` are global: they go **before** the subcommand.
  `walmart-support --json --portal walmart cases list`, never `... cases list --json`.
- Name the portal you acted on in every report back.
- Prose goes through a file: `--description-file`, `--message-file`, `--field-file`. Never inline
  multi-line text on the command line.
- Filter output in the shell before it reaches the conversation:

```bash
walmart-support --json --portal walmart cases list --since 30d | jq -r '.[] | select(.status | test("Need")) | .case_number'
```

## Authentication

- Commands log in on their own and cache the session per portal. Nothing needs to run first.
- Credentials live only in the config file. A missing-config error is fixed there, not with
  environment variables.
- On `portal error:` (exit 1), run `auth check` once to tell credentials from portal.
- `authenticated False` usually means a stale cache: run `auth logout`, then rerun the original
  command. Rule this out before reporting the portal down. Never loop `auth check`.

## Reading

- `cases get` shows the case and a reply *count*; `cases replies` shows the conversation.
- Read the **whole** thread. Automated notices stack on top of real replies and bury them.
  `--latest N` only re-reads a message already known; never use it to learn what support said.
- `--from-support` drops our own messages. Separate human replies from automated ones.
- Retry failed reads freely. After a failed **write**, re-read the thread before retrying: the
  write may have landed.
- No reply for 3+ days is a stall. Report it with any unsent draft; whether to follow up is the
  user's call. Re-running the failing API is not a follow-up.

## Searching

- A plain `--query` searches the portal's abbreviated text and can miss terms. Use `--deep` when a
  search that should match does not.
- `--deep` costs one request per case. Narrow with `--status`/`--since` first; past 25 candidates
  it exits 2.
- Take `--status` values from the portal's reference. Never reuse a filter across portals.
- `--limit` saves requests only without other filters.

## Filing

1. Run `cases create` **without** `--submit`. It prints the payload and files nothing.
2. Read the last line, `would file at <portal> through <action>.` (`portal`/`action` under
   `--json`), and check both against what you meant.
3. Resolve every item under `the portal's form would reject this case` (`problems` under `--json`).
   `--submit` exits 2 until none remain. The portal does not enforce these itself: **never satisfy
   them with placeholder values.**
4. Show the payload to the user. On an explicit go-ahead, rerun the identical command with
   `--submit`.

The case body follows `assets/case-description.md`. Per-portal category, field and advertiser
rules are in the references.

## Replying, attaching, closing

- `cases reply` and `cases attach` have no dry run; they act at once.
- Keep a reply to a few lines: the ask and one or two facts. Never restate the case body or paste
  evidence tables.
- A reply is capped at **4000 characters after rendering** (measured on Walmart): text is HTML-escaped and each newline
  becomes `<br>`, so a blank line costs 8. Over the cap the CLI exits 2 before sending. A
  `STRING_TOO_LONG` portal error inserted nothing — trim and resend without re-reading.
- After `cases attach`, confirm the reported attachment count went up.
- `cases close` cannot be undone. Get the user's go-ahead first. Only exit 0 proves a close. On Sam's Club,
  follow `references/samsclub.md`.

## Before any outbound write

Applies to `cases create --submit`, `cases reply` and `cases attach`. Each needs the user's
explicit go-ahead; nothing sent can be recalled. Check the draft and every attachment against:

- **Keep** — advertiser, campaign and ad group ids, `wm_qos.correlation_id`, `wm_consumer.id`,
  tenant, timestamps, raw request and response bodies.
- **Redact** — `wm_sec.auth_signature` and `Authorization: Bearer` → `<redacted>`.
- **Remove** — internal ticket keys, scratch paths, internal table/pipeline/service names,
  colleagues, chat references, which customer escalated, how many are affected, roadmap, what the
  defect blocks. A HAR carries internal hosts and other advertisers' traffic.
- **Portal** — never post one retailer's identifiers onto the other retailer's case.

## Exit codes

- `0` — success.
- `1` — portal or network failure, or a close that left the status unmoved. Report it; do not
  retry blindly.
- `2` — refused before anything was written: usage, config, unknown portal or
  platform, empty or over-length reply, missing file, unsupported close, deep-search cap, unknown
  form field, or `--submit` with form problems. Fix the command, not the connection.
