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

## CPC inflation

- **25% rise = warning.** Auction pressure climbing relative to the ad's baseline cost-per-click.
- **50% rise = critical.** The ad is becoming materially more expensive to run.
- **Caveat:** rising CPC with rising impressions = scaling, not fatigue. The classifier looks for CPC inflation in the context of flat/declining impressions. (The current implementation uses a simpler test — CPC change vs baseline — and relies on the frequency + CTR signals to confirm fatigue. Tyler should still sanity-check before pausing.)

## Baseline window — days 4–7 after launch

Meta's algorithm exits the learning phase around days 3–4. Peak performance typically occurs days 4–7 before fatigue starts encroaching (Adligator lifecycle model). We use this as the reference point rather than "all-time average" so a long-running ad's later performance is judged against its strongest version, not its mean.

For ads outside the 93-day insight retention window, we fall back to the oldest 7-day slice of the current query and tag the baseline `estimated`. Estimated baselines are noisier — flag them in Slack output.

## Minimum thresholds

- **1,000 impressions** in the 14-day window. Below this, CTR/CPC are noise.
- **7 days since `created_time`.** Need enough data for the baseline window to even exist.
- **21 days = creative age warning.** Most creatives in narrow audiences start declining by day 14–21 (Adligator, Search Engine Land). Surfaced in the daily-check skill, not here.

## What the classifier *doesn't* do

- Pause ads or change budgets. This skill is read-only on Meta.
- Distinguish "the creative is fatigued" from "the audience is exhausted." That distinction is left for Tyler — surface the metrics, let the human decide whether to refresh the ad or broaden the audience.
- Cross-reference HubSpot ICP volume. The classifier is purely Meta-side. CPICP attribution lives in the campaign-level Apps Script pipeline.
