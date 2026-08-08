---
name: pipeline-health
description: Check whether the Honeycomb ads pipeline is working — data freshness, Meta token, IC tracking, dashboard endpoint
---

# Pipeline Health

## Purpose

Answer one question: is the system working right now? Run this before any other skill so a downstream "no fatigue signals" or "all caught up" reading isn't actually masking a broken pipeline.

## Scripts

`scripts/check_health.py` runs four health checks against the Google Sheet, the Meta API, and the dashboard endpoint. It POSTs one row per check to the `pipeline_health` Sheet tab and prints structured JSON to stdout for Slack composition.

```
python3 skills/pipeline-health/scripts/check_health.py
# or, to skip the Sheet write while developing:
python3 skills/pipeline-health/scripts/check_health.py --no-sheet-write
```

Requires:
- `META_ACCESS_TOKEN` env var
- `EXEC_ENDPOINT` env var (optional — falls back to `exec_endpoint` from `benchmarks.json`)
- `data/config/benchmarks.json` for thresholds

The four checks:
1. **data_freshness** — calls `?action=rolling-latest-date`, compares to expected (yesterday in account timezone, or two days ago if running before 7 AM ET).
2. **meta_token** — calls Meta `debug_token`, parses `is_valid` and `expires_at`.
3. **ic_conversion_event** — calls Meta `customconversions`, verifies the IC custom conversion ID is present and active.
4. **dashboard_endpoint** — calls `?action=rollup` with the configured timeout, verifies a JSON response. (Was `?action=leaderboard` in an earlier draft of this skill; that action doesn't exist in `handleDashboardApi_` — the check was switched to the real `rollup` action.)

The script's stdout JSON looks like:

```json
{
  "date": "2026-05-03",
  "checks": [
    {"name": "data_freshness", "status": "PASS", "detail": "..."},
    ...
  ],
  "sheet_write": {"posted": true, "written": 4}
}
```

`sheet_write.posted` reports whether the historical log row was committed. If `posted: false` with an `error`, surface that in Slack alongside the WARN/FAIL — it means the Sheet log is broken even though the checks ran.

## Interpreting output

- **All PASS:** system is healthy. In autonomous mode, do nothing — silent success. In interactive mode, print results to terminal.
- **Any WARN or FAIL:** compose a Slack message that lists ONLY the non-PASS checks. Use the `detail` string verbatim. Post via the Slack webhook.
- **Sheet log:** the script handles writing to `pipeline_health` automatically. Don't re-POST. If `sheet_write.posted` is `false`, that's itself a problem to flag.

## Output — Slack (only on WARN/FAIL, only if webhook is set)

**Skip Slack posting entirely if `SLACK_WEBHOOK_URL` env var is unset or empty** — print the summary to terminal only. This is the default in interactive mode; Slack is opt-in via the secret.

When the webhook IS set and there are non-PASS checks: plain text, no markdown headers, FAIL first then WARN, posted to `$SLACK_WEBHOOK_URL` via curl. Example:

```
⚠️ Pipeline Health — 2026-05-03

FAIL: Data freshness — last data from 2026-04-30, expected 2026-05-02
WARN: Meta token expires in 12 days — regenerate before 2026-05-15
```

If a token regeneration deadline is mentioned, include the calendar date so it's actionable without arithmetic.

## Output — Sheet

Handled by the script. Each run POSTs one row per check to `?action=health-write` (creates the `pipeline_health` tab on first call). Header row: `date, check, status, detail, recorded_at`. Don't issue your own POST — read `sheet_write.posted` in the script's JSON to confirm it succeeded.

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, **always print a human-readable summary to terminal** — don't just dump raw JSON. Format:

```
Pipeline Health — 2026-05-03

[PASS] data_freshness — latest data: 2026-05-02, expected: 2026-05-02
[PASS] meta_token — valid, expires in 47 days
[PASS] ic_conversion_event — custom conversion 2330338620810873 ('Investment Crowdfunding Prequal Decision') exists
[WARN] dashboard_endpoint — valid JSON in 8.2s (slow cold start)

Sheet log: 4 rows written to pipeline_health
```

Always show all four checks (including PASS) so Tyler sees the full state. Then one trailing line confirming the Sheet write outcome.

## Constraints

- This skill only reads. It does not fix anything. Don't regenerate tokens, don't restart pipelines, don't modify config — just surface the diagnosis.
- **Silent when healthy in autonomous mode.** A daily "all clear" trains people to ignore the channel. Only post on WARN/FAIL.
- Never log the Meta token. The `detail` strings should never contain the access token.
- If `META_ACCESS_TOKEN` is not set, fail loudly with a clear error, not a silent WARN.
