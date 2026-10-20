# Walmart Connect portal

`--portal walmart`. The only portal that takes `--platform`, `--field`, and `cases close`.

## Statuses

| Status | Meaning | Clock |
| --- | --- | --- |
| `Need Info` | On us | **Auto-resolves after 3 days**; any reply clears it |
| `Needs Info - Internal` | With Walmart engineering | None |
| `WIP` | In progress after our reply | None |
| `Ready for Review` | Not yet established | Unknown |
| `Closed` | Terminal | — |

- `Need Info` may follow a real "fixed, please retest" or queue automation. Read the message above
  it to tell which; reply if the case still matters.
- `--status "need info"` matches both `Need Info` and `Needs Info - Internal`.
- The list holds cases since Sep 2025. Older cases return only through the portal's own
  historical-cases request.
- Recount before bulk triage:

```bash
walmart-support --json --portal walmart cases list | jq -r '.[].status' | sort | uniq -c | sort -rn
```

## Filing

One case per issue. Run `categories list --platform <platform>` for the category and issue names.

### `--platform`

Choose by the product the failing call belongs to, not by who reported it.

| Signal | `--platform` |
| --- | --- |
| `/service/display/…`, display creatives, Moderation Assist | `display` |
| `/service/WAP…`, campaigns, `sba_profile_image_upload`, Sponsored Brands and Videos | `sponsored-products` |
| brand shops, shelves | `shop-builder` |

`sponsored-brands` and `sponsored-videos` have no categories on a partner account. A Search case
shows Sponsored Products as its platform.

### `--issue` under `--category API`

| Situation | `--issue` | Form fields to fill |
| --- | --- | --- |
| One endpoint misbehaving | `Endpoint-Specific` | endpoint picklist; `Request / Response Details` and `Error Message` via `--field-file` |
| Rate limits | `Throttling` | `Endpoint`, `Rate Limit Issue Details`, `Issue Timestamp`; `--advertisers` required |
| Many endpoints failing at once (e.g. `520 no healthy upstream`) | `Other` | `--advertisers` required |
| Access or test data | `Production API Access`, `Sandbox API Access`, `Sandbox/Dummy Data Request` | none |

- The endpoint picklist is `SP Endpoint` on `sponsored-products` and `Display Onsite Endpoint` on
  `display`. It accepts only the paths the dry run lists; if the failing path is absent, pick the
  closest and name the real path in the subject and description.
- The advertiser id always comes from `--advertisers`, one id. Never pass it through `--field`.
- `Walmart Advertiser Name`, required on most non-API forms, goes through `--field`.
- Field labels match case-insensitively. An unknown label exits 2 and lists the valid ones.

### Subject and description

- Subject: product + endpoint + symptom + environment.
- Description: raw cURL request(s) and raw response(s), verbatim, then one line of expected
  behaviour. Use `assets/case-description.md`.
- Keep the request and response in the description **even when a form field repeats them**: no
  form field has yet been confirmed to land on a filed case.

## Closing

`cases close` moves a case to `Closed`, and the portal offers no reopen. Ask first. A zero exit is
the only proof; exit 1 means the status did not move.
