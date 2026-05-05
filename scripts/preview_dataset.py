#!/usr/bin/env python3
"""Deterministic Creative Intelligence preview — no LLM, $0 cost.

Reads /tmp/creative_dataset.json (produced by build_creative_dataset.py)
and emits a Markdown brief using only Python: per-vertical winners
and losers by CPICP, structural-pattern deltas between cohorts,
side-by-side pairs (the cleanest causal-ish signal — same image,
different bodies, different outcomes), and a corpus-wide rollup.

The full Creative Intelligence skill layers an LLM brief on top
(claude-sonnet-4-5 reads the dataset + cached images, writes a
narrative brief with categorical framings). That layer costs ~$5
per run and adds polish; this script is the underlying signal at
$0 cost. Tyler can read this preview to decide whether the LLM
layer is worth the spend.

Usage:
    python3 scripts/preview_dataset.py [/path/to/creative_dataset.json]

If no path is provided, reads /tmp/creative_dataset.json (the
default output of build_creative_dataset.py).
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median, StatisticsError
from typing import Any

DEFAULT_INPUT = "/tmp/creative_dataset.json"

# Floors for what counts as a meaningful winner/loser at the per-ad level.
# Mirrors the floors in build_creative_dataset.py for decile lists.
MIN_AD_CPICP_FOR_RANKING = 0.01  # any non-null cpicp counts; the ad-level
                                  # spend/days_active floors are applied
                                  # by the dataset builder upstream.

# Width caps for inline Markdown rendering — keeps brief readable.
BODY_PREVIEW_CHARS = 110
TITLE_PREVIEW_CHARS = 60


def short(text: str | None, n: int) -> str:
    if not text:
        return "(none)"
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n - 1] + "…"


def fmt_cpicp(cpicp: float | None) -> str:
    return f"${cpicp:.2f}" if cpicp is not None else "n/a"


def avg(items: list[Any], key: str) -> float | None:
    """Mean of a feature across structural-features dicts."""
    values = [(v.get("structural") or {}).get(key)
              for v in items
              if (v.get("structural") or {}).get(key) is not None]
    return round(sum(values) / len(values), 1) if values else None


def share(items: list[Any], key: str) -> float | None:
    """Percentage of items where the named boolean feature is true."""
    values = [(v.get("structural") or {}).get(key) for v in items
              if (v.get("structural") or {}).get(key) is not None]
    if not values:
        return None
    return round(sum(1 for x in values if x) / len(values) * 100, 1)


def render_pattern_delta(winners: list[dict], losers: list[dict],
                         label: str, getter) -> str | None:
    """Compare a feature between winning and losing variant cohorts.
    Returns a one-line markdown bullet OR None if delta is uninformative."""
    w = getter(winners)
    l = getter(losers)
    if w is None or l is None:
        return None
    if abs(w - l) < 1.0:  # too small a delta to mention
        return None
    arrow = "↑" if w > l else "↓"
    return f"- **{label}** in winners: {w} {arrow} from losers' {l}"


def split_at_median(variants: list[dict]) -> tuple[list[dict], list[dict]]:
    """Same logic as ad-copy-generator: median CPICP split."""
    valid = [v for v in variants if v.get("cpicp") is not None]
    if len(valid) < 2:
        return valid[:5], []
    try:
        med = median(v["cpicp"] for v in valid)
    except StatisticsError:
        return valid[:5], []
    winners = sorted(
        [v for v in valid if v["cpicp"] < med],
        key=lambda v: v["cpicp"])[:5]
    losers = sorted(
        [v for v in valid if v["cpicp"] > med],
        key=lambda v: v["cpicp"], reverse=True)[:5]
    return winners, losers


def render_vertical(vert: str, ads: list[dict],
                    variants_in_vert: dict[str, list[dict]]) -> list[str]:
    """Per-vertical Markdown section."""
    out: list[str] = []
    cpicps = [a["cpicp"] for a in ads if a.get("cpicp") is not None]
    spend = sum(a.get("spend") or 0 for a in ads)
    ic = sum(a.get("ic_conversions") or 0 for a in ads)
    median_cpicp = median(cpicps) if cpicps else None

    out.append(f"## {vert}")
    out.append("")
    out.append(f"- **{len(ads)} ads** in window, "
               f"**{len(cpicps)} with IC conversions**, "
               f"median CPICP {fmt_cpicp(median_cpicp)}")
    out.append(f"- spend: ${spend:.2f}, IC conversions: {ic}")
    out.append("")

    # Per-ad ranking
    by_cpicp = sorted([a for a in ads if a.get("cpicp") is not None],
                      key=lambda a: a["cpicp"])
    if len(by_cpicp) >= 2:
        out.append("**Top 3 ads by CPICP:**")
        for ad in by_cpicp[:3]:
            bodies = ad.get("bodies") or []
            body_preview = short(bodies[0] if bodies else None,
                                 BODY_PREVIEW_CHARS)
            name = ad.get("ad_name") or ad.get("ad_id") or "(unnamed)"
            out.append(f"- {fmt_cpicp(ad['cpicp'])} — `{name}` "
                       f"— body[0]: \"{body_preview}\"")
        out.append("")
        out.append("**Bottom 3 ads by CPICP:**")
        for ad in by_cpicp[-3:]:
            bodies = ad.get("bodies") or []
            body_preview = short(bodies[0] if bodies else None,
                                 BODY_PREVIEW_CHARS)
            name = ad.get("ad_name") or ad.get("ad_id") or "(unnamed)"
            out.append(f"- {fmt_cpicp(ad['cpicp'])} — `{name}` "
                       f"— body[0]: \"{body_preview}\"")
        out.append("")

    # Per-variant winner/loser split — quote actual copy
    body_winners, body_losers = split_at_median(variants_in_vert["body"])
    title_winners, title_losers = split_at_median(variants_in_vert["title"])

    if body_winners or body_losers:
        out.append("**Body variants** (median-split by CPICP):")
        if body_winners:
            out.append("")
            out.append("_Winners (below median CPICP):_")
            for v in body_winners:
                out.append(f"- {fmt_cpicp(v.get('cpicp'))} "
                           f"({v.get('ad_count')} ads, "
                           f"{v.get('total_ic_conversions')} IC) — "
                           f"\"{short(v.get('text'), BODY_PREVIEW_CHARS)}\"")
        if body_losers:
            out.append("")
            out.append("_Losers (above median CPICP):_")
            for v in body_losers:
                out.append(f"- {fmt_cpicp(v.get('cpicp'))} "
                           f"({v.get('ad_count')} ads, "
                           f"{v.get('total_ic_conversions')} IC) — "
                           f"\"{short(v.get('text'), BODY_PREVIEW_CHARS)}\"")
        out.append("")

    if title_winners or title_losers:
        out.append("**Title variants** (median-split by CPICP):")
        if title_winners:
            out.append("")
            out.append("_Winners:_")
            for v in title_winners:
                out.append(f"- {fmt_cpicp(v.get('cpicp'))} — "
                           f"\"{short(v.get('text'), TITLE_PREVIEW_CHARS)}\"")
        if title_losers:
            out.append("")
            out.append("_Losers:_")
            for v in title_losers:
                out.append(f"- {fmt_cpicp(v.get('cpicp'))} — "
                           f"\"{short(v.get('text'), TITLE_PREVIEW_CHARS)}\"")
        out.append("")

    # Structural-pattern deltas
    all_winners = body_winners + title_winners
    all_losers = body_losers + title_losers
    if all_winners and all_losers:
        out.append("**Structural patterns** (winner cohort vs loser cohort):")
        deltas: list[str | None] = [
            render_pattern_delta(all_winners, all_losers,
                                  "avg word count",
                                  lambda items: avg(items, "word_count")),
            render_pattern_delta(all_winners, all_losers,
                                  "% with proper noun",
                                  lambda items: share(items, "has_proper_noun")),
            render_pattern_delta(all_winners, all_losers,
                                  "% with question mark",
                                  lambda items: share(items, "has_question_mark")),
            render_pattern_delta(all_winners, all_losers,
                                  "% with imperative opener",
                                  lambda items: share(items, "opens_with_imperative")),
            render_pattern_delta(all_winners, all_losers,
                                  "% with second-person",
                                  lambda items: share(items, "has_second_person")),
            render_pattern_delta(all_winners, all_losers,
                                  "% with number",
                                  lambda items: share(items, "has_number")),
            render_pattern_delta(all_winners, all_losers,
                                  "% with dollar amount",
                                  lambda items: share(items, "has_dollar_amount")),
        ]
        rendered = [d for d in deltas if d]
        if rendered:
            out.extend(rendered)
        else:
            out.append("- (no significant deltas — winners and losers "
                        "share structural fingerprints)")
        out.append("")

    return out


def render_side_by_side(pairs: list[dict],
                        ads_by_id: dict[str, dict]) -> list[str]:
    """The cleanest causal signal: same image, different bodies,
    different CPICP."""
    if not pairs:
        return []

    out: list[str] = []
    out.append("## Side-by-side pairs")
    out.append("")
    out.append(f"_Same image, different bodies — audience and creative "
               f"image held constant by selection. **{len(pairs)} image "
               f"groups** with differing bodies in this window._")
    out.append("")

    # Sort pair groups by largest CPICP delta first — those are the
    # most informative
    def best_delta(group: dict) -> float:
        deltas = []
        for pair in group.get("differing_pairs") or []:
            a = pair.get("ad_a_cpicp")
            b = pair.get("ad_b_cpicp")
            if a is not None and b is not None:
                deltas.append(abs(a - b))
        return max(deltas) if deltas else 0

    sorted_groups = sorted(pairs, key=best_delta, reverse=True)
    for group in sorted_groups[:8]:  # top 8 groups by delta
        ih = group.get("image_hash", "")
        out.append(f"### Image `{ih[:12]}…` "
                   f"({group.get('ad_count_sharing')} ads share it)")
        out.append("")
        for pair in (group.get("differing_pairs") or [])[:3]:
            ad_a_id = pair.get("ad_a")
            ad_b_id = pair.get("ad_b")
            ad_a = ads_by_id.get(ad_a_id, {})
            ad_b = ads_by_id.get(ad_b_id, {})
            out.append(f"- **{ad_a.get('ad_name', ad_a_id)}** "
                       f"({fmt_cpicp(pair.get('ad_a_cpicp'))}) "
                       f"vs **{ad_b.get('ad_name', ad_b_id)}** "
                       f"({fmt_cpicp(pair.get('ad_b_cpicp'))})")
            for body in (pair.get("bodies_only_in_a") or [])[:2]:
                out.append(f"  - _only in {ad_a.get('ad_name', 'A')}:_ "
                            f"\"{short(body, BODY_PREVIEW_CHARS)}\"")
            for body in (pair.get("bodies_only_in_b") or [])[:2]:
                out.append(f"  - _only in {ad_b.get('ad_name', 'B')}:_ "
                            f"\"{short(body, BODY_PREVIEW_CHARS)}\"")
        out.append("")
    return out


def render_corpus_rollup(ads: list[dict], variants: list[dict],
                          decile_top: list[str],
                          decile_bottom: list[str],
                          ads_by_id: dict[str, dict]) -> list[str]:
    out: list[str] = []
    out.append("## Corpus rollup")
    out.append("")

    cpicps = [a["cpicp"] for a in ads if a.get("cpicp") is not None]
    if cpicps:
        out.append(f"- portfolio median CPICP: ${median(cpicps):.2f}")
        out.append(f"- ads with IC conversions: {len(cpicps)} of {len(ads)}")
    out.append(f"- unique variants in corpus: {len(variants)}")
    out.append("")

    if decile_top:
        out.append("**Top decile ads** (lowest CPICP):")
        for ad_id in decile_top:
            ad = ads_by_id.get(ad_id, {})
            out.append(f"- {fmt_cpicp(ad.get('cpicp'))} — "
                        f"`{ad.get('ad_name', ad_id)}` "
                        f"({ad.get('vertical', 'unknown')})")
        out.append("")

    if decile_bottom:
        out.append("**Bottom decile ads** (highest CPICP):")
        for ad_id in decile_bottom:
            ad = ads_by_id.get(ad_id, {})
            out.append(f"- {fmt_cpicp(ad.get('cpicp'))} — "
                        f"`{ad.get('ad_name', ad_id)}` "
                        f"({ad.get('vertical', 'unknown')})")
        out.append("")

    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    input_path = argv[0] if argv else DEFAULT_INPUT

    try:
        with open(input_path) as f:
            dataset = json.load(f)
    except FileNotFoundError:
        sys.stderr.write(
            f"ERROR: dataset not found at {input_path}. "
            f"Run build_creative_dataset.py first.\n")
        return 2
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"ERROR: malformed JSON: {exc}\n")
        return 2

    ads = dataset.get("ads") or []
    variants = dataset.get("variants") or []
    pairs = dataset.get("side_by_side_pairs") or []
    decile_top = dataset.get("top_decile_ads") or []
    decile_bottom = dataset.get("bottom_decile_ads") or []

    if not ads:
        sys.stderr.write("ERROR: dataset has no ads\n")
        return 2

    ads_by_id = {a["ad_id"]: a for a in ads if a.get("ad_id")}

    # Group variants by vertical via the ads they appear in
    variants_by_vert_dim: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: {"body": [], "title": [], "description": []})
    for v in variants:
        # variants list per-vertical via their ads' vertical attribute
        verts_for_variant = {ads_by_id.get(aid, {}).get("vertical")
                             for aid in (v.get("appears_in_ads") or [])}
        for vert in verts_for_variant:
            if vert and v.get("dimension") in variants_by_vert_dim[vert]:
                variants_by_vert_dim[vert][v["dimension"]].append(v)

    # Group ads by vertical
    ads_by_vert: dict[str, list[dict]] = defaultdict(list)
    for ad in ads:
        if ad.get("vertical"):
            ads_by_vert[ad["vertical"]].append(ad)

    # Render
    out: list[str] = []
    out.append("# Creative Intelligence — Deterministic Preview")
    out.append("")
    out.append(f"_Generated {datetime.now(timezone.utc).isoformat()} from "
               f"`{input_path}`._")
    out.append("")
    out.append(f"**Source corpus:** {dataset.get('since')} → "
               f"{dataset.get('until')} ({dataset.get('snapshot_count')} "
               f"daily snapshots).")
    out.append("")
    out.append("**This is the $0 deterministic preview** — pure Python, "
               "no LLM. The full Creative Intelligence skill layers an "
               "LLM brief on top of the same dataset (~$5/run on Sonnet "
               "4.5 to categorize ~400 unique variants). Read this "
               "preview to decide whether the LLM layer is worth the "
               "spend on top.")
    out.append("")
    out.append("---")
    out.append("")

    # Corpus rollup first
    out.extend(render_corpus_rollup(ads, variants, decile_top,
                                     decile_bottom, ads_by_id))

    # Side-by-side pairs second — load-bearing causal-ish signal
    out.extend(render_side_by_side(pairs, ads_by_id))

    # Per-vertical sections, sorted by ad count descending
    sorted_verts = sorted(ads_by_vert.items(),
                          key=lambda kv: -len(kv[1]))
    for vert, vert_ads in sorted_verts:
        if len(vert_ads) < 3:
            continue  # too few ads for meaningful per-vertical view
        out.extend(render_vertical(vert, vert_ads,
                                    variants_by_vert_dim.get(vert, {
                                        "body": [], "title": [],
                                        "description": []})))

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
