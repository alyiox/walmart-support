# Walmart Connect portal

`--portal walmart`. The portal `--platform` applies to, and the only one `cases close` works
against.

## Status vocabulary

The three that carry a clock or an action:

| Portal status | Meaning | Clock |
| --- | --- | --- |
| `Need Info` | Nominally on you | **3-day auto-resolve.** Any reply clears it |
| `Needs Info - Internal` | With Walmart's own engineering | None |
| `WIP` | In progress after your reply | None |

`Need Info` may follow a real "we fixed it, please retest", or it may be queue automation — read the
message above it to tell which. Either way, reply to stop the clock if the case still matters.

Observed across 127 cases (Apr 2025 – Sep 2026): `Closed` 120, `Resolved` 3, `Needs Info -
Internal` 2, `WIP` 1, `Need Info` 1.

`Closed` is the **only** status shared with the Sam's Club portal — every status that marks a case
*open* differs, so a `--status` filter does not carry across. Re-run the count when triaging in
bulk, since the mix moves:

```bash
walmart-support --json --portal walmart cases list | jq -r '.[].status' | sort | uniq -c | sort -rn
```

`--status` matches on whole words, so `--status "need info"` catches both `Need Info` and
`Needs Info - Internal`, which are distinct portal statuses.

## Filing a case

One case per issue.

1. `walmart-support --portal walmart categories list --platform <platform>` — the `--category`
   and `--issue` names must match the portal's own dropdown exactly, and they differ per
   platform.
2. `walmart-support --portal walmart cases create ...` prints the payload and files **nothing**.
3. Show that payload to the user, then re-run the identical command with `--submit`.

Never add `--submit` to the first attempt. It files a new record into a real support queue.

Pick `--platform` by the product the failing call belongs to, not by who reported it:

| Signal | `--platform` |
| --- | --- |
| `/service/display/…`, display creatives, Moderation Assist | `display` |
| `/service/WAP…`, campaigns, `sba_profile_image_upload` | `sponsored-products` / `-brands` / `-videos`, or `sponsored-search` if unsure |
| brand shops, shelves | `shop-builder` |

Sponsored Search collapses onto Sponsored Products — that is the portal's own behaviour, and why a
Search case shows Sponsored Products in its Platform field.

`--issue`: **Endpoint-specific problem** for one endpoint misbehaving, **Outage / Downtime** when
many fail at once (e.g. `520 no healthy upstream`). Sandbox access, dummy data and throttling have
their own issues.

### What goes in the description

Raw cURL request(s) and raw response(s), verbatim, then one line stating the expected behaviour.
Support traces from the wire, so paraphrase nothing. `assets/case-description.md` is the skeleton.

Subject: product + endpoint + symptom + environment, concise. Advertiser ids go in `--advertisers`.
