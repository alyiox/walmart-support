# Sam's Club portal

`--portal samsclub`. A separate Salesforce org from Walmart's, running the same app: same Aura
transport, same `AC_*` Apex controllers, same `Case_Category__c` schema.

Verified end to end by filing a real case through this CLI — create, attach, reply — and reading
each step back. Closing is the one thing that does not work; see below.

## No platform picker

`fetchChannels` returns an empty list and the contact form shows no platform selector. Every case
is filed against **Sponsored Products**. Passing `--platform` exits 2 rather than being ignored.

## Two filing actions, and only one of them is safe for API cases

API-category cases file through `saveApiCase` here, everything else through `openCase`, and the CLI
picks per category the way the portal's own form does. Only `saveApiCase` stores the subject and
body as written: this org's `openCase` HTML-escapes them **twice** before inserting the Case. It is
the portal's bug, no input avoids it, and `docs/portal-internals.md` has the evidence.

What that means in practice:

- **Case reads undo it**, and only where it was applied, so a case filed before this was understood
  reads back correctly and a case quoting `&quot;` in a code sample keeps its quoting.
- **A non-API case here is still stored escaped**, because `openCase` is the only method the org
  offers for it. Nothing is lost, but `cases list` shows the subject as the portal abbreviated it,
  cut from the escaped text and sometimes ending mid-entity. Use `cases get` for the real subject.
- **Support's own agents read the stored, mangled text.** Never quote our clean rendering back at
  them as evidence of what they received.

Routing is correct either way: the earlier CLI-filed case landed as `API-AdCases` / `Product
Related Questions`. The account is resolved through `getAdvertiserInfo` rather than the contact
record, which the CLI handles itself.

The reCAPTCHA on the portal's own form is a **client-side gate only** — it blocks the form's submit
button, not the Apex method.

## `--advertisers` reaches API cases only

On an API case the flag lands: `saveApiCase` takes the ids as a parameter of its own.

Under any other category it does not, because this org's `openCase` offers no field for them — its
Apex fills *Advertisers Affected* from the account name instead, which is what the first CLI-filed
case came back with. The CLI warns and drops the flag, so **put the ids in the description body**
for those categories; it is stored verbatim.

## `Onboard New API Advertiser` needs fields the CLI does not collect

That issue's own form asks for company name, associated brands, vendor name and billing contact,
and `saveApiCase` carries them in the same wrapper. The CLI sends them empty and marks the case
with the onboarding flag the form sets, so filing one from here produces a case support has to come
back and ask about. Use the portal UI for that issue.

## Closing is not supported

`walmart-support --portal samsclub cases close ...` exits 2 without contacting the portal.

`closeCaseSt` — the action that closes a Walmart case — resolves on this org, answers SUCCESS, and
returns the case with its status **untouched**. That is worse than an error: nothing distinguishes
it from a successful close except re-reading the case. `closeCase` and `closeCaseStatus` do not
exist here.

The portal's own **Close Case** button (case page → confirmation modal → OK) does work, and lands
the case on **`Canceled`**, not `Closed`. That is where this org's `Canceled` cases come from. Ask
the user to close from the UI, and note that the portal warns closing prevents further comments.

## Status vocabulary

Distinct from Walmart's: **`Closed` is the only name the two portals share**, and every status
marking a case *open* differs — so never carry a Walmart status filter across. Observed across 400
cases (Oct 2022 – Sep 2026):

| Status | Share | Reading |
| --- | --- | --- |
| `Closed` | 364 | Terminal |
| `Canceled` | 17 | Terminal — what the UI's Close Case produces |
| `Pending Review` | 8 | Filed, not yet triaged |
| `New` | 3 | Filed, not yet triaged |
| `In Progress` | 3 | Being worked |
| `Reviewed` | 2 | Triaged |
| `Add Brands` | 2 | Request-specific |
| `On Hold` | 1 | Parked |

No `Need Info` equivalent appeared, so the 3-day auto-resolve clock that governs Walmart cases has
no observed counterpart here. Do not assume one either way without checking a live case.

Re-run the count when triaging in bulk, since the mix moves:

```bash
walmart-support --portal samsclub --json cases list | jq -r '.[].status' | sort | uniq -c | sort -rn
```

Case numbers are zero-padded and 8 digits (`00010002`), not Walmart's bare `10000001`.

## Categories

Nine level-1 categories, from the live portal.
`walmart-support --portal samsclub categories list` re-reads them.

| Category | Issues |
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

`API` writes `API-AdCases` to the case, the same backend value Walmart uses — and is the one
level-1 category that files through `saveApiCase` rather than `openCase`.

The two orgs' `openCase` methods do not even take the same parameters, which is why the CLI keeps a
per-portal payload; the lists are in `docs/portal-internals.md`.

## The detail field set varies by category

`getCaseData` returns different fields depending on the case's category, so a blank column is not
evidence of anything. An API case carries `Issue_Category__c`, `Sub_Category_1__c`,
`Advertisers_Affected__c` and `Sponsored_Ad_Contact_{Name,Email}__c`. An onboarding case
carries none of those and instead has `Account_Admin_*`, `Company_Name_Supplier__c`,
`Agency_Name__c` and `Vendor_Number__c`, leaving `cases get` to render *issue category* and *sub
category* empty — exactly as the portal's own UI does for that case.

Read `additional_fields` rather than assuming a fixed shape; Sam's populates it heavily.
