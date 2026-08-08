---
name: ad-copy-generator
description: Draft new ad-copy variants for a target vertical from the Creative Intelligence dataset. Drafts are markdown for human review; never auto-published. Compliance regex backstop catches the most-likely-to-slip-through violations but never substitutes for human review.
---

# Ad-Copy Generator

## Purpose

Take the per-variant performance + structural patterns Creative Intelligence produces and turn them into draft ad copy for the next round. Closes the loop from "what's working" → "what to write next." The drafts are NOT auto-published — every draft requires human review per the compliance checklist before going live.

## Pipeline

Single Python script. Reads the Creative Intelligence dataset, picks the per-vertical winning patterns, asks Claude to draft N new variants (body + title + description triples) following those patterns, runs a compliance regex backstop, writes a markdown file under `data/drafts/`.

```
python3 skills/ad-copy-generator/scripts/generate_drafts.py \
    --vertical breweries \
    [--num-drafts 5] \
    [--input /tmp/creative_dataset.json] \
    [--all-verticals] [--min-vertical-ads 5] \
    [--dry-run]
```

Requires:
- `ANTHROPIC_API_KEY` env var (unless `--dry-run`)
- A populated `/tmp/creative_dataset.json` from the Creative Intelligence pipeline (or a path passed via `--input`)
- The reference markdown at `skills/ad-copy-generator/references/{voice_guide,compliance_rules}.md`

## How it picks patterns

For each target vertical:

1. Filter the dataset's `variants[]` to those that appear in this vertical's ads.
2. Compute median CPICP for each dimension (body / title / description).
3. **Winners** = top 5 by lowest CPICP, below-median.
4. **Losers** = top 5 by highest CPICP, above-median.
5. Compute structural-feature deltas between winners and losers (avg word count, % with proper noun, % with question mark, % imperative opener, etc.).
6. Build a Claude prompt that includes the full voice guide, the full compliance rules, the winning examples (verbatim from the corpus), the losing examples to avoid, and the structural pattern data.
7. Force `tool_use` on a `draft_ads` tool whose schema requires `patterns_observed` (a 2-3 sentence model summary) plus an array of `drafts` each with `body`, `title`, `description`, `pattern_followed`.

Median split (rather than naive top-5 / bottom-5) ensures winners and losers are always distinct cohorts even on small variant pools. With only 3 variants in a dimension, naive ranking would put the WORST variant in winners just because it's "top 5 by ascending CPICP."

## Compliance backstop

`COMPLIANCE_BLOCKLIST` in the script catches:

- Quantified return promises (`12% APY`, `5% return annually`)
- Multiple-x return claims (`3x returns`)
- Guarantee language (`guaranteed`, `risk-free`, `assured return`)
- FDIC comparisons
- Specific dollar-return testimonials (`Sarah earned $X`)

Drafts that hit any pattern get a `⚠️ Compliance flags:` banner in the markdown. The regex is a backstop, not a substitute — the reviewer's checklist at the bottom of every output file catches the long tail.

## Output

`data/drafts/<YYYY-MM-DD>-<vertical>.md` — Markdown file with:

1. **Source corpus stats** — ad count, IC count, median CPICP, total spend
2. **Patterns observed** — Claude's 2-3 sentence summary of what distinguishes winners from losers
3. **Structural pattern data** — the JSON block of feature deltas between winners and losers
4. **N drafts** — each with body, title, description, `pattern_followed` explanation, and compliance-flag status (✓ or ⚠️)
5. **Reviewer checklist** — the 7-item compliance + voice review the human runs before publishing

`stdout` summary lists which verticals got drafts, where the file landed, and how many drafts had compliance flags.

## Constraints

- **Drafts are draft only.** Nothing this script produces is auto-published. The markdown file lives in `data/drafts/` for human review.
- **Read-only on Meta and Anthropic** at the level of write actions. The script only reads the dataset, calls the Anthropic API once per vertical for drafting, and writes a local markdown file.
- **No Sheet writes.** Drafts intentionally don't go to a Sheet tab. The markdown format is the artifact; if Tyler wants tracking later, that's a future feature.
- **No autonomous workflow** in this iteration. The skill is `workflow_dispatch`-only; Tyler runs it after reading the Creative Intelligence brief and deciding which verticals warrant new drafts. An autonomous Monday cron could be added later once the human-review loop has been exercised a few times.
- **Don't run on insufficient data.** `--all-verticals` skips any vertical with fewer than `--min-vertical-ads` ads (default 5). Single-vertical mode (`--vertical X`) doesn't enforce that floor — Tyler explicitly asked for that vertical, so trust the caller.

## Cost

Per Anthropic call: ~$0.05-0.10 (system + user messages with full voice/compliance guide and ~10 variant examples; max_tokens=4096 for the response). Per `--all-verticals` run with 8 verticals above the floor: ~$0.50-0.80. Cheap enough that this can run weekly without budget concerns.

## What this skill does NOT do

- Generate images. Only text drafts. Image generation belongs to a different skill (or Canva/Meta Studio).
- Push drafts to Meta. The compliance review needs to happen first; the draft markdown is meant for copy-paste by a human into Meta Ads Manager.
- Test variants A/B. That's Meta's optimizer's job after the variants are launched.
- Replace the Creative Intelligence brief. The brief tells Tyler what's working; this skill writes the next round. Run them in order.
