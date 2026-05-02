# Skill: Ad Copy Generator

## Purpose

Draft Meta ad copy variants for Honeycomb Credit's investment-crowdfunding (IC) verticals. Always produces COPY DRAFTS for human review — never uploads to Meta.

## When to invoke

- Tyler asks for ad copy for a specific business / vertical
- The fatigue-monitor skill flagged an ad as "Pause + replace" and Tyler wants a refresh draft
- Generating variants for a new campaign launch

## Inputs

1. **Vertical** — restaurant, brewery, gym, salon, retail, food-and-beverage manufacturer, or "general small business"
2. **Audience** — small business owners (raisers) OR community investors (backers)
3. **Existing high-CTR copy** (optional) — `data/creatives/creatives.json` filtered to recent winners from `data/derived/winner_bleeder.json` provides "what's working now"
4. **Brand voice rules** — `CLAUDE.md` § Tone & Brand Voice

## Brand voice (from CLAUDE.md, must follow)

- Warm, community-oriented, empowering
- Speak to small business owners as entrepreneurs and community pillars
- Avoid financial jargon
- **Never make specific return or investment performance promises**
- Always include appropriate disclaimers when referencing investment products

## Output format

For each variant, produce:

```
### Variant <N> — <hook label>

**Primary text** (≤ 125 chars before truncation in feed):
<text>

**Headline** (≤ 40 chars):
<text>

**Description** (≤ 30 chars, optional):
<text>

**CTA**: LEARN_MORE | SIGN_UP | APPLY_NOW | GET_OFFER

**Compliance disclaimer** (must appear, full version in body or as a stable footer):
"Investing involves risk. Securities offered through Honeycomb Portal LLC, member FINRA/SIPC."

**Targeting note**: <audience hint, e.g. "raisers, food and beverage, $50k–$500k revenue">
```

Produce 3 variants per request, each with a different hook (community pride, growth ambition, customer-as-investor angle, etc.). Label the hook so Tyler knows which is which.

## Hard rules

- Do NOT include specific APY / interest rate claims unless Tyler explicitly provides them in the prompt with approval context.
- Do NOT promise returns ("earn X%", "guaranteed yield", "passive income") — variants must rely on community / story / mission framing.
- Do NOT use urgency manipulation that violates Reg CF spirit ("limited time only — don't miss out!").
- Do NOT reference performance of past raises ("our raisers averaged X%").
- ALWAYS include the compliance line.

## When refreshing a fatigued ad

If invoked because of a fatigue alert:

1. Read the original ad's `body`, `title`, `call_to_action_type` from `data/creatives/creatives.json`
2. Identify the hook (what was the original ad selling — pride, opportunity, story?)
3. Generate variants that **change the hook**, not just the wording. Same hook = same audience reaction = same fatigue.
4. Note in the response: "Original hook: <X>. New hooks: <Y>, <Z>, <W>."

## What this skill does NOT do

- Upload to Meta (no API integration; that would defeat the human-in-the-loop principle)
- Promise legal review (Tyler's team handles compliance review separately)
- Generate creative imagery (text only)
- Edit the production `creatives.json` (read-only for this skill)
