---
name: portfolio-scaling
description: Weekly structural diagnosis per vertical (scalable / stable / saturating / over-invested / new-audience-needed). Proposes pool-based budget reallocation with a 12% weekly cap shared with the daily optimizer.
---

# Portfolio Scaling

## Purpose

The daily budget optimizer adjusts each campaign by ±2-4% based on 14-day CPL rank and lead trend. That's a short-horizon, campaign-grain signal. This skill adds the missing **structural** layer: 12-week trailing diagnoses per vertical to answer "is this vertical *able* to absorb more spend?" — which short-window scoring can't see.

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
| `elasticity_r` | Pearson correlation of weekly spend vs weekly CPL. Only weeks with ≥`min_weekly_conversions` (10 leads) count. |
| `cpl_degradation` | Median-split the qualifying weeks by spend; compare avg CPL of high-spend half vs low-spend half. |
| `frequency_trend` | Linear-regression slope of spend-weighted weekly frequency over the last `frequency_trend_saturation_weeks` (4). Labelled rising / flat / falling at ±5% of mean. |
| `cpm_trend` | Same, on CPM, over `cpm_trend_weeks` (4). |
| `cpl` | Total spend / total leads, last 12 weeks. Compared to portfolio median. This is the cost axis for classification, for every sort order, and for every number the brief headlines. |
| `total_ic_conversions` | Leads that reached an IC decision, last 12 weeks. Secondary only — IC is too sparse to classify on, and it never appears in the brief as a headline, sort key, or threshold. `cpicp` / `ic_rate` are still emitted for the `scaling_log` wire contract but are not printed. |

Vertical assignment: campaign names of the form `<AD|ICD|LEADS|RevN>-<vertical>-Q<N>-<YYYY>` (optional `PAUSED - ` prefix) bucket by `<vertical>` — the prefix is the objective, not the audience, so `LEADS-Broad-Q3-2026` continues `ICD-Broad-Q2-2026` under `broad`, and `LEADS-IFW-Broad-Q3-2026` is `ifw-broad`. The regex is duplicated in `skills/creative-intelligence/scripts/build_creative_dataset.py`; keep the two in sync.

Classification rule:

| Condition | Class |
|---|---|
| `|r| < 0.2` | **scalable** |
| `0.2 ≤ |r| < 0.5` | **stable** |
| `|r| ≥ 0.5` AND `cpl_degradation > 30%` AND `cpl > portfolio_median_cpl` | **over-invested** |
| `|r| ≥ 0.5` AND `cpl_degradation > 30%` (CPL not above median) | **saturating** |
| `|r| ≥ 0.5` AND no CPL degradation signal | **stable** |

Modifier (orthogonal to classification): **`new_audience_needed`** when `frequency_trend == rising` AND `cpm_trend == rising` over the same 4-week window. This fires *before* any single campaign in the vertical hits the optimizer's frequency-2.0 watch threshold — it's a vertical-level early warning that audience expansion (not budget) is the lever.

## Confidence

| Weeks with ≥10 leads (`min_weekly_conversions`) | Label | Effect |
|---|---|---|
| ≥ `min_weeks_confident` (10) | `confident` | Full-sized proposals. |
| ≥ `min_weeks_directional` (6) | `directional` | Tagged "directional" in brief; same proposals but lower confidence. |
| < 6 | `insufficient` | No classification, no proposal, no optimizer tag. Still printed in the brief when it has an ACTIVE campaign (see Slack output rules). |

A vertical whose campaigns all fall below the optimizer's `LIFETIME_MIN_CONVERSIONS = 10` gate (lifetime leads per campaign, summed from `weekly_rollup.meta_conversions`) is excluded from optimizer-eligible verticals (`optimizer_eligible = false` in the JSON). The gate used to count IC, which marked the live LEADS campaigns ineligible while paused legacy campaigns with old IC history stayed eligible.

## The 12% weekly cap

**Total |change_pct| per campaign per week, summed across all sources, is capped at 12%.** That's the single hard rail on the entire pipeline. Sources counted:

- Optimizer increase / decrease cycles (typical ±2%, max ±4% reductions)
- Portfolio-level 1% knockdown (identifiable via `"portfolio knockdown"` substring in `signal_reasons`)
- Strategic reallocation entries (Session 2 will tag these via the `source` column)

Computed empirically from `budget_queue` rows with `status == "executed"`. The optimizer runs daily but doesn't always produce changes — proposals can expire unapproved. Counting actual movement (not assumed cycles) is the only way to keep the cap honest.

`compute_scaling_profiles.py` does this by calling `?action=scaling-queue-read&since=<previous_tuesday>`.

## Only ACTIVE campaigns are actionable

Every campaign considered for a decrease, an increase, or absorption capacity
must have `effective_status == "ACTIVE"`. A budget move against a PAUSED or
ARCHIVED campaign is inert at best and misleading at worst.

This guard was added 2026-09-09 after the brief proposed a −397 cents/day cut
to `ICD-Health, Fitness & Personal Care-Q2-2026`, a campaign whose ad sets had
all been paused since 2026-08-17. At the time 26 of 29 campaigns were paused
and 86% of the reported portfolio budget belonged to campaigns Meta was not
delivering, so the portfolio total and the tolerance band were both meaningless.

If every campaign in a saturating vertical is paused, propose nothing for that
vertical and say so — do not fall back to the paused budget.
`reallocation.pool.paused_saturating_verticals` lists them, and
`pool.increase_skip_reason` says why nothing absorbed the pool when
`increases` is empty (e.g. "no scalable/stable vertical has an ACTIVE
campaign").

The same guard applies to the portfolio total. `portfolio.current_total_daily_cents`,
the tolerance headroom and `optimizer_cycles_this_week` count ACTIVE campaigns
only; `portfolio.paused_total_daily_cents` carries the rest for transparency.
Before this (through the 2026-09-08 run) the total summed paused budgets too,
read $24,172/week "current" against a $9,000 target, and scaled every increase
to zero while flagging `knockdown_risk` every week.

## Reallocation pool

Pool, not pairings. Decreases free dollars; the pool is then allocated across receiving verticals.

**Decreases** (per saturating + over-invested vertical):
- `severity = (|r| - elasticity_saturating_threshold) / (1.0 - elasticity_saturating_threshold)`, capped at 1.0
- Over-invested verticals get a 1.5× severity boost (capped at 1.0)
- Per campaign: `desired_cut_pct = severity × weekly_remaining_pct`
- Floor protection: post-change daily ≥ `$25 × (1 + campaign_floor_buffer_pct)` = $26/day

**Increases** (across scalable + stable verticals):
- Weight = inverse CPL. Stable gets 0.5× weight (secondary priority)
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
    "current_total_daily_cents", "current_total_weekly_dollars",   // ACTIVE only
    "active_campaign_count", "paused_total_daily_cents",
    "target_weekly_spend", "weekly_spend_tolerance",
    "tolerance_headroom_daily_cents",
    "total_leads", "total_spend", "cpl", "median_cpl",             // 12-week headline
    "total_ic_conversions", "median_cpicp", "median_ic_rate",      // secondary, not printed
    "optimizer_cycles_this_week"
  },
  "verticals": {                                                   // CPL ascending, null CPL last
    "<vertical>": {
      "classification", "confidence", "new_audience_needed",
      "elasticity_r", "elasticity_n_weeks",
      "cpl", "spend_share_pct",
      "ic_rate", "cpicp",                                          // secondary, not printed
      "avg_frequency", "frequency_trend", "frequency_series",
      "cpm_trend", "cpm_series",
      "high_spend_cpl_degradation_pct",
      "total_spend", "total_conversions", "total_ic_conversions",
      "weeks_in_window", "weeks_with_conversions",
      "campaign_ids", "campaign_names", "active_campaign_count",
      "optimizer_eligible"
    }
  },
  "campaigns": {
    "<campaign_id>": {
      "campaign_id", "campaign_name", "vertical",
      "effective_status", "daily_budget_cents",
      "lifetime_leads",
      "weekly_consumed_pct", "weekly_remaining_pct",
      "knockdown_applied_this_week"
    }
  }
}
```

`total_conversions` is leads (traced `weekly_rollup.meta_conversions` ← `rolling_data.conversions` ← the lead priority chain in `Code.js`).

### reallocation.json

```
{
  "computed_at", "today", "lockout_until", "affected_campaign_ids",
  "pool": {
    "freed_daily_cents", "allocated_daily_cents",
    "net_change_daily_cents", "net_change_type",
    "portfolio_current_daily_cents", "portfolio_active_campaign_count",   // ACTIVE only
    "portfolio_post_change_daily_cents",
    "portfolio_post_change_weekly_dollars",
    "target_weekly_dollars", "tolerance_weekly_dollars",
    "knockdown_risk",
    "paused_saturating_verticals", "increase_skip_reason"
  },
  "decreases": [{vertical, campaign_id, campaign_name,
                 current_daily_cents, change_cents, change_pct,
                 post_change_cents, classification, elasticity_r,
                 cpl, total_leads, vertical_spend,
                 remaining_headroom_pct, reason}],
  "increases": [{vertical, campaign_id, campaign_name,
                 current_daily_cents, change_cents, change_pct,
                 post_change_cents, classification,
                 cpl, total_leads, vertical_spend,
                 remaining_headroom_pct, allocation_weight_reason}],
  "audience_actions": [{vertical, diagnosis, action,
                        creative_prescription, creative_source}]
}
```

`cpl`, `total_leads` and `vertical_spend` on each proposal row are the vertical's 12-week figures (per-campaign lead counts are not in the rollup aggregation). `diagnosis` opens with `CPL $X on N leads (12w)` before the frequency / CPM / elasticity detail.

`creative_prescription` and `creative_source` are `null` when the creative cache or `?action=creative-intelligence-read` aren't available — the audience action still ships with diagnosis + suggested targeting expansion, just without the copy/visual line.

### scaling_log sheet

Written to via `?action=scaling-write` on `--write-log`. Auto-creates the `scaling_log` tab on first call. One row per vertical per run: `date, vertical, classification, confidence, elasticity_r, ic_rate, cpicp, spend_share_pct, avg_frequency, frequency_trend, cpm_trend, new_audience_needed, weeks_with_conversions, contributed_to_pool, received_from_pool, recorded_at`.

> **Wire contract is still IC-named.** The `handleScalingWrite_` handler in
> `apps-script/Code.js` reads its payload keys by name, so the legacy
> `cpicp` and `ic_rate` keys must keep being sent even though every metric above is
> lead-based. Unrecognised keys are written as blanks — the script also
> sends `cpl` and `total_leads` so the Sheet fills them in the moment the
> header gains those columns, but **today `scaling_log` stores no CPL and no
> lead count**; its only cost columns are `ic_rate` / `cpicp`. Renaming needs
> the matching `Code.js` edit plus a redeploy, tracked separately — the
> Apps Script deploy pipeline has not run since 2026-06-23. Until then the
> brief must not use `scaling-log-read` as a cost source (see §4 below).


## Slack output rules

The skill prompt (NOT the script) composes the Slack message. Leads and
cost-per-lead are the headline of every section; IC appears once, as a
trailing secondary line. Title the message `*Honeycomb Scaling — <date>*`.
It has four sections, in order:

1. **Scaling labels** — open with ONE portfolio line built from
   `portfolio.total_leads`, `portfolio.total_spend`, `portfolio.cpl` and
   `portfolio.median_cpl`:

   ```
   Portfolio (12w): 1,240 leads · $17,600 spend · CPL $14.19 · median vertical CPL $16.40
   ```

   Then one line per vertical, **sorted by CPL ascending** within each class
   group (scalable → stable → saturating / over-invested); the JSON is
   already in that order. Format:

   ```
   ✅ broad — scalable (directional) · CPL $12.80 (442 leads / $5,660) · r=0.11 · share 32%
   ```

   Emoji: ✅ scalable, ── stable, ⚠️ saturating / over-invested. Tag
   `directional` confidence explicitly. Skip `insufficient` verticals
   **unless** `active_campaign_count > 0` — those are the campaigns
   Meta is delivering right now, so print them as a one-liner after the
   classified list: `🆕 ifw-broad — too new to classify · CPL $15.10 on 180
   leads (3 weeks, 1 active campaign)`. If `cpl` is null print `CPL —`
   and place the vertical last in its group; never substitute `cpicp`.
   Close the section with a single secondary line built from
   `portfolio.total_ic_conversions`: `_of which N reached an IC decision_`
   (omit when 0). Never print `ic_rate`, `cpicp`, `median_cpicp`,
   `median_ic_rate` or per-vertical IC counts, and never use them to order
   or emphasise anything.

2. **Strategic reallocation** — frame the pool in lead terms before the
   dollar mechanics: `Moving $F/day out of <saturating verticals, CPL $A on
   N leads> into <scalable verticals, CPL $B on M leads>` (each proposal
   row carries `cpl` / `total_leads` / `vertical_spend`). Then pool
   freed/allocated dollars, net portfolio change, and active-portfolio
   weekly spend vs target ± tolerance (`pool.portfolio_current_daily_cents`
   counts ACTIVE campaigns only; say "active portfolio"). Per affected
   campaign:

   ```
   ↓ ICD-Health…-Q2-2026: $125 → $121/day (−3.2%) · health saturating · vertical CPL $22.40 on 88 leads · headroom used 4% (opt 2% + strat 2%)
   ```

   When `knockdown_risk: true`, add a one-line "may trigger 1% knockdown
   next cycle" note. If `pool.paused_saturating_verticals` is non-empty,
   say `<vertical> saturating but every campaign PAUSED — no move proposed`
   rather than leaving it out. If `increases` is empty, print
   `pool.increase_skip_reason` verbatim (e.g. "no scalable/stable vertical
   has an ACTIVE campaign — nothing to absorb the pool"). End with the
   lockout window: "Lockout: Wed-Mon. Affected campaigns are locked out of
   daily-optimizer moves until <next Tuesday>" — the lockout is recorded
   either way, but do not describe the optimizer as running: `runBudgetAnalysis`
   has been early-returned since 2026-09-09. Then the approval ask — `Approve to shift $F/day
   toward lower-CPL verticals` — with the two-step approval link pair. If
   registration was refused (`{"error":"unauthorized"}`), post without links
   and prefix the section with `⚠️ registration refused (EXEC_SHARED_SECRET
   missing) — no approval links this week`; never fabricate URLs.

3. **Audience action required** — for each `new_audience_needed` vertical,
   show CPL + lead count first (the `diagnosis` string already opens with
   `CPL $X on N leads (12w)`), then the frequency / CPM diagnosis and the
   duplicate-ad-set recommendation. Include the creative prescription line
   if `creative_source` is set.

4. **Last week's evaluation** — if `?action=scaling-queue-read&since=<last
   Tuesday>` returns rows with `source=strategic` and `status=executed`,
   report per affected vertical:

   ```
   broad: CPL $14.20 → $13.10 (395 → 431 leads/wk) · frequency 1.62 → 1.71 · classification stable → scalable
   ```

   **CPL and lead counts do not come from `scaling-log-read`** — the
   `scaling_log` Sheet only stores `ic_rate` / `cpicp` today (see the wire
   contract note above). Compute them from `?action=rollup` (read-only,
   ungated): sum `spend` and `meta_conversions` per vertical for the two
   most recent `week_start` values, and CPL = spend / meta_conversions.
   Use `scaling-log-read` only for classification, `frequency_trend`,
   `avg_frequency` and the pool flags. If the rollup rows are unavailable,
   write `prior-week CPL unavailable` — never cite `cpicp` or `ic_rate` as
   the movement metric. End with `Verdict: reallocation helped / hurt /
   inconclusive on CPL`. Skip the section entirely if no strategic rows
   executed.

## Status comment one-liner

Write to `/tmp/agent_status.txt` before exiting:

```
leads=<N> cpl=$<X> verticals=<N> scalable=<N> saturating=<N> over_invested=<N> new_audience_needed=<N> freed=$<X>/day allocated=$<Y>/day net=<zero_sum|net_positive|net_negative> knockdown_risk=<bool>
```

`leads` and `cpl` are `portfolio.total_leads` / `portfolio.cpl` (12-week) so the issue #48 log is lead-legible at a glance.

The workflow's status step posts this to issue #48 alongside the run conclusion.

## What this skill does NOT do

- Modify the optimizer's daily logic, scoring, or step sizes.
- Hardcode any threshold; everything tunable lives in `benchmarks.json:scaling`.
- Execute changes against the Meta API directly. The strategic execution path reuses `executeBudgetChanges`'s helper (Session 2) via the existing approve/reject flow.
- Operate on campaigns with `learning_stage_info.status == "LEARNING"` (the snapshot pipeline's `compute_signals.py` filter handles that gate; defensive re-check happens at compute_reallocation time via `weekly_remaining_pct == 0` for any campaign Meta is still calibrating).
- Auto-approve. Tyler approves the Tuesday brief manually, same two-step confirmation as the daily optimizer.
