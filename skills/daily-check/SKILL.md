# Skill: Daily Check

## Purpose

Produce a short, action-oriented summary of yesterday's Meta ad performance using the latest ad-level snapshot. The "5 daily questions" Berman-style check, adapted for Honeycomb Credit's investment-crowdfunding context.

## When to invoke

- Tyler asks "give me the daily" / "daily check" / "what happened yesterday"
- The agent loop runs on a schedule and needs a status read
- Before any other skill (fatigue-monitor, budget-optimizer) — this orients the rest of the session

## Inputs

Read in this order:

1. `data/snapshots/<latest>/_manifest.json` — confirms the snapshot exists and is recent
2. `data/snapshots/<latest>/ad_insights.json` — yesterday's ad-level rows
3. `data/snapshots/<latest>/adset_insights.json` — yesterday's ad-set rows
4. `data/derived/summary.json` — counts of fatigue severities, winners, bleeders
5. `data/derived/fatigue_signals.json` — only if summary shows critical/warning ads
6. `data/config/benchmarks.json` — thresholds for context

If `data/snapshots/` is empty or `summary.json` is older than 36 hours, surface that as the top-line finding ("snapshot is stale — pipeline may be broken") and run skill `pipeline-health` for triage.

## The five questions

Produce a markdown response that answers, in order:

1. **Did we spend what we planned?**
   Total spend yesterday vs the per-day pro-rata of `weekly_target_spend` ($10,000 ÷ 7 ≈ $1,429/day). Flag if outside ±20%.

2. **Are we hitting our IC volume?**
   Total `ic_conversions` summed across ads yesterday. Compare to the 7-day rolling average. Flag day-over-day changes ≥ ±25%.

3. **Where is fatigue?**
   List ads in `summary.actionable_critical` and `summary.actionable_warning`. For each, name the ad, ad set, severity, and the specific flags (`ctr_declining`, `frequency_critical`, etc.).

4. **Who's winning, who's bleeding?**
   Top 3 winners and top 3 bleeders from `winner_bleeder.json`. For each, include CTR vs ad-set average and spend share.

5. **What needs a human?**
   One short bulleted list of items where a human (Tyler) should weigh in. Each item must include: the specific ad/ad set, the proposed action (pause / shift budget / refresh creative), and why. Do NOT auto-execute — recommendations only.

## Output format

```
# Daily check — <YYYY-MM-DD>

## Spend
…

## IC volume
…

## Fatigue
…

## Winners & bleeders
…

## Needs human review
- …
```

Keep the whole response under ~400 words. If there are no flags in a section, write a single sentence ("All ads under fatigue thresholds.") rather than an empty heading.

## Constraints

- Never recommend a budget change to an ad set whose `learning_stage_info.status == "LEARNING"` (already filtered by `compute_signals.py` — re-check defensively).
- Never claim a fatigue signal is actionable if the ad has < 3 days active or < 1,000 impressions. The compute step already filters these; if you encounter rows flagged `actionable: false`, do not promote them.
- All numbers come from the snapshot files. Do NOT call the Meta API directly — that's the data pipeline's job.
- Honeycomb is regulated (Reg CF). Don't draft anything that promises returns. Use existing brand voice cues from `CLAUDE.md`.
