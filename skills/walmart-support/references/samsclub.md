# Sam's Club portal

`--portal samsclub`. No platform picker, no form fields, no `cases close`: `--platform`,
`--field` and `cases close` all exit 2.

## Statuses

| Status | Reading |
| --- | --- |
| `Pending Review`, `New` | Filed, not yet triaged |
| `Reviewed` | Triaged |
| `In Progress` | Being worked |
| `Add Brands` | Request-specific |
| `On Hold` | Parked |
| `Closed` | Terminal |
| `Canceled` | Terminal — what the portal's own Close Case button produces |

- No `Need Info` status and no observed auto-resolve clock. Never assume either way without
  checking a live case.
- Case numbers are 8 digits, zero-padded (`00010002`).
- Recount before bulk triage:

```bash
walmart-support --json --portal samsclub cases list | jq -r '.[].status' | sort | uniq -c | sort -rn
```

## Filing

Every case files against Sponsored Products. The default `--issue` does not exist here: always
pass one.

| `--category` | Issues |
| --- | --- |
| `API` | API User Permissions, API Client Set-up, Technical Troubleshooting, Onboard New API Advertiser, Product Related Questions, Report a Bug, Report an Outage, Other |
| `Access` | Add User, Remove User, Other |
| `Set-up` | Campaign Setup, Adgroup Setup, Can't find item, Keyword related, Other |
| `Optimization` | Item issue, Cannot find my campaign, Campaign is not spending, Campaign is not performing, Other |
| `Reporting` | Incorrect Reporting Data, Cannot View Report, Reporting Data Issue, Missing data, On demand report related, Campaign tab related, Item health report related, Dashboard tab related, Other |
| `Billing` | Invoice request, Dispute, Other |
| `Password or Other Login Issue` | Cannot Login to Sponsored Ads Platform, Other |
| `Feature Request/Feedback` | Access, Set-up, Optimization, Reporting, Billing, API, Other |
| `Problem Area Not Listed` | (none) |

`categories list` re-reads the live tree.

### The action decides how the text is stored

| `--category` | Preview says | Subject and body stored |
| --- | --- | --- |
| `API` | `through saveApiCase` | as written |
| anything else | `through openCase` | **HTML-escaped twice** |

- If an API case previews `through openCase`, fix `--category` before submitting.
- `cases get` and `cases replies` undo the escaping; `cases list` shows the escaped, abbreviated
  subject. Use `cases get` for the real one.
- Support's agents read the stored, escaped text. Never quote our rendering to them as what they
  received.

### Advertisers

- `--advertisers` reaches **API cases only**. Elsewhere the CLI warns and drops it: put the ids in
  the description body.
- `Onboard New API Advertiser` needs company, brand, vendor and billing details the CLI does not
  collect. File that issue from the portal UI.

## Closing

Unsupported from the CLI. Ask the user to close from the portal: case page → **Close Case** →
confirm. The case lands on `Canceled`, not `Closed`, and accepts no further comments.

## Reading

`getCaseData` returns a different field set per category. A blank `issue category` on an
onboarding case is expected; read `additional_fields` instead of assuming a fixed shape.
