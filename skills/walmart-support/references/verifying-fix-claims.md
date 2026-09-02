# Re-verifying a fix claim

Walmart **Ads API** knowledge, not portal knowledge: this is about proving whether a claimed fix
actually shipped, and it applies whichever portal the case lives on.

Re-run the repro when support's newest reply demands it: they claim a fix, ask for a retest, or ask
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
