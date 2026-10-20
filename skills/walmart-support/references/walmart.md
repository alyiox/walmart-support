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
| `Ready for Review` | Meaning not yet established; first seen after the October 2026 rebuild | Unknown |

`Need Info` may follow a real "we fixed it, please retest", or it may be queue automation — read the
message above it to tell which. Either way, reply to stop the clock if the case still matters.

Observed across the 86 cases the portal lists after its October 2026 rebuild (Sep 2025 – Oct 2026):
`Closed` 83, `Needs Info - Internal` 2, `Ready for Review` 1. Cases older than Sep 2025 are no
longer listed; the portal returns them only through its own historical-cases request.

`Closed` is the **only** status shared with the Sam's Club portal — every status that marks a case
*open* differs, so a `--status` filter does not carry across. Re-run the count when triaging in
bulk, since the mix moves:

```bash
walmart-support --json --portal walmart cases list | jq -r '.[].status' | sort | uniq -c | sort -rn
```

`--status "need info"` catches both `Need Info` and `Needs Info - Internal`, which are distinct
portal statuses with different clocks.

## Filing a case

One case per issue.

1. `walmart-support --portal walmart categories list --platform <platform>` — the `--category`
   and `--issue` names must match the portal's own dropdown, and they differ per platform.
2. `walmart-support --portal walmart cases create ...` prints the payload and files nothing. Read
   what it says the form would reject, and fill those fields with `--field`/`--field-file`.
3. Once the dry run reports no problems, show that payload to the user, then re-run the identical
   command with `--submit`.

Pick `--platform` by the product the failing call belongs to, not by who reported it:

| Signal | `--platform` |
| --- | --- |
| `/service/display/…`, display creatives, Moderation Assist | `display` (Onsite Display) |
| `/service/WAP…`, campaigns, `sba_profile_image_upload`, Sponsored Brands and Videos | `sponsored-products` |
| brand shops, shelves | `shop-builder` |

`sponsored-brands` and `sponsored-videos` are accepted but offer no categories on a partner
account; file Brands and Videos API problems under `sponsored-products`.

Sponsored Search collapses onto Sponsored Products — that is the portal's own behaviour, and why a
Search case shows Sponsored Products in its Platform field.

`--category API`, then `--issue`: **Endpoint-Specific** for one endpoint misbehaving,
**Throttling** for rate limits, **Other** when many fail at once (e.g. `520 no healthy upstream`) —
there is no outage issue. Production access, sandbox access and dummy data have their own issues.

What each API form asks for, by the label `--field` takes:

| `--issue` | Fill |
| --- | --- |
| Endpoint-Specific | the endpoint picklist — `SP Endpoint` on `sponsored-products`, `Display Onsite Endpoint` on `display`; `Request / Response Details` and `Error Message` as `--field-file` |
| Throttling | `Endpoint`, `Rate Limit Issue Details`, `Issue Timestamp`, and `--advertisers` |
| Other | `--advertisers` |

The endpoint picklist accepts only the paths the dry run lists. When the failing path is not one of
them, pick the closest and name the real path in the subject and description.

Most non-API forms also require `Walmart Advertiser Name`, typed with `--field`; the advertiser id
always comes from `--advertisers`, and takes **one** id.

### What goes in the description

Raw cURL request(s) and raw response(s), verbatim, then one line stating the expected behaviour.
Support traces from the wire, so paraphrase nothing. `assets/case-description.md` is the skeleton.
Keep them in the description even when a form field takes the same text: no form field has yet been
confirmed to land on a filed case.

Subject: product + endpoint + symptom + environment, concise. The advertiser id goes in
`--advertisers`.
