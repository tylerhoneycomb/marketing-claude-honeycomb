---
name: fatigue-monitor
description: Detect ad creative fatigue — CTR decay, frequency saturation, CPC inflation — and classify severity
---

# Fatigue Monitor

## Purpose

Identify ads losing effectiveness before CPICP degrades. Classify severity so Tyler can act: pause, replace, or monitor. Surface pending budget conflicts so a fatiguing ad doesn't get more spend tomorrow.

## Scripts

Three scripts, run in sequence. The pipeline writes intermediates to `/tmp/` so each step is inspectable.

```
python3 skills/fatigue-monitor/scripts/fetch_fatigue_data.py > /tmp/fatigue_fetch.json
python3 skills/fatigue-monitor/scripts/compute_baselines.py --input /tmp/fatigue_fetch.json > /tmp/fatigue_baselines.json
python3 skills/fatigue-monitor/scripts/classify_fatigue.py --fetch /tmp/fatigue_fetch.json --baselines /tmp/fatigue_baselines.json
```

`classify_fatigue.py` POSTs the per-ad rows to `?action=fatigue-write` and prints the structured summary to stdout. Use `--no-sheet-write` and/or `--no-budget-check` for dry-runs.

Requires:
- `META_ACCESS_TOKEN` env var
- `EXEC_ENDPOINT` env var (optional — falls back to `exec_endpoint` from `benchmarks.json`)
- `data/config/benchmarks.json` for thresholds

## How baselines work

Each ad's baseline is its peak performance window — by default days 4–7 after launch (configurable via `fatigue.baseline_window_start_day` / `baseline_window_end_day`). `compute_baselines.py` picks one of three paths per ad:

| Path | When | API cost |
|---|---|---|
| **A — peak_window (in-range)** | Days 4–7 fall within the 14-day fetch window. Slice from the data already in hand. | 0 extra calls |
| **B — peak_window (historical)** | Ad is older than 14 days but within Meta's 93-day insight retention. | **One** consolidated query covering the union of all Path-B ads' baseline windows, filtered by ad_id. NOT per-ad. |
| **C — estimated** | Ad is older than 93 days, missing `created_time`, or too new for any window yet. Use the oldest 4 days of the current 14-day window as a proxy. | 0 extra calls |

`baseline_type: "estimated"` is less reliable — flag it in the Slack output if it's driving a `fatigued` classification.

## Classification matrix

The `fatigue.*` thresholds in `benchmarks.json` drive this. See `references/fatigue_thresholds.md` for sourcing.

| Frequency | CTR vs baseline | CPC vs baseline | Classification |
|---|---|---|---|
| ≥ `frequency_critical` (3.0) | any | any | **saturated** — broaden audience or reduce ad-set budget |
| ≥ `frequency_warning` (2.0) | declined > `ctr_fatigued_decline_pct` (30%) | any | **fatigued** — pause or replace |
| ≥ `frequency_warning` (2.0) | declined `ctr_early_decline_pct`–`ctr_fatigued_decline_pct` (15–30%) | any | **early_fatigue** — queue replacement |
| < `frequency_warning` (2.0) | declined > `ctr_fatigued_decline_pct` (30%) | stable | **underperforming** — quality issue, not fatigue |
| otherwise | otherwise | otherwise | **healthy** |

For retargeting campaigns (`campaign_defaults.type == "retargeting"`), `frequency_critical` is `5.0` instead of `3.0`. Currently all Honeycomb campaigns are prospecting.

## Eligibility gates (skipped before classification)

- `effective_status` ≠ `ACTIVE`
- < `fatigue.min_impressions` (1,000) impressions in the 14-day window
- < `fatigue.min_days_active` (7) days since `created_time`
- No baseline computed (rare — usually means the ad has zero impressions in its baseline window)

The `stats.skipped` field in the output reports counts per gate.

## Budget conflict check

`classify_fatigue.py` calls `?action=budget-queue-read`, looks for `pending` proposals where `direction == "increase"`, and matches by `campaign_id` against fatigued / early_fatigue ads. When a match exists, the per-ad row gets a `budget_conflict` string like:

> "Pending budget INCREASE on Rev2 - IC - Wineries (+2.0%) — consider pausing this ad before approval"

Surface that prominently in Slack — it's the main "act today" signal.

## Output schema (classify_fatigue.py stdout)

```
{
  "date", "since", "until",
  "stats": {ads_evaluated, by_classification: {...}, skipped: {...},
            pending_budget_proposals, pending_increases_with_conflict,
            campaign_type},
  "classifications": [
    {ad_id, ad_name, campaign_id, campaign,
     classification,
     ctr_baseline, ctr_current, ctr_decline_pct,
     frequency,
     cpc_baseline, cpc_current, cpc_change_pct,
     days_active, baseline_type, baseline_since, baseline_until,
     headline, thumbnail_url,
     budget_conflict},
    ...
  ],
  "sheet_write": {posted, written|error|skipped}
}
```

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, **always print a human-readable summary to terminal** — don't just dump raw JSON. Same format as the Slack template below, just printed to stdout. Always show non-healthy ads grouped by severity. End with a one-liner confirming the Sheet write outcome (e.g., "Sheet log: N rows written to fatigue_log") and the per-classification counts from `stats.by_classification`.

## Output — Slack (only non-healthy ads, only if webhook is set)

**Skip Slack posting entirely if `SLACK_WEBHOOK_URL` env var is unset or empty** — print to terminal only (see Interactive section above). Slack is opt-in via the secret; the default for interactive runs is terminal-only.

When the webhook IS set: plain text, sectioned by severity (FATIGUED first, then EARLY FATIGUE, then SATURATED, then UNDERPERFORMING). Skip `healthy` entirely. Per ad include name, campaign, the metrics in the format shown below, days active, and a short headline preview if available. If `baseline_type == "estimated"` for a fatigued ad, append `(estimated baseline)` after the metrics line. If a `budget_conflict` exists, render it as a separate warning line under that ad. POST to `$SLACK_WEBHOOK_URL` via curl.

```
🔥 Fatigue Monitor — 2026-05-03

FATIGUED:
  Winery sunset v1 (Rev2 - IC - Wineries)
  CTR: 1.8% → 0.9% (↓50%) | Freq 3.2 | CPC: $1.50 → $2.40 (↑60%)
  Active 25 days | "Invest in what you love"
  ⚠️ Pending budget INCREASE on Rev2 - IC - Wineries (+2.0%) — consider pausing this ad before approval

EARLY FATIGUE:
  Brewery hero v2 (Rev2 - IC - Breweries)
  CTR: 2.1% → 1.5% (↓28.6%) | Freq 2.4
  Active 18 days

SATURATED AUDIENCES:
  Coffee shop v1 (Rev2 - IC - Coffee): freq 3.5 across 4 active ads
```

If `classifications` is empty (everything healthy), say so in one line: "No fatigue signals — all evaluated ads healthy or below thresholds."

## Output — Sheet

Handled by `classify_fatigue.py`. ALL evaluated ads (including healthy) write to `fatigue_log` via `?action=fatigue-write`. Header row: `date, ad_id, ad_name, campaign, classification, ctr_baseline, ctr_current, ctr_decline_pct, frequency, cpc_baseline, cpc_current, days_active, baseline_type, budget_conflict, recorded_at`. Don't issue your own POST.

## Constraints

- This skill **does not** pause ads or change budgets. It surfaces signals and conflicts.
- Ads under `min_impressions` (1,000) or `min_days_active` (7) are skipped — don't manually override.
- `baseline_type: "estimated"` carries lower confidence than `"peak_window"`. Mention it in Slack if it drove a fatigued/early_fatigue verdict so Tyler can weight the recommendation accordingly.
- The Path-B Meta query is the most expensive call this skill makes. If `--no-historical-query` is passed to `compute_baselines.py`, all Path-B ads degrade to `estimated` baselines — fine for testing, but the production output should run the query.
- Retargeting threshold (`frequency_retargeting_critical: 5.0`) is set in `benchmarks.json` but not currently exercised — all Honeycomb campaigns are prospecting. If retargeting campaigns are added, set `campaign_defaults.type` accordingly or extend the script to look up campaign type per-ad.
