---
name: fatigue-monitor
description: Detect ad creative fatigue — CPL inflation, CTR decay, frequency saturation — and report what each fatiguing ad's leads cost
---

# Fatigue Monitor

## Purpose

Identify ads losing effectiveness before CPL degrades further, and say what each one's leads cost so Tyler can act: pause, replace, or monitor. Surface pending budget conflicts so a fatiguing ad doesn't get more spend tomorrow.

Leads and CPL are the headline on every line of this skill's output. CTR, frequency and CPC are diagnostics that explain *why* the leads got expensive.

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
- `EXEC_SHARED_SECRET` env var for the Sheet write (`fatigue-write` is a protected action; without it `sheet_write.posted` is `false` with `error: "unauthorized"`)
- `data/config/benchmarks.json` for thresholds

## How baselines work

Each ad's baseline is its peak performance window — by default days 4–7 after launch (configurable via `fatigue.baseline_window_start_day` / `baseline_window_end_day`). The baseline carries `leads_baseline` / `cpl_baseline` / `spend_baseline` alongside `ctr_baseline` / `cpc_baseline` / `cpm_baseline`. `compute_baselines.py` picks one of three paths per ad:

| Path | When | API cost |
|---|---|---|
| **A — peak_window (in-range)** | Days 4–7 fall within the 14-day fetch window. Slice from the data already in hand. | 0 extra calls |
| **B — peak_window (historical)** | Ad is older than 14 days but within Meta's 93-day insight retention. | **One** consolidated query covering the union of all Path-B ads' baseline windows, filtered by ad_id. NOT per-ad. |
| **C — estimated** | Ad is older than 93 days, missing `created_time`, or too new for any window yet. Use the oldest 4 days of the current 14-day window as a proxy. | 0 extra calls |

`baseline_type: "estimated"` is less reliable — flag it in the Slack output if it's driving a `fatigued` classification. This applies to the CPL baseline too: a 4-day window holds few leads, so `cpl_baseline` is noisier than `ctr_baseline`. `cpl_baseline` is `null` when the window bought no leads, and the CPL rules stay silent in that case.

## Classification matrix

The `fatigue.*` thresholds in `benchmarks.json` drive this. See `references/fatigue_thresholds.md` for sourcing. CPL change is only evaluated when both the baseline and current windows bought at least one lead.

| Frequency | CPL vs baseline | CTR vs baseline | Classification |
|---|---|---|---|
| ≥ `frequency_critical` (3.0) | any | any | **saturated** — broaden audience or reduce ad-set budget |
| any | risen ≥ `cpl_inflation_critical_pct` (50%) | any | **fatigued** — leads are materially more expensive; pause or replace |
| ≥ `frequency_warning` (2.0) | any | declined > `ctr_fatigued_decline_pct` (30%) | **fatigued** — pause or replace |
| ≥ `frequency_warning` (2.0) | risen ≥ `cpl_inflation_warning_pct` (25%) | any | **early_fatigue** — queue replacement |
| ≥ `frequency_warning` (2.0) | any | declined `ctr_early_decline_pct`–`ctr_fatigued_decline_pct` (15–30%) | **early_fatigue** — queue replacement |
| < `frequency_warning` (2.0) | any | declined > `ctr_fatigued_decline_pct` (30%) | **underperforming** — quality issue, not fatigue |
| otherwise | otherwise | otherwise | **healthy** |

Each row's `signals` list says which rules fired (`frequency_critical`, `frequency_warning`, `cpl_critical`, `cpl_warning`, `ctr_fatigued`, `ctr_early`) so the brief can explain a verdict without re-deriving it. CPC is reported only; it is not a classification input.

For retargeting campaigns (`campaign_defaults.type == "retargeting"`), `frequency_critical` is `5.0` instead of `3.0`. Currently all Honeycomb campaigns are prospecting.

## Eligibility gates (skipped before classification)

- `effective_status` ≠ `ACTIVE`
- < `fatigue.min_impressions` (1,000) impressions in the 14-day window
- < `fatigue.min_days_active` (7) days since `created_time`
- No baseline computed (rare — usually means the ad has zero impressions in its baseline window)

The `stats.skipped` field in the output reports counts per gate.

## Ordering

`classifications` is pre-sorted for the brief: severity section (fatigued → early_fatigue → saturated → underperforming → healthy), then within a section budget conflicts first, then ads that spent with zero leads, then CPL descending (most expensive leads first), then spend descending. Render in that order — do not re-sort.

## Budget conflict check

`classify_fatigue.py` calls `?action=budget-queue-read`, keeps the **newest** `pending` proposal with `direction == "increase"` per campaign (the queue is append-only and stale optimizer rows never expire, so oldest-first would pin a dead proposal), and matches by `campaign_id` against fatigued / early_fatigue ads. When a match exists, the per-ad row gets a `budget_conflict` string composed in Python, lead-first:

> "Pending budget INCREASE on LEADS-Broad-Q3-2026 (+2.0%, +$12.50/day, strategic) — this ad bought 5 leads at CPL $22.40 (baseline $15.45, +45%) in the last 7 days; consider pausing before approval"

Zero-lead variant:

> "Pending budget INCREASE on LEADS-Broad-Q3-2026 (+2.0%, +$12.50/day, strategic) — this ad spent $61.20 with 0 leads in the last 7 days; consider pausing before approval"

Render it verbatim. Surface it prominently in Slack — it's the main "act today" signal. The proposal's `signal_reasons` field is deliberately never rendered: rows queued before the lead pivot carry IC-era wording.

## Output schema (classify_fatigue.py stdout)

```
{
  "date", "since", "until",
  "stats": {ads_evaluated, non_healthy, by_classification: {...},
            spend_7d, leads_7d, cpl_7d, target_cpl_dollars, prequal_7d, ic_7d,
            skipped: {...},
            pending_budget_proposals, pending_increases_with_conflict,
            campaign_type},
  "classifications": [
    {ad_id, ad_name, campaign_id, campaign,
     classification, signals,
     spend_current, leads_current, cpl_current,
     leads_baseline, cpl_baseline, cpl_change_pct, zero_lead_spend,
     prequal_current, ic_current,
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

`prequal_current` / `ic_current` / `prequal_7d` / `ic_7d` are reported only — never a sort key, threshold, or headline.

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, **always print a human-readable summary to terminal** — don't just dump raw JSON. Same format as the Slack template below, just printed to stdout. Always show non-healthy ads grouped by severity, in the order `classifications` already has them. End with a one-liner confirming the Sheet write outcome (e.g., "Sheet log: N rows written to fatigue_log") and the per-classification counts from `stats.by_classification`. If `sheet_write.posted` is `false`, add a separate `WARN` line with `sheet_write.error`.

## Output — Slack (headline + non-healthy ads only, only if webhook is set)

**Skip Slack posting entirely if `SLACK_WEBHOOK_URL` env var is unset or empty** — print to terminal only (see Interactive section above). Slack is opt-in via the secret; the default for interactive runs is terminal-only.

When the webhook IS set, the Slack body is **exactly two things**: the headline line and the non-healthy section. The "Sheet log" trailer, the `by_classification` counts one-liner and any `WARN` line stay in the terminal / workflow log — they never go to Slack.

Headline: `🔥 Fatigue Monitor — <date> — <non_healthy> ads at risk · $<spend_7d> spend / <leads_7d> leads (CPL $<cpl_7d>) last 7d`, plus ` · <N> budget conflicts` when `pending_increases_with_conflict > 0`.

Non-healthy section: plain text, sectioned by severity (FATIGUED first, then EARLY FATIGUE, then SATURATED, then UNDERPERFORMING). Skip `healthy` entirely. Per ad:

1. Name (campaign)
2. **Lead line (first):** `Leads 7d: N (CPL $a → $b, ↑x%) | spend $s`. Use `0 leads on $s spend` when `leads_current == 0`. Omit the arrow/percent when `cpl_change_pct` is `null` (baseline had no leads) and show just `CPL $b`. When `cpl_current` is above `stats.target_cpl_dollars` (from `lead_economics.target_cpl_dollars`, currently $16), append ` · vs $<target> target`.
3. Diagnostics line: `CTR: a% → b% (↓x%) | Freq f | CPC: $a → $b (↑x%)`. Append `(estimated baseline)` if `baseline_type == "estimated"`.
4. `Active N days | "<headline>"` (headline only if available)
5. Secondary, only when `ic_current > 0`: `of which N reached an IC decision`
6. If `budget_conflict` is non-null, render it verbatim as a `⚠️` line under that ad.

```
🔥 Fatigue Monitor — 2026-09-10 — 4 ads at risk · $630 spend / 35 leads (CPL $18.00) last 7d · 1 budget conflict

FATIGUED:
  Winery sunset v1 (LEADS-Broad-Q3-2026)
  Leads 7d: 5 (CPL $15.45 → $22.40, ↑45%) | spend $112 · vs $16 target
  CTR: 1.8% → 0.9% (↓50%) | Freq 3.2 | CPC: $1.50 → $2.40 (↑60%)
  Active 25 days | "Invest in what you love"
  of which 1 reached an IC decision
  ⚠️ Pending budget INCREASE on LEADS-Broad-Q3-2026 (+2.0%, +$12.50/day, strategic) — this ad bought 5 leads at CPL $22.40 (baseline $15.45, +45%) in the last 7 days; consider pausing before approval

  Brewery hero v3 (LEADS-Broad-Q3-2026)
  Leads 7d: 0 leads on $61 spend
  CTR: 2.0% → 1.2% (↓40%) | Freq 2.5 | CPC: $1.10 → $1.90 (↑73%) (estimated baseline)
  Active 40 days

EARLY FATIGUE:
  Brewery hero v2 (LEADS-Broad-Q3-2026)
  Leads 7d: 9 (CPL $14.10 → $18.20, ↑29%) | spend $164 · vs $16 target
  CTR: 2.1% → 1.5% (↓28.6%) | Freq 2.4
  Active 18 days

SATURATED AUDIENCES:
  Coffee shop v1 (LEADS-Broad-Q3-2026)
  Leads 7d: 11 (CPL $13.80 → $14.90, ↑8%) | spend $164
  Freq 3.5 | CTR: 1.9% → 1.7% (↓10%)
  Active 22 days
```

**All-healthy runs.** `classifications` includes healthy ads, so "empty" is the wrong test. The condition is *no non-healthy classifications* — `stats.non_healthy == 0` (equivalently, `by_classification` has only `healthy`). In that case the Slack body is the headline plus this single line in place of the non-healthy section, so Tyler can tell "ran and found nothing" from "did not run":

> "No fatigue signals — N ads evaluated, $X spend / Y leads (CPL $Z) in the last 7d, all healthy."

Pull N / X / Y / Z from `stats.ads_evaluated`, `stats.spend_7d`, `stats.leads_7d`, `stats.cpl_7d`. If `stats.ads_evaluated` is 0 (nothing passed the eligibility gates), say so instead: "No ads passed the eligibility gates — see stats.skipped."

## Output — Sheet

Handled by `classify_fatigue.py`. ALL evaluated ads (including healthy) write to `fatigue_log` via `?action=fatigue-write`. Header row: `date, ad_id, ad_name, campaign, classification, ctr_baseline, ctr_current, ctr_decline_pct, frequency, cpc_baseline, cpc_current, days_active, baseline_type, budget_conflict, recorded_at`. The payload also carries `spend_current, leads_current, cpl_current, leads_baseline, cpl_baseline, cpl_change_pct` additively; `handleFatigueWrite_` reads keys by name and ignores the ones it doesn't have a column for until the Sheet schema is extended. Don't issue your own POST.

## Constraints

- This skill **does not** pause ads or change budgets. It surfaces signals and conflicts.
- Ads under `min_impressions` (1,000) or `min_days_active` (7) are skipped — don't manually override.
- Leads and CPL are the headline and the sort key. IC / prequal counts are reported on a secondary line only — never promote them to the headline, a threshold, or a sort key.
- `baseline_type: "estimated"` carries lower confidence than `"peak_window"` — for CPL even more so than CTR, because a 4-day proxy holds few leads. Mention it in Slack if it drove a fatigued/early_fatigue verdict so Tyler can weight the recommendation accordingly.
- The Path-B Meta query is the most expensive call this skill makes. If `--no-historical-query` is passed to `compute_baselines.py`, all Path-B ads degrade to `estimated` baselines — fine for testing, but the production output should run the query.
- Retargeting threshold (`frequency_retargeting_critical: 5.0`) is set in `benchmarks.json` but not currently exercised — all Honeycomb campaigns are prospecting. If retargeting campaigns are added, set `campaign_defaults.type` accordingly or extend the script to look up campaign type per-ad.
