# Fatigue Detection — Threshold Reference

Where the values in `data/config/benchmarks.json → fatigue.*` come from. Claude can read this if it needs to explain a classification to Tyler or justify why an ad got tagged.

## CTR decay thresholds

- **15% decline = early fatigue.** Industry consensus across Adligator, AdStellar, Ryze. The "first warning" point — recoverable with a refresh.
- **30% decline = fatigued.** Industry consensus is 20–30%. We use 30% to avoid false positives at Honeycomb's lower volume per ad. Ads at this level are actively wasting spend.

CTR is measured as last-7-day rolling vs the ad's own baseline window. Never use a fixed CTR floor — what's healthy in one vertical can be poor in another.

## Frequency thresholds

- **2.0 warning (prospecting).** Meta's own analytics confirm performance decline begins around this point. Audience is starting to repeat.
- **3.0 critical (prospecting).** Consensus across inBeat, AdMetrics, Search Engine Land. Audience saturated — the same people are seeing the ad 3+ times in 7 days.
- **5.0 critical (retargeting).** Retargeting tolerates higher frequency because the audience is warm and self-selected. Honeycomb doesn't currently run retargeting campaigns; this threshold is configured but unused.

## CPL inflation — the outcome-level signal

- **25% rise = warning.** The ad's leads cost a quarter more than in its peak window. With frequency at or above the warning line this classifies as `early_fatigue`.
- **50% rise = critical.** Leads are materially more expensive; classifies as `fatigued` regardless of CTR, because an ad can hold its click-through while the people it reaches stop converting.
- **Guard:** CPL is only compared when both the baseline and current windows bought at least one lead. A 4-day baseline window holds few leads, so a single-lead swing can look like a large percentage — treat CPL-driven verdicts on `estimated` baselines with extra caution and sanity-check spend before pausing.
- Values mirror `scripts/compute_signals.py`, which applies the same `cpl_inflation_*` thresholds to the snapshot pipeline.

## CPC inflation (reported only)

- **25% rise = warning, 50% rise = critical** are configured in `benchmarks.json` for reference, but CPC is not a classification input in this skill — it is shown on the diagnostics line to explain a CPL rise (auction pressure vs. conversion drop-off).
- **Caveat:** rising CPC with rising impressions = scaling, not fatigue. Tyler should still sanity-check before pausing.

## Baseline window — days 4–7 after launch

Meta's algorithm exits the learning phase around days 3–4. Peak performance typically occurs days 4–7 before fatigue starts encroaching (Adligator lifecycle model). We use this as the reference point rather than "all-time average" so a long-running ad's later performance is judged against its strongest version, not its mean. The baseline carries leads and CPL as well as CTR/CPC/CPM.

For ads outside the 93-day insight retention window (or with no `created_time`), we fall back to the oldest 4-day slice of the current 14-day query — the same length as the days 4–7 peak window (`baseline_window_end_day - baseline_window_start_day + 1`) — and tag the baseline `estimated`. Estimated baselines are noisier, and the estimated CPL baseline especially so — flag them in Slack output.

## Minimum thresholds

- **1,000 impressions** in the 14-day window. Below this, CTR/CPC are noise.
- **7 days since `created_time`.** Need enough data for the baseline window to even exist.
- **21 days = creative age warning.** Most creatives in narrow audiences start declining by day 14–21 (Adligator, Search Engine Land). Surfaced in the daily-check skill, not here.

## What the classifier *doesn't* do

- Pause ads or change budgets. This skill is read-only on Meta.
- Distinguish "the creative is fatigued" from "the audience is exhausted." That distinction is left for Tyler — surface the metrics, let the human decide whether to refresh the ad or broaden the audience.
- Cross-reference HubSpot ICP volume. The classifier is purely Meta-side. CPL is the primary cost metric and the only cost threshold here; IC / prequal counts ride along on each row as reported-only fields and never drive a classification, sort, or headline.
