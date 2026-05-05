---
name: creative-intelligence
description: Weekly Monday brief on what creative copy + visual patterns are winning across Honeycomb's ad portfolio. Always quotes actual winning text and cites real numbers — never recommends categorical labels.
---

# Creative Intelligence

## Purpose

Tell Tyler what to write next. Reads the variant-grain corpus dataset; correlates copy + visual patterns with per-ad CPICP across the lookback window; produces briefs that quote actual winning copy alongside its real numbers and structural fingerprint. Categories are how we navigate the dataset; they are NOT the answer. A brief that says "lead with owner_story angle" is a failure — the right answer is "lead with the owner's name and years in business; bodies like 'Sarah's been brewing for 12 years' produced 3 ICPs at $42 CPICP across the 4 brewery ads they appeared in".

## Scripts

Three invocations in sequence. Build runs first so the creative cache is fully refreshed (asset_feed_spec arrays + image_hashes populated for every active creative), then categorize tags any new variants/images, then build re-runs to re-emit the dataset JSON with the new tags attached. The second build call is essentially free — cache and images are already on disk, it just re-emits the JSON.

```
python3 skills/creative-intelligence/scripts/build_creative_dataset.py --output /tmp/creative_dataset.json
python3 skills/creative-intelligence/scripts/categorize_creative.py
python3 skills/creative-intelligence/scripts/build_creative_dataset.py --output /tmp/creative_dataset.json
```

The order matters. `categorize_creative.py` reads `data/creatives/creatives.json` and only categorizes variants whose creative entry has the new asset_feed_spec arrays populated. `build_creative_dataset.py` is the script that refreshes those arrays via Meta calls. Running categorize first against an unrefreshed cache (e.g. the first-ever run) means the categorizer has zero work to do, and the dataset emits without LLM tags.

Requires:
- `META_ACCESS_TOKEN` env var (for the dataset builder's Meta calls)
- `ANTHROPIC_API_KEY` env var (for the categorizer's Anthropic calls)
- `EXEC_ENDPOINT` env var (optional — falls back to `exec_endpoint` in `data/config/benchmarks.json`)
- `SLACK_WEBHOOK_URL` env var (optional — Slack post is gated on this)

Scheduled via `.github/workflows/agent-creative-intelligence.yml` for Monday 14:00 UTC. The first run is the slowest: ~$5 of Anthropic calls to categorize 200-400 unique variants and 50-150 unique images, plus the Meta-side cache refresh + image downloads. Subsequent runs hit cache for everything except new variants and process in seconds.

## Architecture (in one paragraph)

Per [docs/CREATIVE_INTELLIGENCE_DESIGN.md](../../docs/CREATIVE_INTELLIGENCE_DESIGN.md): the attribution spine is **corpus-level text aggregation**, not per-ad asset_id breakdown. Meta's optimizer converges on a single winning variant within days, so per-ad per-variant attribution would be dominated by Meta's choice. Instead, when the same body text appears across N different ads, we sum spend and IC conversions across all N to produce a per-variant CPICP that's meaningful at the corpus level. Side-by-side comparisons come from ads sharing an image_hash but differing on bodies — audience and image held constant by selection.

## Dataset shape (`/tmp/creative_dataset.json`)

```
{
  "since", "until",
  "ad_count", "variant_count", "pair_group_count",
  "ads": [
    {ad_id, ad_name, vertical, campaign_name,
     impressions, spend, ic_conversions, cpicp, days_active,
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
     total_ic_conversions, cpicp,
     llm_copy_angle, llm_copy_rationale,
     appears_in_ads: [...]}
  ],
  "side_by_side_pairs": [
    {image_hash, ad_count_sharing,
     differing_pairs: [{ad_a, ad_b, ad_a_cpicp, ad_b_cpicp,
                         bodies_only_in_a, bodies_only_in_b}]}
  ],
  "top_decile_ads": [...], "bottom_decile_ads": [...]
}
```

Variant entries are sorted by CPICP ascending (best first). Ad entries are sorted the same way. Local image paths point at jpg files under `data/creatives/images/` — read those directly when you need to reason about the visual.

## What to look for

In priority order:

1. **Per-vertical winners + losers.** For each vertical with ≥10 ads in the window, find the 3 lowest-CPICP and 3 highest-CPICP ads. Read their bodies/titles/descriptions inline. Look for structural patterns that differ — opening word, length, presence of proper nouns, presence of numbers/dollar amounts, second-person vs first-person plural. Quote the actual differences.

2. **Side-by-side pairs.** These are the cleanest signal in the dataset. Within each `side_by_side_pairs[]` group, the audience + image are held constant; CPICP delta is causal-ish. List the bodies that differ between paired ads and quote them. If `ad_a_cpicp` ≪ `ad_b_cpicp`, the bodies in `bodies_only_in_a` are the winners.

3. **Variant-level corpus aggregation.** For each variant in `variants[]` sorted by CPICP, the ones with `ad_count ≥ 5` and `total_ic_conversions ≥ 10` carry real signal. The boilerplate "MCAs drain your margins…" body appearing in 50+ ads will sit near the corpus-median CPICP — that's expected; it's a baseline body, not a differentiator.

4. **Bottom-decile commonalities.** What do the worst-performing ads' variants have in common that the top-decile don't? Question-opener bodies, second-person leads, generic descriptions — these are angles to retire.

5. **Image-style + copy-angle interactions.** A `real_person` image paired with an `owner_story` body is probably underexposed in the data; a `graphic` image paired with a `social_proof` body might be overexposed. Use the LLM tags as navigation, then quote the actual examples.

## Confidence labels

Apply to every grouped finding:
- **confident** — ≥10 ads AND ≥25 IC conversions in the group
- **directional** — ≥5 ads AND ≥10 IC conversions
- **insufficient** — below either floor; report as a hypothesis to test, not a conclusion

When the spend-weighted median disagrees with the unweighted median by >25% on a grouped finding, name the outlier ad and downgrade confidence.

## Output rules

**Always quote the actual copy and cite the real numbers.** Never recommend a category as the action. Always frame as "lead with bodies like X" not "lead with owner_story angle."

For each finding, the brief must include:
- the vertical (or "portfolio-wide")
- the actual winning copy in quotes
- the structural fingerprint (length, opening word, key syntactic markers from the `structural` block)
- the real numbers (`ad_count`, `total_ic_conversions`, `cpicp`)
- the confidence label
- a contrasting loser when available (quote the loser too)

Briefs without quoted copy or without cited numbers fail the SKILL.md spec.

## Output — Interactive (terminal)

When invoked from an interactive Claude Code session, print a sectioned brief to terminal:

```
🎯 Creative Intelligence — 2026-05-05

PORTFOLIO WINNERS (confident, 47 ads):
  Owner-name openers cluster in the top decile. Bodies like
    "Sarah's been brewing for 12 years"          ($42 CPICP, 4 ads, 18 IC)
    "Mike opened the brewery in 2021"            ($38 CPICP, 3 ads, 12 IC)
  vs the bottom-decile question openers:
    "Ready to grow your brewery?"                ($180 CPICP, 2 ads, 1 IC)
  Structural fingerprint of winners: 38 word avg, has_proper_noun=true,
  no question marks, opens with possessive form.

[BREWERIES] (confident, 14 ads):
  Top variant: "Banks Pass on Your Brewery. We Don't."
    $44 CPICP across 6 ads, 22 IC conversions
  Bottom variant: "Get Funding for Your Brewery"
    $176 CPICP across 3 ads, 1 IC conversion
  Side-by-side under image 7babd2e: body "Banks decline restaurants…"
  ($42) outperformed "Restaurant owners: prequalify…" ($98).

[BAKERIES] (directional, 5 ads): ...

CORPUS-WIDE PATTERNS:
  ...

INSUFFICIENT (3 verticals, hypothesis only):
  ...
```

End with: `Sheet log: N rows written to creative_intelligence_log` and one line of stats.

## Output — Slack (only if `SLACK_WEBHOOK_URL` is set)

Skip Slack posting entirely if `SLACK_WEBHOOK_URL` is unset or empty — print to terminal only. When the webhook IS set, POST a condensed version of the terminal brief: portfolio-wide top finding (one quoted body + numbers), top finding per vertical with `confident` label, plus any verticals where the bottom decile suggests a specific angle to retire. Skip `directional` and `insufficient` findings on Slack — they go to terminal/log only.

## Output — Sheet

POST one row per vertical to `?action=creative-intelligence-write` with this payload (the script does NOT issue this POST itself; the SKILL prompt orchestrates it via the `/exec` endpoint):

```
{
  "rows": [
    {
      "date": "2026-05-05",
      "vertical": "breweries",
      "ad_count": 14,
      "median_cpicp": 52.30,
      "spend_total": 1422.50,
      "ic_total": 27,
      "top_body_variant_id": "08cb19e3d818bfc7",
      "top_body_text": "Banks Pass on Your Brewery. We Don't.",
      "top_body_cpicp": 44.0,
      "top_visual_hash": "7babd2e837eb42b4167c1e37d6be7b9e",
      "top_visual_style": "real_person",
      "bottom_decile_count": 2
    },
    ...
  ]
}
```

`creative-intelligence-write` auto-creates the `creative_intelligence_log` tab on first call. Header row: `date, vertical, ad_count, median_cpicp, spend_total, ic_total, top_body_variant_id, top_body_text, top_body_cpicp, top_visual_hash, top_visual_style, bottom_decile_count, recorded_at`.

## Status reporter

Before exiting, write one line to `/tmp/agent_status.txt`, e.g.:

```
verticals=8 variants=247 confident=2 directional=4 insufficient=2 winners_top=owner_story bottom_decile_count=14
```

Pull values from the dataset (`ad_count` per vertical, len(variants), confidence-label distribution from your own analysis). The workflow's status reporter step picks this up and posts it to the agent-loop tracking issue (#48).

## Constraints

- **Read-only** on Meta and the repo (except `/tmp/agent_status.txt`). Do not modify any files. Do not push commits.
- **Never present `insufficient` findings as actionable.** Frame them as hypotheses to test as more data accumulates.
- **Do not hallucinate numbers.** Every number cited in the brief must come directly from the dataset. If a variant has `cpicp: null`, don't make one up — say "no IC conversions yet."
- **Always quote actual copy.** A brief that recommends a category instead of citing the text it observed is a failure of the skill's purpose.
- **Categorical tags are navigation, not the answer.** Use `llm_copy_angle` and `visual_style` to slice the dataset and find patterns, but the patterns are described in terms of the actual text and the structural features.
- **Honor confidence labels.** A finding with 4 ads and 8 IC conversions is `insufficient` — don't recommend acting on it without flagging that it's a hypothesis.
