# Skill: Budget Optimizer

## Purpose

Recommend ad-set-level budget reallocations based on ad-level performance, then route those recommendations through the existing Apps Script + Slack approval pipeline. This skill **does not change anything in Meta directly** — it produces a proposal that Tyler approves manually.

## When to invoke

- Tyler asks "what should we shift this week?" / "any budget moves?"
- After the fatigue-monitor skill identifies actionable bleeders or winners
- Twice-weekly (Wed/Fri) cadence to align with the existing budget pipeline

## How this fits the existing system

The campaign-level budget pipeline lives in `apps-script/Code.js` (`runBudgetAnalysis`, `computeBudgetSignals_`, `computeRecommendations_`, `postBudgetProposalToSlack_`). It already:

- Pulls 14-day rolling signals from `rolling_data` (Google Sheet)
- Applies a rules engine with caps (±2% per cycle, ±4% hard one-shot)
- Posts a proposal to Slack with Approve / Reject buttons
- Executes via Meta API only on human approval

This skill **adds ad-level granularity** to that flow:

1. Read derived signals from `data/derived/winner_bleeder.json`
2. Suggest which ad sets to bias up vs down based on the winner/bleeder mix inside them
3. Recommend ad-level pauses for confirmed bleeders (a manual step in Ads Manager, since the existing pipeline only moves budgets at the ad-set/campaign level)

## Inputs

1. `data/derived/winner_bleeder.json` — per-ad ranking inside its ad set
2. `data/derived/fatigue_signals.json` — to cross-reference fatigue
3. `data/snapshots/<latest>/adsets.json` — for current `daily_budget_cents`, `lifetime_budget_cents`, `optimization_goal`, `learning_stage_info`
4. `data/config/benchmarks.json` — ALL thresholds. Never hardcode.

## Decision logic

For each ad set, classify by the mix of winners and bleeders inside it:

| Pattern | Recommendation |
|---|---|
| 1+ winners, 0 bleeders, no fatigue flags | **Bias up** — recommend +2% ad-set budget |
| 1+ winners, 1+ bleeders | **Pause bleeders + hold budget** — recommend pausing the specific bleeder ads, keep ad-set spend flat |
| 0 winners, 1+ bleeders, frequency_warning at ad-set level | **Bias down** — recommend −2% ad-set budget, pause bleeders |
| All ads OK, no winners, no bleeders | **Hold** — no action |
| Ad set in learning phase | **Hold** — never touch learning-phase ad sets, no exceptions |

**Eligibility gates** (skip the ad set entirely if any apply):
- Ad set is in learning phase
- Ad set has fewer than `lifetime_min_conversions` (default 25) total conversions in the rolling window
- Ad set's parent campaign is paused (check `effective_status`)

## Output format

```
# Budget proposal — <YYYY-MM-DD>

Window: last <N> days. Caps: ±2% per cycle, ±4% hard limit.

## Ad-set budget moves (route through existing pipeline)

| Ad set | Campaign | Current daily | Proposed | Δ% | Reason |
|---|---|---|---|---|---|
| <name> | <campaign> | $40.00 | $40.80 | +2.0% | 2 winners, 0 bleeders, frequency 1.7 |
| <name> | <campaign> | $50.00 | $49.00 | -2.0% | 1 bleeder, frequency 2.4 |

## Ads to pause (manual — Ads Manager)

- <ad_name> in <adset_name> — bleeder, CTR 0.34% vs ad-set avg 0.91%, 4-day spend $48
- …

## Held (eligibility gates)

- 3 ad sets in learning phase
- 2 ad sets below 25-conversion threshold

## Next step

The ad-set budget table above can be applied via the existing `runBudgetAnalysis()` flow: the agent surfaces these directionally, the Apps Script rules engine enforces the actual ±2% / ±4% caps and writes the Slack proposal. Run `runBudgetAnalysis` from the Apps Script editor (or wait for the Wed/Fri 6 AM trigger) to materialize the proposal in Slack.
```

## Constraints

- **Never edit Meta directly.** This skill only emits proposals. The agent's job ends at "tell Tyler what to do" and "queue it for the existing approval flow."
- **Never exceed ±2% per recommendation.** The Apps Script rules engine will cap at that anyway, but this skill should not propose larger moves and have them silently clamped.
- **Never recommend new spend that violates the daily floor.** Every active ad set must remain ≥ `daily_min_cents` (default $25.00).
- **Show your math.** Every row in the table must reference the specific signal that drove it (winner count, bleeder count, frequency value).
- If `winner_bleeder.json` and `fatigue_signals.json` disagree (e.g., an ad is labeled winner but also flagged ctr_declining), defer to the fatigue signal — that's the leading indicator. Mention the conflict in the reason column.
