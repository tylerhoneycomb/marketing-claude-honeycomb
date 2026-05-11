---
name: daily-check
description: Morning briefing — pacing, portfolio performance, winners, bleeders, and early fatigue signals at ad level
---

# Daily Check

## Purpose

Answer five questions each morning so Tyler can act on the right thing: Am I on pace? What's running? How's the portfolio? Who's winning / bleeding? Any early fatigue signals?

This skill runs alongside the existing Apps Script daily digest (campaign-level rollup + AI narrative). It adds ad-level detail and an explicit pacing read.

## Scripts

`scripts/fetch_daily_data.py` — Pulls 7 days of campaign, ad set, and ad-level insights from Meta plus current ad-set objects (`learning_stage_info`, `daily_budget`) and ad objects (`created_time`, `effective_status`). Outputs JSON to stdout.

`scripts/analyze_daily.py` — Reads the fetch output, applies thresholds from `data/config/benchmarks.json`, computes pacing/portfolio/winners/bleeders/fatigue/learning/stale-creative views, POSTs the summary row to `?action=daily-check-write`, and outputs structured JSON to stdout for Slack composition.

Run:

```
python3 skills/daily-check/scripts/fetch_daily_data.py > /tmp/daily_data.json
python3 skills/daily-check/scripts/analyze_daily.py --input /tmp/daily_data.json
```

Or piped:

```
python3 skills/daily-check/scripts/fetch_daily_data.py \
  | python3 skills/daily-check/scripts/analyze_daily.py
```

Use `--no-sheet-write` on `analyze_daily.py` for dry runs.

Requires:
- `META_ACCESS_TOKEN` env var
- `EXEC_ENDPOINT` env var (optional — falls back to `exec_endpoint` from `benchmarks.json`)
- `data/config/benchmarks.json` for thresholds

## Output schema (analyze_daily.py stdout)

```
{
  "date": "YYYY-MM-DD",                  // = until / yesterday
  "pacing": {status, yesterday_spend, remaining_daily_target,
             weekly_target, spent_this_week, days_remaining, week_start},
  "portfolio": [{campaign, spend, ic_conversions, cpicp, ctr, frequency}, …],
  "winners":   [{ad_name, campaign, cpc, conversions, ctr}, …],   // up to 3
  "bleeders":  [{ad_name, campaign, ctr, adset_avg_ctr, spend_share_pct}, …],
  "fatigue_flags": [{ad_name, campaign, frequency,
                     ctr_3d, ctr_prior_4d, ctr_decline_pct}, …],
  "learning_phase": [{adset_name, campaign_id, status}, …],
  "stale_creatives": [{ad_name, campaign_id, days_active, created_time}, …],
  "totals": {spend, ic_conversions, cpicp},
  "sheet_write": {posted, written|error|skipped}
}
```

## Interpreting output

- **Pacing:** `underspending` / `overspending` / `on_pace`. Informational, not an emergency. Always include in the summary so Tyler can see whether to adjust budget today.
- **Portfolio:** list every campaign with IC conversions, sorted by best CPICP. Call out campaigns with non-trivial spend and zero IC conversions — those are the ones to investigate.
- **Winners / Bleeders:** top 3 of each. These are the specific ads Tyler should look at. If `winners` is empty, that means no ad in the last 7 days hit the floor of ≥5 conversions + ≥1,000 impressions — say so explicitly.
- **Fatigue flags:** these *preview* the fatigue-monitor skill. Mention them in the briefing but note the full fatigue analysis lives in the separate skill.
- **Learning phase:** list ad sets currently in learning. State explicitly that no budget changes should be made to these — that's a hard rule.
- **Stale creatives:** ads active > `fatigue.creative_age_warning_days` (21 by default). Worth a refresh look but not necessarily fatiguing. **If the list has >15 entries, render the top 15 by days_active descending and collapse the long tail into one summary line** (e.g., `+ 55 more ads at ≤30d`). When most of the tail shares a created_time (cohort launch), name the cohort prefix so the summary is scannable (e.g., `+ 55 more BR-* cohort ads at 30d`). Default rendering of 70+ rows makes the message unreadable.
- **`sheet_write.posted == false`:** the historical log didn't write. Surface that as its own line in Slack — the briefing is still useful, but Tyler should know the log is broken.

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, **always print a human-readable summary to terminal** — don't just dump raw JSON. Same format as the Slack template below, just printed to stdout. Always show all sections that have data (skip empty sections). End with a one-liner confirming the Sheet write outcome (e.g., "Sheet log: 1 row written to daily_check_log").

## Output — Slack (only if webhook is set)

**Skip Slack posting entirely if `SLACK_WEBHOOK_URL` env var is unset or empty** — print to terminal only (see Interactive section above). Slack is opt-in via the secret; the default for interactive runs is terminal-only.

When the webhook IS set: compose a plain-text summary, keep it scannable — one line per item, sections separated by blank lines. No markdown headers. POST to `$SLACK_WEBHOOK_URL` via curl. Example shape:

```
📊 Daily Check — 2026-05-03

PACING: underspending — $1,500 yesterday, $8,050/day needed for $10,000 target

PORTFOLIO (7d, best CPICP first):
  Breweries: $150.00 CPICP, 13 ICPs, $1,950, freq 1.6
  …

WINNERS:
  WinnerAd (Breweries): $1.07 CPC, 10 convs

BLEEDERS:
  BleederAd (Breweries): 0.5% vs 1.5% adset avg, 25% spend share

FATIGUE WATCH:
  FatigueAd (Breweries): freq 2.3, CTR ↓43% (3d vs prior 4d)

LEARNING:
  AS1 (campaign c1) — no budget changes

STALE:
  WinnerAd: 48 days active
```

Skip empty sections rather than printing "(none)". If everything is empty (no winners, no bleeders, no fatigue), say so in one line: "All ads under signal floors today."

End the Slack message with a one-line footer: `_Source: fresh Meta API call_`. The parallel campaign-level Apps Script "Honeycomb Ads" digest reads from the `rolling_data` sheet snapshot (~7 AM pull), so the two reports may show different "yesterday spend" values for the same day — Meta's attribution shifts between the morning snapshot and your runtime API call. The footer makes the source unambiguous.

## Output — Sheet

Handled by `analyze_daily.py`. One summary row per run via `?action=daily-check-write` to the `daily_check_log` tab (auto-created on first call). Header: `date, pacing_status, total_spend, total_icps, portfolio_cpicp, fatigue_flag_count, recorded_at`. Don't issue your own POST.

## Constraints

- This skill **does not** recommend budget changes. It surfaces signals.
- **Never** propose changes to ad sets in learning phase — flag them by name and stop.
- This skill runs **alongside** the existing Apps Script daily digest. It is not a replacement; the digest covers the campaign-level rollup and the weekly AI narrative. This skill adds ad-level detail.
- Scripts handle Meta API + Sheet writes. Don't make additional API calls from the skill executor — read the JSON and compose Slack from it.
- Numbers come from a single 7-day Meta query. If Meta returns fewer days (e.g., a brand-new account), the pacing math degrades gracefully — `remaining_daily_target` may be `null`. Surface that as "insufficient history for pacing read" rather than reporting confusing numbers.
