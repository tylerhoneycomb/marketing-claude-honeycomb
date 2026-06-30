---
name: portfolio-scaling
description: Weekly structural diagnosis per vertical (scalable / stable / saturating / over-invested / insufficient, plus a new-audience-needed modifier). Proposes pool-based budget reallocation with a 12% weekly cap shared with the daily optimizer.
---

# Portfolio Scaling

## Purpose

The daily budget optimizer adjusts each campaign by ±2-4% based on 14-day CPICP rank and ICP trend. That's a short-horizon, campaign-grain signal. This skill adds the missing **structural** layer: 12-week trailing diagnoses per vertical to answer "is this vertical *able* to absorb more spend?" — which short-window scoring can't see.

It produces two deliverables:

1. **Scaling labels** that tag each daily optimizer proposal — informational, no logic change to the optimizer.
2. **A weekly Tuesday reallocation** — a separate Slack brief that proposes shifts from saturating verticals to scalable ones via a pool, sharing a 12% weekly cap with the optimizer.

It never modifies the optimizer's logic, scoring, or step sizes. The 12% cap is a hard rail; the lockout window prevents the optimizer from acting on campaigns the strategic reallocation just touched.

## Scripts

Two scripts, run in sequence.

```
python3 skills/portfolio-scaling/scripts/compute_scaling_profiles.py
python3 skills/portfolio-scaling/scripts/compute_reallocation.py [--write-log]
```

The first writes `data/derived/scaling_profiles.json` and prints a one-screen JSON summary to stdout. The second reads that file plus `benchmarks.json` (and optionally the creative intelligence cache) and writes `data/derived/reallocation.json`. Pass `--write-log` to POST per-vertical rows to `?action=scaling-write` (the agent workflow does this; manual runs typically skip it).

Required env vars:
- `META_ACCESS_TOKEN` — for current campaign daily_budget lookups
- `EXEC_ENDPOINT` — optional override; falls back to `exec_endpoint` in `benchmarks.json`

Both scripts honor `data/config/benchmarks.json:scaling.*` for thresholds. Never hardcode a threshold in the scripts.

## Classification matrix

Per vertical, computed over the last 12 weeks:

| Signal | Computation |
|---|---|
| `elasticity_r` | Pearson correlation of weekly spend vs weekly CPL. Only weeks with ≥`min_weekly_conversions` (3) count. |
| `cpl_degradation` | Median-split the qualifying weeks by spend; compare avg CPL of high-spend half vs low-spend half. |
| `frequency_trend` | Linear-regression slope of spend-weighted weekly frequency over the last `frequency_trend_saturation_weeks` (4). Labelled rising / flat / falling at ±5% of mean. |
| `cpm_trend` | Same, on CPM, over `cpm_trend_weeks` (4). |
| `cpicp` | Total spend / total IC, last 12 weeks. Compared to portfolio median. |

Classification rule:

| Condition | Class |
|---|---|
| `|r| < 0.2` | **scalable** |
| `0.2 ≤ |r| < 0.5` | **stable** |
| `|r| ≥ 0.5` AND `cpl_degradation > 30%` AND `cpicp > portfolio_median_cpicp` | **over-invested** |
| `|r| ≥ 0.5` AND `cpl_degradation > 30%` (CPICP not above median) | **saturating** |
| `|r| ≥ 0.5` AND no CPL degradation signal | **stable** |

Modifier (orthogonal to classification): **`new_audience_needed`** when `frequency_trend == rising` AND `cpm_trend == rising` over the same 4-week window. This fires *before* any single campaign in the vertical hits the optimizer's frequency-2.0 watch threshold — it's a vertical-level early warning that audience expansion (not budget) is the lever.

## Confidence

| Weeks with ≥3 conversions | Label | Effect |
|---|---|---|
| ≥ `min_weeks_confident` (10) | `confident` | Full-sized proposals. |
| ≥ `min_weeks_directional` (6) | `directional` | Tagged "directional" in brief; same proposals but lower confidence. |
| < 6 | `insufficient` | No classification, no proposal, no optimizer tag. |

A vertical whose campaigns all fall below the optimizer's `LIFETIME_MIN_CONVERSIONS = 10` gate is excluded from optimizer-eligible verticals (`optimizer_eligible = false` in the JSON).

## The 12% weekly cap

**Total |change_pct| per campaign per week, summed across all sources, is capped at 12%.** That's the single hard rail on the entire pipeline. Sources counted:

- Optimizer increase / decrease cycles (typical ±2%, max ±4% reductions)
- Portfolio-level 1% knockdown (identifiable via `"portfolio knockdown"` substring in `signal_reasons`)
- Strategic reallocation entries (Session 2 will tag these via the `source` column)

Computed empirically from `budget_queue` rows with `status == "executed"`. The optimizer runs daily but doesn't always produce changes — proposals can expire unapproved. Counting actual movement (not assumed cycles) is the only way to keep the cap honest.

`compute_scaling_profiles.py` does this by calling `?action=scaling-queue-read&since=<previous_tuesday>`.

## Reallocation pool

Pool, not pairings. Decreases free dollars; the pool is then allocated across receiving verticals.

**Decreases** (per saturating + over-invested vertical):
- `severity = (|r| - elasticity_saturating_threshold) / (1.0 - elasticity_saturating_threshold)`, capped at 1.0
- Over-invested verticals get a 1.5× severity boost (capped at 1.0)
- Per campaign: `desired_cut_pct = severity × weekly_remaining_pct`
- Floor protection: post-change daily ≥ `$25 × (1 + campaign_floor_buffer_pct)` = $26/day

**Increases** (across scalable + stable verticals):
- Weight = inverse CPICP. Stable gets 0.5× weight (secondary priority)
- Vertical's pool share = its weight / total weight
- Per campaign: distributed proportionally by current daily budget, capped by `weekly_remaining_pct`

**Tolerance band** (post-change weekly portfolio spend):
- Hard bounds: `[target − tolerance, target + tolerance]` — readable from `?action=get_spend_goal`
- Above target+tolerance → scale increases down proportionally
- Below target−tolerance → scale decreases down proportionally
- `knockdown_risk: true` in the output when post-change > target (even if still within tolerance) — flags that the optimizer's next cycle may apply a 1% knockdown. Do **not** pre-deduct the knockdown from increases; it would undersize the reallocation, and it'll get counted via the normal headroom path on the next cycle.

## Lockout

After the Tuesday brief is approved and Wednesday 3 AM execution applies the changes, the optimizer is locked out of touching the affected campaigns through end-of-Monday (Wed–Mon, 6 calendar days). `SCALING_LOCKOUT_UNTIL` Script Property is set to next Tuesday 00:00 UTC; the optimizer's Tuesday-morning cycle sees lockout already expired and is free to act.

The lockout list (`SCALING_AFFECTED_CAMPAIGN_IDS`) covers every campaign in the `decreases` AND `increases` arrays — both sides of the pool are protected from optimizer interference during the evaluation window.

## Output schemas

### scaling_profiles.json

```
{
  "computed_at", "today", "previous_tuesday", "elasticity_window_weeks",
  "benchmarks": {... copied from benchmarks.json ...},
  "portfolio": {
    "current_total_daily_cents", "current_total_weekly_dollars",
    "target_weekly_spend", "weekly_spend_tolerance",
    "tolerance_headroom_daily_cents",
    "median_cpicp", "median_ic_rate", "optimizer_cycles_this_week"
  },
  "verticals": {
    "<vertical>": {
      "classification", "confidence", "new_audience_needed",
      "elasticity_r", "elasticity_n_weeks",
      "ic_rate", "cpicp", "spend_share_pct",
      "avg_frequency", "frequency_trend", "frequency_series",
      "cpm_trend", "cpm_series",
      "high_spend_cpl_degradation_pct",
      "total_spend", "total_conversions", "total_ic_conversions",
      "weeks_in_window", "weeks_with_conversions",
      "campaign_ids", "campaign_names", "optimizer_eligible"
    }
  },
  "campaigns": {
    "<campaign_id>": {
      "campaign_id", "campaign_name", "vertical",
      "effective_status", "daily_budget_cents",
      "lifetime_ic_conversions",
      "weekly_consumed_pct", "weekly_remaining_pct",
      "knockdown_applied_this_week"
    }
  }
}
```

### reallocation.json

```
{
  "computed_at", "today", "lockout_until", "affected_campaign_ids",
  "pool": {
    "freed_daily_cents", "allocated_daily_cents",
    "net_change_daily_cents", "net_change_type",
    "portfolio_current_daily_cents", "portfolio_post_change_daily_cents",
    "portfolio_post_change_weekly_dollars",
    "target_weekly_dollars", "tolerance_weekly_dollars",
    "knockdown_risk"
  },
  "decreases": [{vertical, campaign_id, campaign_name,
                 current_daily_cents, change_cents, change_pct,
                 post_change_cents, classification, elasticity_r,
                 remaining_headroom_pct, reason}],
  "increases": [{vertical, campaign_id, campaign_name,
                 current_daily_cents, change_cents, change_pct,
                 post_change_cents, classification, cpicp,
                 remaining_headroom_pct, allocation_weight_reason}],
  "audience_actions": [{vertical, diagnosis, action,
                        creative_prescription, creative_source}]
}
```

`creative_prescription` and `creative_source` are `null` when the creative cache or `?action=creative-intelligence-read` aren't available — the audience action still ships with diagnosis + suggested targeting expansion, just without the copy/visual line.

### scaling_log sheet

Written to via `?action=scaling-write` on `--write-log`. Auto-creates the `scaling_log` tab on first call. One row per vertical per run: `date, vertical, classification, confidence, elasticity_r, ic_rate, cpicp, spend_share_pct, avg_frequency, frequency_trend, cpm_trend, new_audience_needed, weeks_with_conversions, contributed_to_pool, received_from_pool, recorded_at`.

## Slack output rules

The skill prompt (NOT the script) composes the Slack message. It has four sections, in order:

1. **Scaling labels** — every vertical with classification + supporting numbers (`r`, CPICP, IC rate, spend share). One line per vertical with the appropriate emoji (✅ scalable, ── stable, ⚠️ saturating/over-invested). Tag `directional` confidence verticals explicitly. Skip `insufficient`.

2. **Strategic reallocation** — pool freed/allocated dollars, net portfolio change, target-vs-actual weekly spend, and headroom-consumed-this-week per affected campaign (showing optimizer + strategic split). When `knockdown_risk: true`, add a one-line "may trigger 1% knockdown next cycle" note. End with the lockout window: "Lockout: Wed-Mon. Optimizer paused on affected campaigns until <next Tuesday>." Append the two-step approval link pair.

3. **Audience action required** — for each `new_audience_needed` vertical, show diagnosis + duplicate-ad-set recommendation. Include the creative prescription line if `creative_source` is set.

4. **Last week's evaluation** — if a strategic reallocation was approved last Tuesday, summarize what happened (CPICP movement, frequency response, classification confirmed/changing). Skip if no prior reallocation. The Hive Mind handler `?action=scaling-log-read` provides historical rows for this synthesis.

## Status comment one-liner

Write to `/tmp/agent_status.txt` before exiting:

```
verticals=<N> scalable=<N> saturating=<N> over_invested=<N> new_audience_needed=<N> freed=$<X>/day allocated=$<Y>/day net=<zero_sum|net_positive|net_negative> knockdown_risk=<bool>
```

The workflow's status step posts this to issue #48 alongside the run conclusion.

## What this skill does NOT do

- Modify the optimizer's daily logic, scoring, or step sizes.
- Hardcode any threshold; everything tunable lives in `benchmarks.json:scaling`.
- Execute changes against the Meta API directly. The strategic execution path reuses `executeBudgetChanges`'s helper (Session 2) via the existing approve/reject flow.
- Operate on campaigns with `learning_stage_info.status == "LEARNING"` (the snapshot pipeline's `compute_signals.py` filter handles that gate; defensive re-check happens at compute_reallocation time via `weekly_remaining_pct == 0` for any campaign Meta is still calibrating).
- Auto-approve. Tyler approves the Tuesday brief manually, same two-step confirmation as the daily optimizer.
