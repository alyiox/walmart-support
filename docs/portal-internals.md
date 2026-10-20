# Portal internals

What was recovered by observing the two Advertising Help portals, and the traps found by getting
it wrong. None of this is needed to *use* the CLI — the README covers that, and
`skills/walmart-support/` covers how to work a case. This file is the record of how the portals
actually behave, so the next person does not have to re-derive it.

Where a fact explains why a module is shaped the way it is, the module's own docstring says so;
this file stays on the portals' side of the line.

## The wire

```
POST /login                         classic login form -> frontdoor.jsp -> session cookies
GET  /s/contact                     scrape the Aura context (fwuid, apck, lrmc) and CSRF token
POST /s/sfsites/aura?r=N&other....  call @AuraEnabled Apex methods through ApexActionController
```

Authentication is the one step that does *not* go through Aura: the portal still serves the classic
Salesforce login form, and the redirect it answers with is what mints the session cookies.

Aura rejects any call whose framework context does not match the deployed build, and that context
rotates with every Salesforce release, so it is scraped on each run and never hardcoded. All of it
lives in `aura.py`; when the contract shifts, that is the one module to re-capture against.

Three details are not guessable:

- An action's descriptor is `apex://<Component>Controller/ACTION$<method>`, while the query-string
  hint drops the `Controller` suffix. The case list is `other.AC_Activity.getCasesForCommunityUser`
  on the wire and `apex://AC_ActivityController/ACTION$getCasesForCommunityUser` inside.
- Only a minimal slice of the page's framework context is echoed back.
- `aura.token` is mandatory and arrives as a **single-use cookie**. The page HTML names it in
  `eikoocnekot` ("tokencookie" reversed), the browser's JS reads it and deletes it, and the name
  changes per response — so the token must be read from the very response that named it.

Both portals answer this identically: same endpoint, same descriptor format, same token cookie.
What differs is org configuration, and that lives in `portals.py` as a `Portal` profile.

A component's own definition is readable without a browser, which is how the differences below
were established rather than guessed:

```
aura://ComponentController/ACTION$getComponentDef   name=markup://c:AC_OpenCase
```

It returns the component's declared server actions with their parameter lists, plus the controller
JavaScript, which is what the form itself runs.

### TLS chain

`advertisinghelp.samsclub.com` sends its leaf certificate without the intermediate,
`GlobalSign GCC R46 OV TLS CA 2025`. Browsers fetch the missing issuer themselves; OpenSSL does
not, so curl and httpx fail with `unable to get local issuer certificate`. Walmart's portal sends
the full chain. The intermediate is bundled in `src/walmart_support/ca-certificates/`, one `.crt` per
certificate, and trusted alongside certifi's roots; replace it there if Sam's moves to another
issuer.

## Walmart's October 2026 rebuild

Walmart moved its portal onto a new Salesforce org in mid-October 2026. The hostname, login, Aura
transport and the `AC_*` controller names stayed; the case form and the case records did not.

- **Cases were migrated.** The list returns only cases created since 2025-09-04; older ones are
  "historical" and come back through a migration request (`AC_CaseMigrationRequestForm`, backed
  by `GCRM_MigrationRequestController.submitMigrationRequestFlat(requestType, fromDateStr,
  toDateStr, caseIds)`). The CLI does not file those requests.
- **A migrated case has two numbers.** `getCaseData` answers `displayCaseNumber` — the old number,
  the one support's emails quote and the list shows — and `detail.CaseNumber`, the new org's own
  (`00035294` against `16639778`). The detail lookup accepts the old one.
- **A migrated case's category moved.** `Issue_Category__c` and `Sub_Category_1__c` hold the
  placeholders `GCRM Issue Category` / `GCRM Sub Category`; the real values are on
  `GCRM_Issue_category__c` / `GCRM_Sub_category_1__c`.
- `c:AC_CaseDetail` no longer resolves as a component, but `AC_CaseDetailController`'s
  `getCaseData`, `saveCaseComment` and `closeCaseSt` still do, with the same parameters. A
  malformed id draws `Invalid id`, not a dropped action, which is how that was checked without
  writing anything.
- A new status, `Ready for Review`, appeared on the first post-rebuild case.

## Filing a case

**The two orgs do not file through the same method, and it is not interchangeable.**

| | Walmart Connect | Sam's Club |
| --- | --- | --- |
| Category tree | `getCategoryTree` | `getCaseCategoriesNew` |
| Issue form | `getFormForConfig` | none |
| API-category cases | `openCase` | `saveApiCase` |
| Everything else | `openCase` | `openCase` |
| `openCase` parameters | 19 | 19, a different 19 |
| Escapes subject/body | not re-measured since the rebuild | **twice, in `openCase` only** |

### Walmart's form

`getCategoryTree(platform, userType, auth)` returns nested nodes —
`{id, label, configId, caseRecordType, authentication, children}`. `auth` is the literal
`Authenticated`; `platform` is the ad unit, except that the category selector renames three before
asking: `Display` → `Onsite Display`, `ShopBuilder` → `BrandShops`, `Store Ads` → `In-Store Ads`.
On an `API Partner` account, Sponsored Products, Onsite Display, BrandShops and Platform Services
return a tree; Sponsored Brands, Sponsored Videos, Offsite Display and In-Store Ads return `[]`.
Sponsored Products' own `Campaign Set-Up` holds the Brands and Videos enablement issue.

A node has one name, and the form writes it twice: `problemDomain` is the level-1 label and
`subDomain` the level-2 label (`API` / `Endpoint-Specific`). There is no separate backend name.

The issue's `configId` goes to `getFormForConfig(configId)`, which answers `{config, fields}`.
`config.Form_Template__c` is sent back as `formTemplateId`; `config.Disable_Submission__c` closes
the issue to new cases. Each field carries `label`, `caseField` (the Case field it lands in),
`fieldType`, `required` (`Always` or `Never`), `options`, `customUi`, `fieldGroup`,
`dependsOnFieldId`/`dependsOnValue` and a `validationPattern`. The form deduplicates fields on
label, `caseField` and type.

The form sends **every** field back in `additionalFieldsString`, as a JSON list of
`{title, required, value, fieldName, fieldType, options, helpText, validations,
validationErrorMessage, skip}` — `fieldName` is the `caseField`, `fieldType` is translated back to
the older names (`Picklist` → `SinglePicklist`, `MultiPicklist` → `MultiSelectPicklist`,
`Radio` → `RadioGroup`, `File` → `File Upload`), and a field whose dependency is unmet goes out
with an empty value. `skip` is true for a `customUi` field, the description, and the advertiser
group; false for a read-only one; otherwise true when the field binds no Case field and is not a
file.

The advertiser group (`fieldGroup` `Advertiser`) is drawn as a picker fed by
`getAdvertisersForAccount(accountId)`. An `API Partner` account has no `Advertiser__c` records, so
the picker falls back to two typed inputs, `Walmart Advertiser Name` and
`Walmart Ad Center Advertiser ID`; the id is also sent as `openCase`'s own `advertiserId`. Two
forms declare the id outside the group, as a plain text field.

**Required fields are enforced only in the browser.** An `openCase` with every required form field
blank reached the insert and failed there on an oversized subject alone, so the portal would have
filed it. The CLI makes the form's checks itself.

The API issues on Sponsored Products and Onsite Display, and what each form requires besides the
description (`*` required):

| Issue | Fields |
| --- | --- |
| Endpoint-Specific | advertiser id, endpoint picklist\*, `Request / Response Details`\*, `Error Message`\*, screenshot |
| Throttling | advertiser id\*, `Endpoint`\*, `Rate Limit Issue Details`\*, `Issue Timestamp`\*, screenshot |
| Production API Access, Sandbox API Access, Sandbox/Dummy Data Request | advertiser id, screenshot |
| Other | advertiser id\*, screenshot |

The endpoint picklist is labelled `SP Endpoint` on Sponsored Products and
`Display Onsite Endpoint` on Onsite Display, and offers a fixed list of paths (18 and 27); a path
not on the list cannot be chosen.

### Sam's Club

Sam's `AC_OpenCase.doInit` sets an `isApiCase` flag when the selected category's parent label is
`API`, and `createCase` branches on it to `saveApiCase(NewAdvertiserWrapper advWrapper)`. Walmart's
build of the same component has no such branch and declares no `saveApiCase` at all.

The full parameter lists live in `portals.py`, in the order and spelling each org declares them.
Only Walmart declares `selectedAccountId`, `additionalFieldsString`, `geo`, `UserType`, `adUnit`,
`documentId`, `advertiserId` and `formTemplateId`; only Sam's declares the four `backend*`
parameters, `currentExperience`, `IsFeatureRequest`, `campaignName` and `supplierChannel`; and Sam's
spells the article parameter `articleNotHelped` against Walmart's `ArticleNotHelped`.

Aura **drops undeclared parameters in silence**, so a value sent under a name the org does not
declare vanishes, and nothing in the response says so.

The `saveApiCase` wrapper takes, verbatim from the form's own controller: `companyName`,
`associatedBrands`, `billingContactJobTitle`, `billingContact`, `billingCompany`, `vendorName`,
`agencyName`, `subject`, `problem`, `problemDomain`, `subDomain`, `subDomain2`, `subDomain3`,
`backendDomain`, `backendSubDomain`, `backendSub2Domain`, `backendSub3Domain`,
`lastLevelCategory`, `supplierChannel`, `isOnboardingCase`, `advertiserAffected`, `guestName`,
`guestEmail`, `emails`. It carries no account id: the org resolves the account from the
authenticated contact. `problemDomain` and `backendDomain` carry the same value, as do `subDomain`
and `backendSubDomain`, because the form reads both of each pair off one field of the selected
category record.

`supplierChannel` is mandatory on the form for an `Advertiser-Ad_Agency` user, which is what this
account is on Sam's Club, and the picker offers `Self Serve` or `API`.

### The double escaping

A case filed through Sam's `openCase` is stored with its subject and body escaped twice:
`&amp;quot;App&amp;quot;` where the text said `"App"`, `&amp;lt;redacted&amp;gt;` where it said
`<redacted>`. The transform is entirely portal-side. Sam's `saveApiCase` stores the identical bytes
raw, and so did Walmart's `openCase` before the rebuild.

Proven without filing anything. `Case.Subject` caps at 255, so an oversized subject fails the
insert and Salesforce echoes the value it was about to write, after any server-side transform.
Aura escapes its own error message once, so the depth of the echo gives the depth at insert:

| Path | Echo | Depth at insert |
| --- | --- | --- |
| Sam's `openCase` | `&amp;amp;quot;App&amp;amp;quot;` | 2 |
| Sam's `saveApiCase` | `&amp;quot;App&amp;quot;` | 1, i.e. raw |
| Walmart `openCase`, before the rebuild | `&amp;quot;App&amp;quot;` | 1, i.e. raw |

It cost 4736 characters of body 6380 stored, against a 32000 cap on `Description`, and it degrades
what the portal's own agents read. Case reads undo it where it was applied — see
`undouble_escape` in `cases.py` — but only for our own rendering.

Ruled out along the way, so nobody repeats it: our own code escaping before sending; the
HTTP/Aura transport; payload shape, across every parameter combination including Sam's 19 alone;
a stale app build on our side; client-side escaping in the form; an overnight portal deploy; and
any length or truncation cap.

### Traps in `openCase`

- `additionalFieldsString` is a JSON **list**, not an object. Apex deserializes it into a `List` —
  an object gives "Expected '[' at the beginning of List/Set".

**Since the rebuild, Walmart's `openCase` has reached the insert but never completed one from this
client**: the 19 parameters bind, and the probe that showed it was rejected on purpose. Whether each
form field lands in its `caseField`, and what a new case's number looks like, wait on the first
real `--submit`; until then, put anything that matters in the description body as well. On Sam's
Club, `openCase` is verified end to end, but **`saveApiCase` has never completed an insert from
this client** either — every probe against it was rejected on purpose.

## Attachments

Uploads go through `saveChunk`, one call per chunk.

Deletion is asymmetric and this bites: an upload returns a **ContentVersion** id (`068…`), while
the portal's delete actions want the **ContentDocument** id (`069…`) and silently do nothing when
handed the other. The `069` ids are recoverable from a case's detail payload. The CLI only
uploads; removing an attachment is a portal-page action.

Attaching *while filing* is unresolved: `openCase` accepts a `documentId` list, but `saveChunk`
requires a `parentId` and the case does not exist yet, so it is unclear what the portal passes at
that point.

## Comments

`saveCaseComment` escapes the text and turns every newline into `<br>` before measuring it against
its 4000-character cap, so a blank line between paragraphs costs 8 characters rather than 2. That
is what `rendered_length` in `cases.py` reproduces. `getCaseData` unescapes on read, so a normal
reply round-trips unchanged.

A case's first "comment" is **not** a stored comment. Its `createdDate` equals the case's creation
second, its body is byte-identical to `Description`, and it exceeds the 4000-character
`CaseComment.Body` cap. `getCaseData` synthesizes it from the Description, so it carries whatever
escaping the Description already has.

## Closing

`closeCaseSt` closes a Walmart case. On the Sam's Club org it resolves, answers `SUCCESS`, and
returns the case with its status **untouched** — a silent no-op, indistinguishable from a real
close without re-reading the case. `closeCase` and `closeCaseStatus` do not exist there. The
command is gated on `Portal.can_close` rather than left to report a close that never happened.

The portal's own **Close Case** button does work: case page, confirmation modal, OK, landing the
case on `Canceled` rather than `Closed`. That is where the org's `Canceled` cases come from, and it
is the workaround to point users at.

*To resolve:* capture the Aura action behind that button. The page reloads on confirm, which wiped
the request log on the attempt made here, so it needs a capture that survives the reload — request
interception, or `Preserve log`. Once the action and its parameters are known, implement it behind
`can_close` and decide whether `Canceled` should be reported as a close or as its own outcome.

## Reading

The list action returns every case in one shot, with no server-side paging or filtering, but it
abbreviates `subject` and `description` to roughly 46 characters plus an ellipsis. `--limit`
reaches it as a SOQL `LIMIT`, applied *before* any filtering, which is why the CLI only pushes it
down when no filter is present.

The detail action's field set **varies by category**, so a blank column is not evidence of
anything. An API case carries `Issue_Category__c`, `Sub_Category_1__c`, `Advertisers_Affected__c`
and `Sponsored_Ad_Contact_{Name,Email}__c`; an onboarding case carries none of those and instead
has `Account_Admin_*`, `Company_Name_Supplier__c`, `Agency_Name__c` and `Vendor_Number__c`.
