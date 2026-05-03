---
name: pipeline-health
description: Check whether the Honeycomb ads pipeline is working — data freshness, Meta token, IC tracking, dashboard endpoint
---

# Pipeline Health

## Purpose

Answer one question: is the system working right now? Run this before any other skill so a downstream "no fatigue signals" or "all caught up" reading isn't actually masking a broken pipeline.

## Scripts

`scripts/check_health.py` runs four health checks against the Google Sheet, the Meta API, and the dashboard endpoint, and prints structured JSON to stdout.

```
python3 skills/pipeline-health/scripts/check_health.py
```

Requires:
- `META_ACCESS_TOKEN` env var
- `EXEC_ENDPOINT` env var (optional — falls back to `account.exec_endpoint` from `benchmarks.json`)
- `data/config/benchmarks.json` for thresholds

The four checks:
1. **data_freshness** — calls `?action=rolling-latest-date`, compares to expected (yesterday in account timezone, or two days ago if running before 7 AM ET).
2. **meta_token** — calls Meta `debug_token`, parses `is_valid` and `expires_at`.
3. **ic_conversion_event** — calls Meta `customconversions`, verifies the IC custom conversion ID is present and active.
4. **dashboard_endpoint** — calls `?action=leaderboard` with the configured timeout, verifies a JSON response.

Each check returns `{name, status, detail}` where `status` is `PASS`, `WARN`, or `FAIL`.

## Interpreting output

- **All PASS:** system is healthy. In autonomous mode, do nothing — silent success. In interactive mode, print results to terminal.
- **Any WARN or FAIL:** compose a Slack message that lists ONLY the non-PASS checks. Use the `detail` string verbatim. Post via the Slack webhook.
- **Always write all results** (including PASS) to the Sheet via `{exec_endpoint}?action=health-write` for the historical log. One row per check.

## Output — Slack (only on WARN/FAIL)

Plain text. No markdown headers. Order: FAIL first, then WARN. Example:

```
⚠️ Pipeline Health — 2026-05-03

FAIL: Data freshness — last data from 2026-04-30, expected 2026-05-02
WARN: Meta token expires in 12 days — regenerate before 2026-05-15
```

If a token regeneration deadline is mentioned, include the calendar date so it's actionable without arithmetic.

## Output — Sheet

POST to `{exec_endpoint}?action=health-write` with payload:

```
{
  "rows": [
    {"date": "2026-05-03", "check": "data_freshness", "status": "PASS", "detail": "..."},
    ...
  ]
}
```

The endpoint creates the `pipeline_health` tab on first call (header row: `date, check, status, detail, recorded_at`).

## Output — Interactive

Print the full JSON to terminal, then a one-line summary like `4 checks: 3 PASS, 1 WARN`. Show all checks regardless of status — Tyler may want to see PASS detail.

## Constraints

- This skill only reads. It does not fix anything. Don't regenerate tokens, don't restart pipelines, don't modify config — just surface the diagnosis.
- **Silent when healthy in autonomous mode.** A daily "all clear" trains people to ignore the channel. Only post on WARN/FAIL.
- Never log the Meta token. The `detail` strings should never contain the access token.
- If `META_ACCESS_TOKEN` is not set, fail loudly with a clear error, not a silent WARN.
