# Creative Intelligence Skill — Attribution Design

_Last updated: 2026-05-04_

## Why this doc exists

Skill 4 (Creative Intelligence) was specced with **per-ad asset-level breakdown insights** as the spine of variant performance attribution. Three rounds of Meta API investigation showed that approach won't return reliable variant-level conversion data for our ad mix. The spine has been replaced with **corpus-level text aggregation**.

This doc captures the reasoning so the next reader doesn't repeat the investigation cycles.

## Constraints discovered

Honeycomb's ads are 100% Meta Asset Feed dynamic creative (`asset_feed_spec`). Each ad carries up to 5 bodies + 5 titles + 5 descriptions + 10 images, and Meta's optimizer mixes-and-matches at delivery.

- **All four asset breakdowns at once with `actions`** → HTTP 400. Meta's docs confirm: "All Dynamic Creative asset breakdowns only support a limited set of metrics" — `actions` (and IC conversion attribution) is not in that supported set when multiple asset breakdowns are combined.
- **Single-dimension breakdowns with `actions`** → likely partially work, but Meta's optimizer **converges on a single winning variant** within days. Per-ad per-variant attribution would show one variant with all the impressions and the others near zero — statistically meaningless for ranking within an ad.
- **Image hash → `/adimages` resolution** → ✅ confirmed working (1440px URLs returned). Used for visual analysis.

## The attribution model

**Variant text is the join key.** Each unique body, title, and description is hashed by SHA-256; that hash is the variant ID.

**Per-ad CPICP** comes from the standard insights endpoint, no breakdowns. Already part of the snapshot pipeline.

**Per-variant CPICP** is computed by aggregating across the full corpus: for variant V appearing in ads {A1, A2, …, An}, sum spend and IC conversions across those ads. A variant in only winning ads aggregates as a winner; a variant in mostly losing ads aggregates as a loser. The boilerplate "MCAs drain your margins…" body — which appears in nearly every ad — gets effectively portfolio-median CPICP, which is the right answer (it's a baseline, not a differentiator).

**Side-by-side comparisons** ("same audience, same image, same time, different copy") come from finding pairs of ads that share an image but differ on body text. Within those pairs, body-level CPICP delta is causal-ish — audience and image held constant by selection.

**Optional enrichment via single-dimension breakdowns.** The pipeline tries `breakdowns=body_asset` per high-impression ad as a best-effort enrichment. If Meta returns useful per-asset rows, attach them. If the call fails or returns single-variant convergence, silently fall back. Not load-bearing.

## What this preserves

Tyler's reframe — preserve information end-to-end, don't compress to categories — still holds:

- Raw text of all 5 bodies + 5 titles + 5 descriptions stored per ad, never collapsed
- Deterministic structural features per variant (char/word/sentence count, opening word, syntactic markers) — pure Python, no LLM call
- LLM categorical tags as decoration on variants, never substitutes
- Bottom-decile losers in the dataset alongside winners
- Side-by-side variant pairs (different mechanism, same output shape)
- SKILL.md briefs that quote actual winning text and cite real numbers

## What we lose vs the original spec

- **Causal per-variant attribution within a single ad.** No more "in ad X, body[2] outperformed body[0] by Y%." Only "body text Z, across the 23 ads it appeared in, returned $42 CPICP vs the corpus median of $58."
- **Real-time variant performance.** A brand-new variant in a single new ad has no corpus history. Its first-week performance is judged at per-ad CPICP only. Converges as the variant gets reused.

## Pipeline shape

```
data/snapshots/<date>/                    (already populated daily)
  ├── ads.json                            (creative_id per ad_id)
  └── ad_insights.json                    (per-ad CPICP)

scripts/build_creative_dataset.py:
  1. Walk ads.json across the lookback window.
  2. Fetch each creative; extract bodies[]/titles[]/descriptions[]/images[]
     from asset_feed_spec.
  3. For each variant text, compute structural features (Python).
  4. Hash variant text → variant_id; build corpus index
     variant_id → list of ad_ids it appears in.
  5. Aggregate per-ad spend + IC conversions across each variant's ad list.
  6. Resolve image_hash → /adimages → download to data/creatives/images/.
  7. Compute side-by-side pairs (same image, different body).
  8. Optionally enrich with single-dim breakdowns for top-impression ads.
  9. Emit /tmp/creative_dataset.json with raw text + features + per-variant
     performance + per-ad performance + pairs + bottom decile.

skills/creative-intelligence/scripts/categorize_creative.py:
  For each unique variant_id without a cached LLM tag, call Anthropic
  API with text + image. Cache result. Estimated ~150-250 unique
  variants → ~$3 first run; pennies per week thereafter.

SKILL.md / agent-creative-intelligence.yml runtime:
  Reads /tmp/creative_dataset.json + cached image files.
  Required brief shape: quote the actual winning text + cite the
  real numbers + note bottom-decile losers. Categories are
  navigation, not the answer.
```

## Decision log

- 2026-05-04 — Pivoted from asset_id-based attribution to corpus-text aggregation after three rounds of Meta API investigation (PRs #50/#51/#52/#53/#54). Single-dimension breakdowns retained as optional enrichment.
