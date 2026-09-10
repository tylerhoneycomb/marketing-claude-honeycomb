---
name: creative-intelligence
description: Weekly Monday brief on what creative copy + visual patterns are winning across Honeycomb's ad portfolio. Always quotes actual winning text and cites real numbers — never recommends categorical labels.
---

# Creative Intelligence

## Purpose

Tell Tyler what to write next. Reads the variant-grain corpus dataset; correlates copy + visual patterns with per-ad CPL across the lookback window; produces briefs that quote actual winning copy alongside its real numbers and structural fingerprint. Categories are how we navigate the dataset; they are NOT the answer. A brief that says "lead with owner_story angle" is a failure — the right answer is "lead with the owner's name and years in business; bodies like 'Sarah's been brewing for 12 years' produced 31 leads at $12.40 CPL across the 4 brewery ads they appeared in".

## Pipeline

The autonomous workflow runs three deterministic Python steps as ordinary workflow steps BEFORE Claude is invoked, then a final claude-code-action step composes the brief from the resulting JSON. Claude does not run the scripts — they're already done by the time the brief composition starts. This split exists because the categorizer's Anthropic SDK calls fail with `APIConnectionError` when run from inside claude-code-action's subprocess shell (suspected env-var inheritance issue), but work reliably when run as ordinary workflow steps.

Workflow step order:

```
# Step 1 — workflow step: refresh creative cache + build corpus
python3 skills/creative-intelligence/scripts/build_creative_dataset.py --output /tmp/creative_dataset.json

# Step 2 — workflow step: LLM tagging (continue-on-error)
python3 skills/creative-intelligence/scripts/categorize_creative.py

# Step 3 — workflow step: re-emit dataset with tags attached
python3 skills/creative-intelligence/scripts/build_creative_dataset.py --output /tmp/creative_dataset.json

# Step 4 — claude-code-action: compose brief from /tmp/creative_dataset.json,
#                              POST to Sheet, optional Slack, write status
```

For interactive runs from a terminal, the same three scripts can be run by hand in the same order; SKILL.md prompt instructions for Claude become "compose a brief from the dataset" rather than "run these scripts."

The order matters even when run by hand: `categorize_creative.py` reads `data/creatives/creatives.json` and only categorizes variants whose creative entry has the asset_feed_spec arrays populated. `build_creative_dataset.py` is the script that refreshes those arrays via Meta calls. Running categorize first against an unrefreshed cache (the first-ever run) means the categorizer has zero work to do and the dataset emits without LLM tags.

Requires:
- `META_ACCESS_TOKEN` env var (for the dataset builder's Meta calls)
- `ANTHROPIC_API_KEY` env var (for the categorizer's Anthropic calls)
- `EXEC_ENDPOINT` env var (optional — falls back to `exec_endpoint` in `data/config/benchmarks.json`)
- `EXEC_SHARED_SECRET` env var (required for the Sheet write — `creative-intelligence-write` is a protected `/exec` action and the gate fails closed; see "Output — Sheet")
- `SLACK_WEBHOOK_URL` env var (optional — Slack post is gated on this)

Scheduled via `.github/workflows/agent-creative-intelligence.yml` for Monday 14:00 UTC (cron paused since 2026-06-08 — `workflow_dispatch` only until unpaused). The first run is the slowest: ~$5 of Anthropic calls to categorize 200-400 unique variants and 50-150 unique images, plus the Meta-side cache refresh + image downloads. Subsequent runs hit cache for everything except new variants and process in seconds.

## Architecture (in one paragraph)

Per [docs/CREATIVE_INTELLIGENCE_DESIGN.md](../../docs/CREATIVE_INTELLIGENCE_DESIGN.md): the attribution spine is **corpus-level text aggregation**, not per-ad asset_id breakdown. Meta's optimizer converges on a single winning variant within days, so per-ad per-variant attribution would be dominated by Meta's choice. Instead, when the same body text appears across N different ads, we sum spend and leads across all N to produce a per-variant CPL that's meaningful at the corpus level. Side-by-side comparisons come from ads sharing an image_hash but differing on bodies — audience and image held constant by selection.

The design doc predates the 2026-09-09 lead pivot and still says CPICP / IC throughout; read every CPICP reference there as CPL and every IC count as leads. The architecture is unchanged — only the metric moved.

## Dataset shape (`/tmp/creative_dataset.json`)

```
{
  "since", "until",
  "ad_count", "variant_count", "pair_group_count",
  "ads": [
    {ad_id, ad_name, vertical, campaign_name,
     impressions, spend, leads, cpl, prequal_decisions, days_active,
     ic_conversions, cpicp (reported only — never a sort key or threshold),
     bodies (count), titles (count), descriptions (count),
     image_hashes, local_image_paths,
     visual_styles: [{image_hash, visual_style, rationale}],
     variant_ids: {bodies: [...], titles: [...], descriptions: [...]}}
  ],
  "variants": [
    {variant_id, dimension (body|title|description), text,
     structural: {char_count, word_count, sentence_count,
                  opening_word, opens_with_imperative,
                  has_question_mark, has_exclamation,
                  has_em_dash, has_arrow,
                  has_number, has_dollar_amount, has_percentage,
                  has_proper_noun, has_second_person,
                  has_first_person_plural, has_negation,
                  avg_word_length, avg_words_per_sentence},
     ad_count, total_spend, total_impressions,
     total_leads, cpl,
     total_ic_conversions, cpicp (reported only),
     llm_copy_angle, llm_copy_rationale,
     appears_in_ads: [...]}
  ],
  "side_by_side_pairs": [
    {image_hash, ad_count_sharing,
     differing_pairs: [{ad_a, ad_b, ad_a_cpl, ad_b_cpl,
                         bodies_only_in_a, bodies_only_in_b}]}
  ],
  "top_decile_ads": [...], "bottom_decile_ads": [...]
}
```

Variant entries are sorted by CPL ascending (best first). Ad entries are sorted the same way. Local image paths point at jpg files under `data/creatives/images/` — read those directly when you need to reason about the visual.

## What to look for

In priority order:

1. **Per-vertical winners + losers.** For each vertical with ≥10 ads in the window, find the 3 lowest-CPL and 3 highest-CPL ads. Read their bodies/titles/descriptions inline. Look for structural patterns that differ — opening word, length, presence of proper nouns, presence of numbers/dollar amounts, second-person vs first-person plural. Quote the actual differences.

2. **Side-by-side pairs.** These are the cleanest signal in the dataset. Within each `side_by_side_pairs[]` group, the audience + image are held constant; CPL delta is causal-ish. List the bodies that differ between paired ads and quote them. If `ad_a_cpl` ≪ `ad_b_cpl`, the bodies in `bodies_only_in_a` are the winners.

3. **Variant-level corpus aggregation.** For each variant in `variants[]` sorted by CPL, the ones with `ad_count ≥ 5` and `total_leads ≥ 40` carry real signal. The boilerplate "MCAs drain your margins…" body appearing in 50+ ads will sit near the corpus-median CPL — that's expected; it's a baseline body, not a differentiator.

4. **Bottom-decile commonalities.** What do the worst-performing ads' variants have in common that the top-decile don't? Question-opener bodies, second-person leads, generic descriptions — these are angles to retire.

5. **Image-style + copy-angle interactions.** A `real_person` image paired with an `owner_story` body is probably underexposed in the data; a `graphic` image paired with a `social_proof` body might be overexposed. Use the LLM tags as navigation, then quote the actual examples.

## Confidence labels

Apply to every grouped finding:
- **confident** — ≥10 ads AND ≥100 leads in the group
- **directional** — ≥5 ads AND ≥40 leads

These floors are set in `data/config/benchmarks.json` under `creative_intelligence`. They were ≥25 / ≥10 IC conversions until 2026-09-09; leads run roughly 14× denser than IC ever did, so the counts are scaled to preserve the original statistical intent rather than to loosen it.
- **insufficient** — below either floor; report as a hypothesis to test, not a conclusion

When the spend-weighted median disagrees with the unweighted median by >25% on a grouped finding, name the outlier ad and downgrade confidence.

## Output rules

**Always quote the actual copy and cite the real numbers.** Never recommend a category as the action. Always frame as "lead with bodies like X" not "lead with owner_story angle."

For each finding, the brief must include:
- the vertical (or "portfolio-wide")
- the actual winning copy in quotes
- the structural fingerprint (length, opening word, key syntactic markers from the `structural` block)
- the real numbers (`ad_count`, `total_leads`, `cpl`)
- the confidence label
- a contrasting loser when available (quote the loser too)

Briefs without quoted copy or without cited numbers fail the SKILL.md spec.

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, print a sectioned brief to terminal:

```
🎯 Creative Intelligence — 2026-05-05

PORTFOLIO WINNERS (confident, 47 ads):
  Owner-name openers cluster in the top decile. Bodies like
    "Sarah's been brewing for 12 years"          ($11.80 CPL, 4 ads, 142 leads)
    "Mike opened the brewery in 2021"            ($12.40 CPL, 3 ads, 96 leads)
  vs the bottom-decile question openers:
    "Ready to grow your brewery?"                ($31.60 CPL, 2 ads, 22 leads)
  Structural fingerprint of winners: 38 word avg, has_proper_noun=true,
  no question marks, opens with possessive form.

[BREWERIES] (confident, 14 ads):
  Top variant: "Banks Pass on Your Brewery. We Don't."
    $12.10 CPL across 6 ads, 188 leads
  Bottom variant: "Get Funding for Your Brewery"
    $29.80 CPL across 3 ads, 24 leads
  Side-by-side under image 7babd2e: body "Banks decline restaurants…"
  ($42 CPL) outperformed "Restaurant owners: prequalify…" ($98 CPL).

[BAKERIES] (directional, 5 ads): ...

CORPUS-WIDE PATTERNS:
  ...

INSUFFICIENT (3 verticals, hypothesis only):
  ...
```

End with: `Sheet log: N rows written to creative_intelligence_log` and one line of stats.

## Output — Slack (only if `SLACK_WEBHOOK_URL` is set)

Skip Slack posting entirely if `SLACK_WEBHOOK_URL` is unset or empty — print to terminal only. When the webhook IS set, POST a condensed version of the terminal brief: portfolio-wide top finding (one quoted body + numbers), top finding per vertical with `confident` label, plus any verticals where the bottom decile suggests a specific angle to retire. Skip `directional` and `insufficient` findings on Slack — they go to terminal/log only.

The first line of the Slack post is always the headline, in exactly this shape:

```
🎯 Creative Intelligence — <until> — <N> leads at $<median CPL> median CPL across <M> ads (<since>–<until>)
```

where `N` = sum of `ads[].leads`, median CPL = median of the non-null `ads[].cpl` values, and `M` = top-level `ad_count`. Every finding that follows quotes the copy and its `(ad_count, total_leads, cpl)` numbers, e.g. `"Sarah's been brewing for 12 years" (4 ads, 142 leads, $11.80 CPL)`. Per-vertical findings are ordered by CPL ascending (best first); the retire-this-angle lines are ordered by CPL descending (worst first).

IC (`ic_conversions` / `total_ic_conversions`) and rewards are reported-only subtypes. A finding may carry at most one secondary line — `of which N reached an IC decision` — and IC / CPICP is never the headline number, a sort key, a threshold, or the reason to flag or retire a variant.

## Output — Sheet

POST one row per vertical to `?action=creative-intelligence-write` with this payload (the script does NOT issue this POST itself; the SKILL prompt orchestrates it via the `/exec` endpoint). The action is protected, so the body carries the shared secret as `key` — read it from `EXEC_SHARED_SECRET`, never hardcode it:

```
{
  "key": "<EXEC_SHARED_SECRET>",
  "rows": [
    {
      "date": "2026-09-14",
      "vertical": "breweries",
      "ad_count": 14,
      "median_cpl": 14.20,
      "median_cpicp": 14.20,
      "spend_total": 1422.50,
      "lead_total": 188,
      "ic_total": 188,
      "top_body_variant_id": "08cb19e3d818bfc7",
      "top_body_text": "Banks Pass on Your Brewery. We Don't.",
      "top_body_cpl": 12.10,
      "top_body_cpicp": 12.10,
      "top_visual_hash": "7babd2e837eb42b4167c1e37d6be7b9e",
      "top_visual_style": "real_person",
      "bottom_decile_count": 2
    },
    ...
  ]
}
```

Field rules:
- `median_cpl` — median of the vertical's non-null `ads[].cpl`; `lead_total` — sum of the vertical's `ads[].leads`.
- `top_body_text` / `top_body_variant_id` / `top_body_cpl` — the lowest-CPL body variant in the vertical that meets the `confident` floor (fall back to the lowest-CPL `directional` body if none is confident; leave the three fields blank if neither exists). Never pick by CPICP. This row is what the Tuesday portfolio-scaling brief quotes as its creative prescription, so the selection rule matters.
- `median_cpicp`, `ic_total`, `top_body_cpicp` — **legacy-named, lead-valued.** Send the same numbers as `median_cpl`, `lead_total`, `top_body_cpl` (see the wire-contract note below).

`creative-intelligence-write` auto-creates the `creative_intelligence_log` tab on first call. Header row: `date, vertical, ad_count, median_cpicp, spend_total, ic_total, top_body_variant_id, top_body_text, top_body_cpicp, top_visual_hash, top_visual_style, bottom_decile_count, recorded_at`.

> **Wire contract is still IC-named.** `handleCreativeIntelligenceWrite_` in
> `apps-script/Code.js` reads the payload keys `median_cpicp`, `ic_total` and
> `top_body_cpicp` by name, so the JSON this skill POSTs still uses those key
> names even though the analysis above is lead-based. Send lead values under
> the legacy keys, and send the same values additively under `median_cpl` /
> `lead_total` / `top_body_cpl` so the payload is ready for the rename; do
> not drop the legacy keys on this side alone, because unrecognised keys are
> written as blanks and the handler would then blank three columns. Switching
> the handler to the lead-named keys requires the matching edit in `Code.js`
> and a redeploy, which is tracked separately — the Apps Script deploy
> pipeline has not run since 2026-06-23 and should be verified first.

## Status reporter

Before exiting, write one line to `/tmp/agent_status.txt`, e.g.:

```
verticals=8 variants=247 confident=2 directional=4 insufficient=2 winners_top=owner_story bottom_decile_count=14
```

Pull values from the dataset (`ad_count` per vertical, len(variants), confidence-label distribution from your own analysis). The workflow's status reporter step picks this up and posts it to the agent-loop tracking issue (#48).

## Constraints

- **Read-only** on Meta and the repo (except `/tmp/agent_status.txt`). Do not modify any files. Do not push commits.
- **Never present `insufficient` findings as actionable.** Frame them as hypotheses to test as more data accumulates.
- **Do not hallucinate numbers.** Every number cited in the brief must come directly from the dataset. If a variant has `cpl: null`, don't make one up — say "no leads yet."
- **Always quote actual copy.** A brief that recommends a category instead of citing the text it observed is a failure of the skill's purpose.
- **Categorical tags are navigation, not the answer.** Use `llm_copy_angle` and `visual_style` to slice the dataset and find patterns, but the patterns are described in terms of the actual text and the structural features.
- **Honor confidence labels.** A finding with 4 ads and 30 leads is `insufficient` — don't recommend acting on it without flagging that it's a hypothesis.
