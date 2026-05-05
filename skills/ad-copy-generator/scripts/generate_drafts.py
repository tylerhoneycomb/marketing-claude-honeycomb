#!/usr/bin/env python3
"""Draft ad-copy variants from the Creative Intelligence dataset.

Reads /tmp/creative_dataset.json (produced by build_creative_dataset.py),
selects the winning patterns for a target vertical, and asks Claude
to draft 3-5 new variants following those patterns. Output is a
human-readable markdown file in data/drafts/<date>-<vertical>.md
plus stdout summary for interactive runs.

Drafts are NOT auto-published. Every draft needs human review per
the compliance checklist before going live in any campaign.

Usage:
    python3 skills/ad-copy-generator/scripts/generate_drafts.py
        --vertical breweries
        [--num-drafts 5]
        [--input /tmp/creative_dataset.json]
        [--output data/drafts/<date>-<vertical>.md]
        [--all-verticals]            # generate for every vertical with confidence
        [--min-vertical-ads 5]       # confidence floor
        [--model claude-sonnet-4-5]
        [--dry-run]                  # print prompt, skip API call

Environment:
    ANTHROPIC_API_KEY    required (unless --dry-run)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median, StatisticsError
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
REFS_DIR = Path(__file__).resolve().parents[1] / "references"
DRAFTS_DIR = REPO_ROOT / "data" / "drafts"

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_INPUT = "/tmp/creative_dataset.json"

# Block-list for the compliance regex backstop. NOT a substitute for
# human review — the human checklist in compliance_rules.md catches
# the long tail. This regex catches the most-likely-to-slip-through
# violations: numeric returns, guarantees, FDIC comparisons.
COMPLIANCE_BLOCKLIST: list[tuple[str, re.Pattern[str]]] = [
    ("quantified return",
     re.compile(r"\b\d+(?:\.\d+)?\s*%\s*(?:apy|return|annually|interest|yield|gain)\b",
                re.IGNORECASE)),
    ("multiple-x return",
     re.compile(r"\b\d+(?:\.\d+)?x\s+(?:return|returns|gain|growth)\b",
                re.IGNORECASE)),
    ("guarantee language",
     re.compile(r"\b(?:guarantee[ds]?|risk[\s-]?free|certain[\s-]?return|"
                r"promised\s+return|secure\s+return|assured\s+return)\b",
                re.IGNORECASE)),
    ("FDIC comparison",
     re.compile(r"\bfdic[\s-]*insured\b", re.IGNORECASE)),
    ("specific dollar return",
     re.compile(r"\bearn(?:ed|s)?\s+\$\d", re.IGNORECASE)),
]

DRAFT_TOOL = {
    "name": "draft_ads",
    "description": ("Draft new ad-copy variants for the target vertical "
                    "based on the winning patterns shown."),
    "input_schema": {
        "type": "object",
        "properties": {
            "patterns_observed": {
                "type": "string",
                "description": ("2-3 sentences describing the structural "
                                "patterns that distinguish winners from "
                                "losers in the source data. Be specific: "
                                "name the opening-word patterns, the "
                                "length differences, the syntactic "
                                "markers."),
            },
            "drafts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "body": {
                            "type": "string",
                            "description": ("Body copy. 25-50 words "
                                            "target. Must follow the "
                                            "voice + compliance rules."),
                        },
                        "title": {
                            "type": "string",
                            "description": ("Title / headline. 4-8 words "
                                            "target. Punchy."),
                        },
                        "description": {
                            "type": "string",
                            "description": ("Tagline-style description. "
                                            "4-8 words. Often a stat "
                                            "or a contrast."),
                        },
                        "pattern_followed": {
                            "type": "string",
                            "description": ("One sentence explaining "
                                            "which winning pattern this "
                                            "draft applies and what it "
                                            "borrows from the source "
                                            "data."),
                        },
                    },
                    "required": ["body", "title", "description",
                                 "pattern_followed"],
                },
            },
        },
        "required": ["patterns_observed", "drafts"],
    },
}


def load_references() -> tuple[str, str]:
    voice = (REFS_DIR / "voice_guide.md").read_text()
    compliance = (REFS_DIR / "compliance_rules.md").read_text()
    return voice, compliance


def load_dataset(path: Path) -> dict[str, Any]:
    if not path.exists():
        sys.stderr.write(
            f"ERROR: dataset not found at {path}. Run "
            f"build_creative_dataset.py first.\n")
        sys.exit(2)
    return json.loads(path.read_text())


def variants_for_vertical(dataset: dict[str, Any],
                          vertical: str) -> dict[str, list[dict[str, Any]]]:
    """For one vertical, return per-dimension lists of variants that
    appeared in ads with sufficient signal. Variants are sorted by
    cpicp ascending (winners first). Includes ALL variants — caller
    decides how many to take from each end."""
    target = vertical.strip().lower()
    ads_in_vertical = {
        ad["ad_id"] for ad in dataset.get("ads", [])
        if (ad.get("vertical") or "").lower() == target
    }
    if not ads_in_vertical:
        return {"body": [], "title": [], "description": []}

    by_dim: dict[str, list[dict[str, Any]]] = {
        "body": [], "title": [], "description": [],
    }
    for v in dataset.get("variants", []):
        # Only variants that appear in this vertical's ads
        if not (set(v.get("appears_in_ads") or []) & ads_in_vertical):
            continue
        if v.get("dimension") in by_dim:
            by_dim[v["dimension"]].append(v)

    # Sort each dimension by CPICP ascending; null CPICP variants
    # sink to the bottom.
    for dim in by_dim:
        by_dim[dim].sort(
            key=lambda x: (x.get("cpicp") if x.get("cpicp") is not None
                            else float("inf"))
        )
    return by_dim


def vertical_summary(dataset: dict[str, Any], vertical: str) -> dict[str, Any]:
    target = vertical.strip().lower()
    ads = [a for a in dataset.get("ads", [])
           if (a.get("vertical") or "").lower() == target]
    ad_count = len(ads)
    cpicps = [a["cpicp"] for a in ads if a.get("cpicp") is not None]
    spend_total = sum(a.get("spend") or 0 for a in ads)
    ic_total = sum(a.get("ic_conversions") or 0 for a in ads)
    try:
        median_cpicp = round(median(cpicps), 2) if cpicps else None
    except StatisticsError:
        median_cpicp = None
    return {
        "vertical": target,
        "ad_count": ad_count,
        "ads_with_conversions": len(cpicps),
        "spend_total": round(spend_total, 2),
        "ic_total": ic_total,
        "median_cpicp": median_cpicp,
    }


def structural_patterns(winners: list[dict[str, Any]],
                        losers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare structural-feature distributions between winning and
    losing variants. Returns a dict the prompt can render as bullet
    points."""
    def avg(items: list[dict[str, Any]], key: str) -> float | None:
        values = [(v.get("structural") or {}).get(key)
                  for v in items
                  if (v.get("structural") or {}).get(key) is not None]
        return round(sum(values) / len(values), 1) if values else None

    def share(items: list[dict[str, Any]], key: str) -> float | None:
        values = [(v.get("structural") or {}).get(key) for v in items
                  if (v.get("structural") or {}).get(key) is not None]
        if not values:
            return None
        return round(sum(1 for x in values if x) / len(values) * 100, 1)

    return {
        "winners_count": len(winners),
        "losers_count": len(losers),
        "avg_word_count": {
            "winners": avg(winners, "word_count"),
            "losers": avg(losers, "word_count"),
        },
        "avg_sentence_count": {
            "winners": avg(winners, "sentence_count"),
            "losers": avg(losers, "sentence_count"),
        },
        "pct_with_proper_noun": {
            "winners": share(winners, "has_proper_noun"),
            "losers": share(losers, "has_proper_noun"),
        },
        "pct_question_mark": {
            "winners": share(winners, "has_question_mark"),
            "losers": share(losers, "has_question_mark"),
        },
        "pct_imperative_opener": {
            "winners": share(winners, "opens_with_imperative"),
            "losers": share(losers, "opens_with_imperative"),
        },
        "pct_second_person": {
            "winners": share(winners, "has_second_person"),
            "losers": share(losers, "has_second_person"),
        },
        "pct_number": {
            "winners": share(winners, "has_number"),
            "losers": share(losers, "has_number"),
        },
    }


def format_variant_excerpts(variants: list[dict[str, Any]],
                            limit: int = 5) -> str:
    """Pretty-print the top-N variants for a dimension as bullet points
    the prompt can show inline."""
    out = []
    for v in variants[:limit]:
        cpicp = v.get("cpicp")
        cpicp_str = f"${cpicp}" if cpicp is not None else "no IC yet"
        out.append(
            f'  - "{v.get("text", "")}"  '
            f'(ad_count={v.get("ad_count", 0)}, '
            f'IC={v.get("total_ic_conversions", 0)}, '
            f'CPICP={cpicp_str})'
        )
    return "\n".join(out) if out else "  (none)"


def build_prompt(vertical: str,
                 summary: dict[str, Any],
                 winners: dict[str, list[dict[str, Any]]],
                 losers: dict[str, list[dict[str, Any]]],
                 patterns: dict[str, Any],
                 num_drafts: int,
                 voice_guide: str,
                 compliance_rules: str) -> tuple[str, str]:
    """Build the (system, user) message pair for the Anthropic call."""
    system = (
        "You are drafting ad-copy variants for Honeycomb Credit, a "
        "small-business investment-crowdfunding platform. Your audience "
        "is small-business owners (in this case: " + vertical + " "
        "owners) considering raising capital from their customers. "
        "Output strictly via the `draft_ads` tool — do not respond "
        "with prose.\n\n"
        "## Voice guide\n\n" + voice_guide + "\n\n"
        "## Compliance rules\n\n" + compliance_rules + "\n\n"
        "Every draft must satisfy the compliance rules. The reviewer "
        "checklist will catch some issues; you should not let any "
        "draft contain quantified return promises, guarantee language, "
        "or FDIC comparisons. When in doubt, hew toward the patterns "
        "in the source data — they have already cleared compliance."
    )

    user = (
        f"# {vertical} — source data\n\n"
        f"- ads in vertical: {summary['ad_count']}\n"
        f"- ads with IC conversions: {summary['ads_with_conversions']}\n"
        f"- median CPICP: "
        f"{'$' + str(summary['median_cpicp']) if summary['median_cpicp'] is not None else 'n/a'}\n"
        f"- total spend: ${summary['spend_total']}\n"
        f"- total IC conversions: {summary['ic_total']}\n\n"
        "## Top-performing bodies (lowest CPICP, real corpus quotes)\n"
        f"{format_variant_excerpts(winners['body'], 5)}\n\n"
        "## Top-performing titles\n"
        f"{format_variant_excerpts(winners['title'], 5)}\n\n"
        "## Top-performing descriptions\n"
        f"{format_variant_excerpts(winners['description'], 5)}\n\n"
        "## Bottom bodies (highest CPICP — patterns to avoid)\n"
        f"{format_variant_excerpts(losers['body'], 5)}\n\n"
        "## Bottom titles\n"
        f"{format_variant_excerpts(losers['title'], 5)}\n\n"
        "## Structural patterns\n"
        f"```\n{json.dumps(patterns, indent=2)}\n```\n\n"
        f"Draft {num_drafts} new variants. Each variant is a "
        "(body, title, description) triple. The bodies must follow "
        "the structural and copy patterns of the winners, not the "
        "losers. Quote specific patterns in your `pattern_followed` "
        "field — explain which winning example each draft borrows "
        "from. Open `patterns_observed` with a 2-3 sentence summary "
        "of what distinguishes winners from losers in this data."
    )
    return system, user


def call_claude(client: Any, model: str, system: str, user: str,
                ) -> dict[str, Any] | None:
    """Single call. Returns the tool_use input dict or None on failure."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            tools=[DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "draft_ads"},
            messages=[{"role": "user", "content": user}],
        )
        for block in getattr(resp, "content", None) or []:
            if getattr(block, "type", None) == "tool_use":
                return getattr(block, "input", None) or {}
        logging.warning("no tool_use block in response")
        return None
    except Exception as exc:  # noqa: BLE001 — surface SDK errors verbatim
        logging.error("Anthropic call failed: %s", exc)
        return None


def compliance_check(text: str) -> list[str]:
    """Return a list of human-readable violations, or empty list if
    clean."""
    flags: list[str] = []
    for label, pattern in COMPLIANCE_BLOCKLIST:
        if pattern.search(text):
            flags.append(label)
    return flags


def render_markdown(vertical: str, summary: dict[str, Any],
                    patterns_observed: str,
                    drafts: list[dict[str, Any]],
                    structural: dict[str, Any],
                    model: str) -> str:
    today = date.today().isoformat()
    body = []
    body.append(f"# Ad-copy drafts — {vertical} — {today}")
    body.append("")
    body.append(f"_Generated by ad-copy-generator. Model: `{model}`. "
                f"Source: `/tmp/creative_dataset.json`._")
    body.append("")
    body.append("**These are drafts. Every draft requires human review "
                "before publication. See the reviewer checklist at the "
                "bottom of this file.**")
    body.append("")
    body.append("## Source corpus")
    body.append("")
    body.append(f"- ads in vertical: **{summary['ad_count']}**")
    body.append(f"- ads with IC conversions: "
                f"**{summary['ads_with_conversions']}**")
    if summary.get("median_cpicp") is not None:
        body.append(f"- median CPICP: **${summary['median_cpicp']}**")
    body.append(f"- total spend: **${summary['spend_total']}**")
    body.append(f"- total IC conversions: **{summary['ic_total']}**")
    body.append("")
    body.append("## Patterns observed (model summary)")
    body.append("")
    body.append(patterns_observed)
    body.append("")
    body.append("## Structural pattern data")
    body.append("")
    body.append("```json")
    body.append(json.dumps(structural, indent=2))
    body.append("```")
    body.append("")
    body.append("## Drafts")
    body.append("")

    for i, d in enumerate(drafts, 1):
        flags = (compliance_check(d.get("body", ""))
                 + compliance_check(d.get("title", ""))
                 + compliance_check(d.get("description", "")))
        body.append(f"### Draft {i}")
        body.append("")
        body.append(f"**Body:** {d.get('body', '')}")
        body.append("")
        body.append(f"**Title:** {d.get('title', '')}")
        body.append("")
        body.append(f"**Description:** {d.get('description', '')}")
        body.append("")
        body.append(f"_Pattern followed:_ {d.get('pattern_followed', '')}")
        body.append("")
        if flags:
            body.append(f"⚠️ **Compliance flags:** {', '.join(flags)}. "
                        f"DO NOT publish without revision.")
            body.append("")
        else:
            body.append("✓ Compliance regex backstop: no flags.")
            body.append("")
    body.append("## Reviewer checklist")
    body.append("")
    body.append("Before publishing any draft above:")
    body.append("")
    body.append("- [ ] No specific return numbers or quantified APY")
    body.append("- [ ] No guarantee language")
    body.append("- [ ] \"Investing involves risk\" is included or queued")
    body.append("- [ ] Stats (500+ / $50M+) match current platform totals")
    body.append("- [ ] CTA is prequalification or learn-more, not an "
                "investment commitment")
    body.append("- [ ] Targets small-business owner, not end investor")
    body.append("- [ ] Compliance team review (if any)")
    body.append("")
    body.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_")
    body.append("")
    return "\n".join(body)


def generate_for_vertical(vertical: str, dataset: dict[str, Any],
                          num_drafts: int, model: str,
                          voice: str, compliance: str,
                          dry_run: bool, client: Any | None,
                          ) -> tuple[Path | None, str]:
    """Generate drafts for one vertical. Returns (output_path,
    stdout_summary). output_path is None on dry-run or failure."""
    summary = vertical_summary(dataset, vertical)
    if summary["ad_count"] == 0:
        return None, f"  {vertical}: no ads in dataset, skipping"

    by_dim = variants_for_vertical(dataset, vertical)
    # Split each dimension at its median CPICP — variants below
    # median are "winners", above median are "losers". Using a
    # naive top-5 / bottom-5 instead would label small variant
    # pools incorrectly: with only 3 variants in a dim, top-5 by
    # ascending CPICP includes the WORST variant. Median split
    # guarantees winners and losers are always distinct cohorts.
    winners: dict[str, list[dict[str, Any]]] = {}
    losers: dict[str, list[dict[str, Any]]] = {}
    for dim, variants in by_dim.items():
        valid = [v for v in variants if v.get("cpicp") is not None]
        if len(valid) < 2:
            winners[dim] = valid[:5]
            losers[dim] = []
            continue
        try:
            med = median(v["cpicp"] for v in valid)
        except StatisticsError:
            winners[dim] = valid[:5]
            losers[dim] = []
            continue
        winners[dim] = sorted(
            [v for v in valid if v["cpicp"] < med],
            key=lambda v: v["cpicp"])[:5]
        losers[dim] = sorted(
            [v for v in valid if v["cpicp"] > med],
            key=lambda v: v["cpicp"], reverse=True)[:5]

    structural = structural_patterns(
        winners["body"] + winners["title"] + winners["description"],
        losers["body"] + losers["title"] + losers["description"],
    )

    system, user = build_prompt(
        vertical, summary, winners, losers, structural,
        num_drafts, voice, compliance)

    if dry_run:
        print(f"=== {vertical}: prompt preview (dry-run) ===")
        print("[system message length:", len(system), "chars]")
        print(user)
        return None, f"  {vertical}: dry-run only"

    if client is None:
        return None, f"  {vertical}: ERROR — no API client"

    result = call_claude(client, model, system, user)
    if not result or not result.get("drafts"):
        return None, f"  {vertical}: ERROR — Claude call failed or empty"

    md = render_markdown(vertical, summary,
                         result.get("patterns_observed", ""),
                         result["drafts"], structural, model)

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    safe_vertical = re.sub(r"[^a-z0-9_-]+", "-", vertical.lower()).strip("-")
    output_path = DRAFTS_DIR / f"{today}-{safe_vertical}.md"
    output_path.write_text(md)

    flag_count = sum(
        1 for d in result["drafts"]
        if compliance_check(d.get("body", ""))
        or compliance_check(d.get("title", ""))
        or compliance_check(d.get("description", ""))
    )
    return output_path, (
        f"  {vertical}: {len(result['drafts'])} drafts → "
        f"{output_path.relative_to(REPO_ROOT)} "
        f"({flag_count} flagged)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Draft ad-copy variants from the Creative "
                    "Intelligence dataset.")
    parser.add_argument("--vertical",
                        help="Target vertical (e.g. 'breweries'). "
                             "Required unless --all-verticals.")
    parser.add_argument("--all-verticals", action="store_true",
                        help="Generate drafts for every vertical "
                             "with at least --min-vertical-ads ads.")
    parser.add_argument("--min-vertical-ads", type=int, default=5,
                        help="Skip verticals with fewer ads than "
                             "this. Default 5.")
    parser.add_argument("--num-drafts", type=int, default=5)
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        type=Path,
                        help="Path to creative_dataset.json")
    parser.add_argument("--output", type=Path,
                        help="Override output path. Only valid with "
                             "--vertical (single-vertical mode).")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the prompt without calling the API")
    args = parser.parse_args(argv)

    if not args.vertical and not args.all_verticals:
        parser.error("must specify --vertical or --all-verticals")
    if args.output and not args.vertical:
        parser.error("--output requires --vertical (single-vertical mode)")

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s")

    dataset = load_dataset(args.input)

    # Stale-cache guard: if the dataset has ads but no variants, the
    # creatives.json cache hasn't been refreshed by
    # build_creative_dataset.py against Meta yet (it's still in the
    # pre-asset_feed_spec-reshape shape). Generating drafts in that
    # state would just error per-vertical with cryptic "no winners"
    # messages. Surface the real cause up front.
    variant_count = len(dataset.get("variants") or [])
    ad_count = len(dataset.get("ads") or [])
    if ad_count > 0 and variant_count == 0:
        sys.stderr.write(
            f"ERROR: dataset at {args.input} has {ad_count} ads but 0 "
            f"variants. The creative cache hasn't been refreshed "
            f"against Meta yet. Run agent-creative-intelligence (or "
            f"build_creative_dataset.py with META_ACCESS_TOKEN set) "
            f"first.\n")
        return 2

    voice, compliance = load_references()

    client: Any | None = None
    if not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            sys.stderr.write("ERROR: ANTHROPIC_API_KEY not set "
                             "(use --dry-run to skip API)\n")
            return 2
        import anthropic  # noqa: PLC0415
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",
        )

    targets: list[str]
    if args.vertical:
        targets = [args.vertical]
    else:
        # Pick verticals with >= --min-vertical-ads
        from collections import Counter
        counts = Counter((a.get("vertical") or "").lower()
                          for a in dataset.get("ads", []))
        targets = sorted(v for v, n in counts.items()
                          if n >= args.min_vertical_ads and v)

    if not targets:
        sys.stderr.write("ERROR: no eligible verticals "
                         f"(min-vertical-ads={args.min_vertical_ads})\n")
        return 2

    print(f"Generating drafts for {len(targets)} vertical(s) "
          f"(model={args.model}, num_drafts={args.num_drafts})")

    summaries = []
    for v in targets:
        path, summary_line = generate_for_vertical(
            v, dataset, args.num_drafts, args.model,
            voice, compliance, args.dry_run, client)
        # Override path if --output specified for single-vertical mode
        if path and args.output and len(targets) == 1:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(path.read_text())
            path.unlink()
            summary_line = (f"  {v}: drafts → "
                            f"{args.output.relative_to(REPO_ROOT)}")
        summaries.append(summary_line)

    print("\n".join(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
