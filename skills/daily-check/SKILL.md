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
             weekly_target, weekly_target_source,
             spent_this_week, days_remaining, week_start},
  "portfolio": [{campaign, spend, leads, cpl, prequal_decisions, ic_conversions, cpicp, ctr, frequency}, …],
  "winners":   [{ad_name, campaign, cpl, leads, cpc, ctr}, …],   // up to 3, cpl asc
  "bleeders":  [{ad_name, campaign, reason, leads, cpl, adset_cpl,
                 ctr, adset_avg_ctr, spend_share_pct}, …],         // up to 3
                 // reason ∈ spend_without_leads | cpl_above_adset
                 //          | ctr_below_adset_no_lead_data
                 // cpl is null on spend_without_leads rows
  "fatigue_flags": [{ad_name, campaign, frequency,
                     ctr_3d, ctr_prior_4d, ctr_decline_pct}, …],
  "learning_phase": [{adset_name, campaign_id, status}, …],
  "stale_creatives": [{ad_name, campaign_id, days_active, created_time}, …],
  "totals": {spend, leads, cpl, prequal_decisions, ic_conversions, cpicp},
  "sheet_write": {posted, written|error|skipped}
}
```

## Interpreting output

- **Pacing:** `underspending` / `overspending` / `on_pace`. Informational, not an emergency. Always include in the summary so Tyler can see whether to adjust budget today. The `weekly_target` is fetched live from `/exec?action=get_spend_goal` (the dashboard-managed spend goal), so it reflects the latest deployment — use the number from the JSON, never a hardcoded "$10,000". If `weekly_target_source == "fallback_unreachable"` the `/exec` call failed and `weekly_target` is a static fallback — append `(target from static fallback — /exec unreachable)` to the PACING line so the staleness is visible.
- **Totals:** headline the brief with `totals` — leads, CPL, spend, prequal decisions. IC (`ic_conversions`) is a subtype: mention it only as a trailing parenthetical, and omit it when 0. Never lead with CPICP.
- **Portfolio:** list every campaign in JSON order (already sorted best CPL first, no-lead campaigns last) with CPL, leads, spend, frequency. Call out campaigns with non-trivial spend and zero leads explicitly (`0 leads`) — those are the ones to investigate. Report IC alongside as a subtype (`· IC n`, only when > 0), never as the sort key.
- **Winners / Bleeders:** top 3 of each. These are the specific ads Tyler should look at. Winners are ranked by CPL (best first) and must clear the floor of ≥5 leads + ≥1,000 impressions; if `winners` is empty, no ad in the last 7 days hit that floor — say so explicitly. Bleeders are ordered spend-without-leads first, then most inflated CPL; render each by its `reason` (see the Slack example). The CPL bleeder threshold is `lead_economics.cpl_warning_multiple` (1.5× the ad-set CPL by default), so a reader knows why an ad qualified.
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
📊 Daily Lead Check — 2026-05-03
7d: 412 leads · $14.20 CPL · $5,850 spend · 371 prequal decisions (of which 2 reached an IC decision)

PACING: underspending — $1,500 yesterday, $8,050/day needed for the $<weekly_target> target

PORTFOLIO (7d, best CPL first):
  Breweries: $12.40 CPL, 157 leads, $1,950, freq 1.6 · IC 1
  Gyms: $18.90 CPL, 64 leads, $1,210, freq 1.9
  Salons: 0 leads, $340, freq 1.2
  …

WINNERS (best CPL first):
  WinnerAd (Breweries): $9.80 CPL, 22 leads (CTR 1.8%)

BLEEDERS:
  BleederAd (Breweries): 0 leads on 25% of ad-set spend
  BleederAd2 (Gyms): $48 CPL vs $16 ad-set CPL, 31% spend share
  BleederAd3 (Salons): 0.5% CTR vs 1.5% ad-set avg, 22% spend share (no lead data in ad set yet)

FATIGUE WATCH:
  FatigueAd (Breweries): freq 2.3, CTR ↓43% (3d vs prior 4d)

LEARNING:
  AS1 (campaign c1) — no budget changes

STALE:
  WinnerAd: 48 days active
```

The headline line under the title comes from `totals`; drop the IC parenthetical when `ic_conversions` is 0. Portfolio lines append `· IC n` only when the campaign's `ic_conversions` > 0. Bleeder lines render by `reason`: `spend_without_leads` → 0 leads / spend share (bleeder rows carry no ad spend figure, so don't invent one); `cpl_above_adset` → ad CPL vs `adset_cpl` / spend share; `ctr_below_adset_no_lead_data` → the CTR comparison with the `(no lead data in ad set yet)` suffix. Never print a CPC or a CTR as a winner's headline number.

Skip empty sections rather than printing "(none)". If everything is empty (no winners, no bleeders, no fatigue), say so in one line: "All ads under signal floors today."

End the Slack message with a one-line footer: `_Source: fresh Meta API call_`. The parallel campaign-level Apps Script "Honeycomb Ads" digest reads from the `rolling_data` sheet snapshot (~7 AM pull), so the two reports may show different "yesterday spend" values for the same day — Meta's attribution shifts between the morning snapshot and your runtime API call. The footer makes the source unambiguous.

## Output — Sheet

Handled by `analyze_daily.py`. One summary row per run via `?action=daily-check-write` to the `daily_check_log` tab (auto-created on first call). Header: `date, pacing_status, total_spend, total_icps, portfolio_cpicp, fatigue_flag_count, recorded_at`. Don't issue your own POST.

> **Wire contract is still IC-named.** The `handleDailyCheckWrite_` handler in
> `apps-script/Code.js` reads its payload keys by name, so the legacy
> `total_icps` and `portfolio_cpicp` keys must keep being sent even though every metric above is
> lead-based. The handler appends only its seven fixed columns, so the
> `total_leads` / `portfolio_cpl` keys the script also sends are silently
> dropped (not blanked) until `Code.js` is edited and redeployed — tracked
> separately; no Code.js change from the lead pivot has been deployed yet.


## Constraints

- This skill **does not** recommend budget changes. It surfaces signals.
- **Never** propose changes to ad sets in learning phase — flag them by name and stop.
- This skill runs **alongside** the existing Apps Script daily digest. It is not a replacement; the digest covers the campaign-level rollup and the weekly AI narrative. This skill adds ad-level detail.
- Scripts handle Meta API + Sheet writes. Don't make additional API calls from the skill executor — read the JSON and compose Slack from it.
- Numbers come from a single 7-day Meta query. If Meta returns fewer days (e.g., a brand-new account), the pacing math degrades gracefully — `remaining_daily_target` may be `null`. Surface that as "insufficient history for pacing read" rather than reporting confusing numbers.
