# Case description skeleton

Copy this into a file and pass it as `--description-file`. Support traces from the wire, so keep
the request and response verbatim — do not summarise, reformat, or retype them.

Run the draft past `Before any outbound write` in `SKILL.md` before it goes.

---

## What we called

```
curl -X GET 'https://advertising.walmart.com/api/v1/...' \
  -H 'WM_SEC.ACCESS_TOKEN: <redacted>' \
  -H 'WM_CONSUMER.ID: 00000000-0000-0000-0000-000000000000' \
  -H 'WM_QOS.CORRELATION_ID: 00000000-0000-0000-0000-000000000000'
```

## What came back

```
HTTP/1.1 500 Internal Server Error

{"details": {"code": "...", "message": "..."}}
```

## What we expected instead

One line. State the expected status and body, not the business impact.

## Scope

- Environment: production | sandbox
- First seen: YYYY-MM-DD HH:MM UTC
- Reproducible: every call | intermittent (N of M)
- Advertiser ids: pass these via `--advertisers`, not here
