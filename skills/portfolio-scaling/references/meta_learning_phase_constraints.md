# Meta learning phase constraints

Reference for the portfolio-scaling skill's safety rails. The 12% weekly cap, the lockout window, and the floor buffer all derive from Meta's optimizer behavior described here. When in doubt, prefer Meta's documented behavior over reasoning from first principles.

## Learning phase basics

Meta places every ad set into a **learning phase** when:
- It's newly created
- It's edited in a way Meta classifies as "significant"
- It returns from being paused

During learning, Meta's bid algorithm is exploring the audience and hasn't converged on stable delivery. CPL/CPICP fluctuates more, and the algorithm prioritizes data collection over efficiency. Once the ad set accumulates ~50 optimization events in a 7-day rolling window, Meta exits the ad set from learning and delivery stabilizes.

**Optimization events** are conversions for the ad set's optimization goal. For Honeycomb's IC-conversion ads, that's the IC custom conversion. For lead-gen ads, it's leads.

## What triggers a learning reset

The portfolio-scaling skill cares about budget changes. Meta documents that **budget changes exceeding ~20% trigger a learning phase reset.** The exact threshold is undocumented — Meta intentionally keeps it fuzzy to discourage gaming — but 20% is the operating consensus from Meta reps and major agency reports.

A learning reset wastes whatever progress the ad set had toward 50 events; if it had 30 events accumulated, those 30 are effectively reset and the ad set has to climb back to 50 from zero. For low-volume verticals at Honeycomb's scale, this can mean the ad set never exits learning.

Other reset triggers:
- Significant audience changes (geo, interests, custom audiences)
- Significant creative changes (swap to a different ad)
- Optimization goal change
- Bid strategy change
- Attribution window change

Unchanged budgets, minor copy edits, and small audience adjustments do NOT reset learning.

## Why the 12% weekly cap

The cap sits at 60% of the ~20% reset threshold, providing a safety margin against:
- Day-to-day micro-adjustments compounding over a week
- Meta's threshold drifting (it's been observed at 15-25% across different ad accounts and time periods)
- Concurrent edits from the optimizer + strategic reallocation + portfolio knockdown all firing in the same week
- Rounding and timezone effects in how Meta computes the % change

In practice the optimizer's daily ±2% increases / ±4% decreases can compound 7 cycles a week without breaching the cap, but only if no strategic reallocation or knockdown stacks. The cap is the safety net for the stacking case.

## The 50-conversion / 7-day rule

Ad sets need ~50 optimization events in 7 days to exit learning. At Honeycomb's spend levels, low-volume verticals (distillers, sustainable main street, etc.) may produce fewer than 50 IC conversions per week even at full budget. **Those ad sets may never fully exit learning**, and that's expected — not a problem the optimizer should try to solve.

This is why the scaling skill's `confidence` floors require ≥3 conversions/week minimum (well below the 50/week needed to exit learning, but enough to make the elasticity correlation meaningful). A vertical can be `confident` for scaling purposes while its ad sets are perpetually in Meta's learning phase.

## Recommended scaling cadence

Meta's official recommendation: increase budget by ≤20% every 3-4 days, with stabilization windows in between. The portfolio-scaling skill's cadence — Tuesday strategic reallocation, Wed-Mon optimizer lockout on affected campaigns — gives ad sets ~5 days of stabilization after each strategic move, which fits the 3-4-day guideline plus a buffer.

The optimizer's ±2-4% daily moves are well below 20% in any single edit, so they don't risk a single-edit reset. Compounded over 5+ days they could approach 10%, which is still below the threshold; the 12% cap prevents the compounding from drifting higher.

## Ad set duplication for new audiences

When a vertical hits `new_audience_needed`, the recommendation is **duplicate the ad set with broader targeting, NOT edit the existing one's targeting.** Reason: editing targeting on an existing ad set is one of the strongest learning-reset triggers. The original ad set has accumulated 50+ events of delivery history; that data is worth preserving for the original audience even if it's saturating.

Duplicating creates a new ad set in learning phase, but it also creates a parallel test against the original. After ~7 days you can compare performance and decide whether to retire the original, keep both, or kill the duplicate.

The new audience action item in the brief should always say "duplicate, don't edit" — this single piece of advice is the highest-value Meta-best-practice the skill encodes.

## What this means for the skill's behavior

- The scaling skill's `weekly_remaining_pct` already accounts for cumulative movement. Tightening the cap below 12% would be over-protective; loosening above 15% would risk learning resets in stacking scenarios.
- The $26/day floor (=$25 hard floor + 4% buffer) protects against one worst-case optimizer reduction cycle pushing a campaign below the $25 minimum. Without the buffer, a campaign at exactly $25 could be reduced to $24 by a single 4% optimizer cycle, which Meta then either rejects or rounds up unpredictably.
- Strategic reallocation execution at Wed 3 AM (24 hours after Tuesday morning's brief was approved) gives Tyler ~21 hours to manually intervene if anything looks wrong. The same 21-hour window the optimizer uses.
- The lockout (Wed-Mon) intentionally exceeds Meta's 3-4-day stabilization window, both to give the agent's evaluation Tuesday a clean read on the strategic move's effect AND to prevent the optimizer from compounding small daily moves on top of a fresh strategic change.

## Sources

- Meta Business Help Center — Learning phase: https://www.facebook.com/business/help/112167992830700
- Meta Best Practices — Bid strategy + budget scaling: industry consensus from Meta solutions consultants and major performance agencies
- Honeycomb-specific operating experience — the optimizer constants in `apps-script/Code.js:31-43` reflect what's worked over the past year of running this account
