# Skill: Fatigue Monitor

## Purpose

Identify ads showing creative fatigue (declining CTR, rising frequency) at a level of specificity Tyler can act on: which ad, in which ad set, in which campaign, with what severity, and what to do.

## When to invoke

- Tyler asks "what's fatiguing?" / "any fatigue?" / "anything I should refresh?"
- The daily-check skill flagged severity = critical or warning and Tyler wants the full list
- A scheduled loop is running and this is one of the steps

## Inputs

1. `data/derived/fatigue_signals.json` — the per-ad fatigue evaluation (already computed)
2. `data/derived/summary.json` — top-line counts
3. `data/snapshots/<latest>/adsets.json` — to read learning-phase status if needed
4. `data/creatives/creatives.json` — for thumbnail URLs and ad copy when surfacing recommendations
5. `data/config/benchmarks.json` — thresholds (display them in the output for transparency)

## What "fatigue" means here

The compute step (`scripts/compute_signals.py`) flags an ad with one or more of:

| Flag | Meaning |
|---|---|
| `ctr_declining` | Recent-half CTR ≤ baseline-half CTR by ≥ `ctr_decline_pct_7d` (default 20%) |
| `frequency_warning` | 7-day avg frequency ≥ `frequency_warning` (default 2.0) |
| `frequency_critical` | 7-day avg frequency ≥ `frequency_critical` (default 3.0) |
| `below_min_days_active` | < 3 days active — too new to judge |
| `below_min_impressions` | < 1,000 impressions in window — sample too small |
| `adset_in_learning` | Parent ad set is in Meta's learning phase — DO NOT recommend changes |

**Severity** is already computed:
- `critical` — `frequency_critical` OR (`ctr_declining` AND `frequency_warning`)
- `warning` — single fatigue flag
- `ok` — no fatigue flags or filtered by gates

Only rows with `actionable: true` should produce recommendations.

## Output format

Group by severity. Within each group, sort by total impressions descending.

```
# Fatigue report — <YYYY-MM-DD>

Window: last <N> days. Thresholds: CTR decline ≥ 20%, freq warning ≥ 2.0, freq critical ≥ 3.0.

## Critical (<count>)

### <ad_name> · <adset_name> · <campaign_name>
- Days active: 14 · Impressions: 28,400 · 7d CTR: 0.82% · 7d frequency: 3.4
- Flags: frequency_critical, ctr_declining (-32% vs baseline-half)
- Recommendation: pause this ad and replace with a fresh variant. Same audience is now seeing it 3.4× per week.

## Warning (<count>)
…

## Filtered (<count>)
- Below thresholds or in learning phase: <count> ads, not actionable yet.
```

Only include ads where `actionable: true`. The "Filtered" section is just a count — don't list them.

## Recommendations

For each actionable ad, choose ONE action from this short menu:

- **Pause + replace** — frequency_critical, OR ctr_declining with > 14 days active
- **Refresh creative** — ctr_declining and < 14 days active (audience may not be saturated yet — try a copy/image variant first)
- **Reduce ad-set budget by max 4%** — frequency_warning only, ad still profitable per `winner_bleeder.json`
- **Watch only** — actionable signal but ambiguous; surface but don't propose action

Recommendations are SUGGESTIONS, not actions. Budget changes flow through the existing Slack approval pipeline in `apps-script/Code.js`. Pausing/replacing creative is currently a manual Tyler-does-it-in-Ads-Manager step — say so.

## Constraints

- If `summary.json` shows `learning_phase_adsets_skipped > 0`, mention it once at the end so Tyler knows some ad sets weren't evaluated.
- Never propose a single budget change > 4% of current daily budget. Even at warning severity. The existing budget system caps at ±2% per cycle (±4% as a hard one-shot limit).
- If `fatigue_signals.json` is empty, say "No fatigue signals — all ads under thresholds or below the data gates." Do not fabricate.
