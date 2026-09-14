---
name: pipeline-health
description: Check whether the Honeycomb ads pipeline is working — data freshness, Meta token, funnel conversions (quality tier + reported subtypes), snapshot volume and lead count, dashboard endpoint
---

# Pipeline Health

## Purpose

Answer one question: is the system working right now? Run this before any other skill so a downstream "no fatigue signals" or "all caught up" reading isn't actually masking a broken pipeline.

## Scripts

`scripts/check_health.py` runs five health checks against the Google Sheet, the Meta API, the newest ad-level snapshot, and the dashboard endpoint. It POSTs one row per check to the `pipeline_health` Sheet tab and prints structured JSON to stdout for Slack composition.

```
python3 skills/pipeline-health/scripts/check_health.py
# or, to skip the Sheet write while developing:
python3 skills/pipeline-health/scripts/check_health.py --no-sheet-write
```

Requires:
- `META_ACCESS_TOKEN` env var
- `EXEC_ENDPOINT` env var (optional — falls back to `exec_endpoint` from `benchmarks.json`)
- `data/config/benchmarks.json` for thresholds

The five checks:
1. **data_freshness** — calls `?action=rolling-latest-date`, compares to expected (yesterday in account timezone, or two days ago if running before 7 AM ET).
2. **meta_token** — calls Meta `debug_token`, parses `is_valid` and `expires_at`.
3. **funnel_conversions** — calls Meta `customconversions` and verifies every custom conversion configured under `conversions` in `benchmarks.json` (the quality tier and each subtype) still exists and is not archived. Reports each one's `last_fired_time`, so a conversion that silently stops firing is visible rather than passing on mere existence. The check returns two views: `status` / `detail` is the full picture (quality tier first, subtypes trailing under `subtypes (reported only): …`) and goes to the Sheet, the terminal and the issue-#48 one-liner; `slack_status` / `slack_detail` covers the quality tier only (`prequal_decisions last fired …`) and is what Slack sees. A missing/archived quality conversion is FAIL in both views; a subtype problem is at most WARN in the full view and leaves `slack_status` at PASS, so it never produces a Slack line. The primary tier (`leads`) is a standard pixel action, not a custom conversion, so it is not checked here — see snapshot_volume.
4. **dashboard_endpoint** — calls `?action=rollup` with the configured timeout, verifies a JSON response.
5. **snapshot_volume** — reads the newest `data/snapshots/<date>/ad_insights.json`. FAILs on 0 insight rows against ad objects, WARNs below `min_expected_insight_rows`, and always reports the lead total and spend (`N leads on $S spend`, summing `leads` with the pre-pivot `conversions` alias as fallback). If `pipeline_health.zero_lead_spend_floor_usd` is set in `benchmarks.json`, spend at or above it with 0 leads is a WARN. This is the only health signal for the primary metric.

The script's stdout JSON looks like:

```json
{
  "date": "2026-05-03",
  "checks": [
    {"name": "data_freshness", "status": "PASS", "detail": "..."},
    {"name": "funnel_conversions", "status": "WARN", "detail": "...",
     "slack_status": "PASS", "slack_detail": "prequal_decisions last fired ..."},
    ...
  ],
  "sheet_write": {"posted": true, "written": 5}
}
```

`sheet_write.posted` reports whether the historical log row was committed. If `posted: false` with an `error`, surface that in Slack alongside the WARN/FAIL — it means the Sheet log is broken even though the checks ran.

## Interpreting output

- **All PASS:** system is healthy. In autonomous mode, do nothing — silent success. In interactive mode, print results to terminal.
- **Any Slack-facing WARN or FAIL:** compose a Slack message that lists ONLY the non-PASS checks, judged by `slack_status` where a check provides one and `status` otherwise. Use `slack_detail` verbatim where present, else `detail`. Post via the Slack webhook. A check that is WARN in `status` but PASS in `slack_status` goes to the Sheet and the terminal, not to Slack.
- **Sheet log:** the script handles writing to `pipeline_health` automatically. Don't re-POST. If `sheet_write.posted` is `false`, that's itself a problem to flag.

## Output — Slack (only on WARN/FAIL, only if webhook is set)

**Skip Slack posting entirely if `SLACK_WEBHOOK_URL` env var is unset or empty** — print the summary to terminal only. This is the default in interactive mode; Slack is opt-in via the secret.

When the webhook IS set and there are Slack-facing non-PASS checks: plain text, no markdown headers, FAIL first then WARN, one line per check shaped `STATUS check_name: detail` (the check name is always the first token after the status), posted to `$SLACK_WEBHOOK_URL` via curl. Example:

```
⚠️ Pipeline Health — 2026-09-09

FAIL dashboard_endpoint: timed out after 10s
WARN meta_token: expires in 12 days (regenerate before 2026-09-22)
WARN snapshot_volume: 2026-09-08: 47 insight row(s), 0 leads on $412.10 spend — lead pipeline may have stopped firing
```

When `funnel_conversions` fires, its line carries only the quality tier:

```
FAIL funnel_conversions: prequal_decisions: 1153878920152279 is ARCHIVED
```

If a token regeneration deadline is mentioned, include the calendar date so it's actionable without arithmetic.

**Slack is leads only.** A Slack line may mention leads, cost per lead, spend and delivery diagnostics — nothing else. For a check that provides `slack_detail`, only `slack_detail` reaches Slack; the rest of its full `detail` exists for the Sheet, the terminal and the issue-#48 one-liner. If `slack_status` is PASS, no line for that check is posted.

## Output — Sheet

Handled by the script. Each run POSTs one row per check to `?action=health-write` (creates the `pipeline_health` tab on first call). Header row: `date, check, status, detail, recorded_at`. Don't issue your own POST — read `sheet_write.posted` in the script's JSON to confirm it succeeded.

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, **always print a human-readable summary to terminal** — don't just dump raw JSON. Format:

```
Pipeline Health — 2026-09-09

[PASS] data_freshness — latest data: 2026-09-08, expected: 2026-09-08
[PASS] meta_token — valid, no expiry (system user token)
[PASS] funnel_conversions — prequal_decisions last fired 2026-09-09 | subtypes (reported only): rewards_crowdfunding=2026-09-09, investment_crowdfunding=2026-09-03
[WARN] dashboard_endpoint — valid JSON in 8.2s (slow cold start)
[PASS] snapshot_volume — 2026-09-08: 2 insight row(s), 394 ad object(s), 20 leads on $268.05 spend

Sheet log: 5 rows written to pipeline_health
```

Always show all five checks (including PASS) so Tyler sees the full state. Then one trailing line confirming the Sheet write outcome.

## Constraints

- This skill only reads. It does not fix anything. Don't regenerate tokens, don't restart pipelines, don't modify config — just surface the diagnosis.
- **Silent when healthy in autonomous mode.** A daily "all clear" trains people to ignore the channel. Only post on WARN/FAIL.
- Never log the Meta token. The `detail` strings should never contain the access token.
- If `META_ACCESS_TOKEN` is not set, fail loudly with a clear error, not a silent WARN.
- **Leads are the primary metric.** The only lead-pipeline signal is the `N leads on $S spend` clause in `snapshot_volume`; keep it in every detail shape so the Slack line is lead-first when it fires.
- **Only the Slack-facing view reaches Slack.** Everything `funnel_conversions` validates beyond the quality tier (existence, archived state, `last_fired_time`) appears only in the full `detail` — the Sheet row, the terminal summary and the issue-#48 one-liner. Such a problem is never more than WARN in `status`, never moves `slack_status` off PASS, and never a threshold. Do not copy anything from `detail` into `slack_detail`, the Slack message, or a Slack example.
