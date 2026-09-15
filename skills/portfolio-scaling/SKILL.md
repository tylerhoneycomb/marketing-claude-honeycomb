---
name: portfolio-scaling
description: Weekly structural diagnosis per vertical (scalable / stable / saturating / over-invested / new-audience-needed). Proposes pool-based budget reallocation with a 12% weekly cap shared with the daily optimizer.
---

# Portfolio Scaling

## Purpose

The daily budget optimizer adjusts each campaign by ±2-4% based on 14-day CPL rank and lead trend. That's a short-horizon, campaign-grain signal. This skill adds the missing **structural** layer: 12-week trailing diagnoses per vertical to answer "is this vertical *able* to absorb more spend?" — which short-window scoring can't see.

> **Reallocation retired 2026-09-15.** At Tyler's request the skill is now
> **diagnosis only**: it classifies verticals and reports them, and proposes
> no budget moves at all. The `Compute reallocation` workflow step, the
> `scaling-queue-write` registration, the approval links and the Wednesday
> execution are all gone (`executeStrategicChanges` in `Code.js` is guarded
> and its stale tokens cleared). `compute_reallocation.py` is kept on disk,
> unwired, so the pool maths can be revived without rewriting it. Sections
> below that describe the pool, the lockout and the approval flow are
> retained as reference for that revival — **they do not run today**.

It produces one deliverable:

1. **Scaling labels** — a weekly Tuesday Slack brief diagnosing each vertical
   Meta is currently delivering, plus any audience actions those verticals
   imply.

It never modifies the optimizer's logic, scoring, or step sizes.

## Scripts

Two scripts, run in sequence.

```
python3 skills/portfolio-scaling/scripts/compute_scaling_profiles.py
python3 skills/portfolio-scaling/scripts/compute_reallocation.py [--write-log]
```

The first writes `data/derived/scaling_profiles.json` and prints a one-screen JSON summary to stdout. **That is the only script the workflow runs.**

The second is **retired and unwired (2026-09-15)**: it reads that file plus `benchmarks.json` and would write `data/derived/reallocation.json`, but nothing calls it and that output file has been removed from the repo. Its `--write-log` POST to `?action=scaling-write` stopped with it, so the `scaling_log` Sheet tab no longer accretes rows — the weekly classification record is the git history of `scaling_profiles.json`, committed on every run. Note that `verticals` now holds only verticals with an ACTIVE campaign, so check that is still the input you want before reviving it.

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
| `total_ic_conversions` | Secondary JSON field only — too sparse to classify on, and never rendered in the brief. `cpicp` / `ic_rate` are still emitted for the `scaling_log` wire contract but are not printed. |

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

## The 12% weekly cap _(optimizer-side only since 2026-09-15 — strategic movement no longer contributes)_

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

**Since 2026-09-15 the guard extends to the brief itself.** A vertical whose
campaigns are ALL paused is not reported at all:
`compute_scaling_profiles.py` emits it under `inactive_verticals` instead of
`verticals`, and the brief renders only `verticals`. The 2026-09-15 brief
opened with three all-paused verticals — `broad` (stable), `health, fitness &
personal care` and `craft producers & bars` (both tagged `over-invested`, with
warning icons) — while the only two campaigns Meta was delivering appeared
last as "too new to classify" afterthoughts. Diagnosing dead inventory as
over-invested invites action on something inert.

A vertical rejoins `verticals` on its own the moment one of its campaigns
goes ACTIVE again; nothing needs re-enabling.

`apps-script/Code.js:getVerticalClassification_` also reads `verticals`, and
the narrowing is safe there: budget proposals require `effective_status ==
"ACTIVE"`, so every campaign it can act on belongs to a vertical still
present, and the lookup already returns null gracefully otherwise.

The same guard applies to the portfolio total. `portfolio.current_total_daily_cents`,
the tolerance headroom and `optimizer_cycles_this_week` count ACTIVE campaigns
only; `portfolio.paused_total_daily_cents` carries the rest for transparency.
Before this (through the 2026-09-08 run) the total summed paused budgets too,
read $24,172/week "current" against a $9,000 target, and scaled every increase
to zero while flagging `knockdown_risk` every week.

## Reallocation pool _(retired 2026-09-15 — reference only, does not run)_

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

## Lockout _(retired 2026-09-15 — reference only, does not run)_

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

### reallocation.json _(retired 2026-09-15 — no longer produced)_

Kept as reference for a future revival of the pool maths. Nothing writes this
file today and the committed copy was deleted.

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
cost-per-lead are the headline of every section; the brief carries leads,
CPL, spend and delivery diagnostics (frequency, CPM, elasticity) and
nothing else. Title the message `*Honeycomb Scaling — <date>*`.

Since 2026-09-15 it has **two** sections and proposes nothing. There is no
pool section, no approval ask, no approve/reject links, and no last-week
evaluation — nothing executes any more, so there is nothing to evaluate.

**Only live verticals are ever rendered.** `compute_scaling_profiles.py`
emits verticals with at least one ACTIVE campaign under `verticals`, and
everything whose campaigns are all paused under `inactive_verticals`. The
brief renders every key in `verticals` and never reads `inactive_verticals`.
It must not name, count or allude to a paused campaign or a retired
vertical. (The 2026-09-15 brief led with three all-paused verticals tagged
`over-invested` while the only two campaigns Meta was delivering appeared
last; that is the failure this rule prevents.)

1. **Scaling labels** — open with ONE portfolio line built from
   `portfolio.total_leads`, `portfolio.total_spend` and `portfolio.cpl`,
   then the live-spend line from `portfolio.current_total_daily_cents` and
   `.active_campaign_count`:

   ```
   Portfolio (12w): 1,240 leads · $17,600 spend · CPL $14.19
   Active portfolio: $300/day ($2,100/wk) across 2 ACTIVE campaigns.
   ```

   `portfolio.median_cpl` is NOT printed: it spans every vertical the
   window ever saw, most of them retired, so it compares live performance
   against dead campaigns. Never add `portfolio.paused_total_daily_cents`
   to the active figure.

   Then one line per vertical, **sorted by CPL ascending** within each class
   group (scalable → stable → saturating / over-invested); the JSON is
   already in that order. Format:

   ```
   ✅ broad — scalable (directional) · CPL $12.80 (442 leads / $5,660) · r=0.11 · share 32%
   ```

   Emoji: ✅ scalable, ── stable, ⚠️ saturating / over-invested. Tag
   `directional` confidence explicitly. A vertical classified
   `insufficient` prints as a one-liner after the classified list:
   `🆕 ifw-broad — too new to classify · CPL $15.10 on 180 leads (3 weeks,
   1 active campaign)`. These are live campaigns with too little history to
   judge, not a problem to flag. If `cpl` is null print `CPL —` and place
   the vertical last in its group; never substitute another cost figure.
   Every JSON field the schema above marks `secondary, not printed` is
   never rendered — not as a headline, sort key, secondary line,
   parenthetical or trailing token.

   If `verticals` is empty, say so in one line ("No ACTIVE campaigns to
   report this week.") and move to section 2.

2. **Audience action required** — for each vertical in `verticals` with
   `new_audience_needed` true, show CPL + lead count first
   (`CPL $X on N leads (12w)`), then the frequency / CPM diagnosis and the
   duplicate-ad-set recommendation. Skip the whole section when no vertical
   qualifies.

**Never** propose, imply or invite a budget change, and never write an
approve or reject URL. There is no approval flow for this brief.

## Status comment one-liner

Write to `/tmp/agent_status.txt` before exiting:

```
leads=<N> cpl=$<X> verticals=<N> scalable=<N> saturating=<N> over_invested=<N> insufficient=<N> new_audience_needed=<N> diagnosis_only
```

`leads` and `cpl` are `portfolio.total_leads` / `portfolio.cpl` (12-week) so the issue #48 log is lead-legible at a glance. `verticals` counts the ACTIVE verticals rendered, not every vertical in the window. The pool tokens (`freed` / `allocated` / `net` / `knockdown_risk`) were dropped on 2026-09-15 with the reallocation itself.

The workflow's status step posts this to issue #48 alongside the run conclusion.

## What this skill does NOT do

- Modify the optimizer's daily logic, scoring, or step sizes.
- Hardcode any threshold; everything tunable lives in `benchmarks.json:scaling`.
- Propose, approve or execute any budget change. Since 2026-09-15 the brief is diagnosis-only: no pool, no proposal, no approval links, and `executeStrategicChanges` in `Code.js` is guarded off.
- Touch the Meta API to write. It only ever reads.
- Render a vertical whose campaigns are all paused. Those live in `inactive_verticals` and never reach the brief.
