#!/usr/bin/env python3
"""Build the Creative Intelligence variant dataset.

Reads the snapshot pipeline + creative cache; emits a JSON file the
SKILL.md prompt feeds to Claude at run-time. The dataset is the
load-bearing artifact for the skill — its shape determines what
briefs the analysis layer can produce.

Per docs/CREATIVE_INTELLIGENCE_DESIGN.md the attribution spine is
corpus-level text aggregation, not per-ad asset_id breakdown. Each
unique body/title/description text gets a stable variant_id (sha256
prefix), and per-variant performance comes from summing across the
ads where that variant appears. Side-by-side comparisons come from
ads that share an image_hash but differ on bodies (audience and
image held constant by selection).

Pipeline:
  1. Aggregate ad_insights across the lookback window per ad_id.
  2. Resolve ad_id -> creative_id from the latest ads.json snapshot.
  3. Resolve campaign_name -> vertical via the AD-<vertical>-Qx-YYYY
     regex.
  4. For each unique creative_id, ensure asset_feed_spec arrays are
     in the cache (re-fetch from Meta otherwise). Update cache.
  5. Resolve every image_hash to a full-size /adimages URL and
     download to data/creatives/images/<hash>.jpg.
  6. For each unique variant text (body/title/description), compute
     structural features and a stable variant_id. Build the corpus
     index variant_id -> list of ad_ids it appears in.
  7. Aggregate spend + IC + impressions per variant.
  8. Find side-by-side pairs (ads sharing an image_hash but differing
     on body text).
  9. Identify top/bottom decile ads by CPICP among ads with
     sufficient spend + days_active.
 10. Emit /tmp/creative_dataset.json.

Usage:
  python3 skills/creative-intelligence/scripts/build_creative_dataset.py
    [--lookback-days 30] [--output /tmp/creative_dataset.json]
    [--skip-meta]                            # use cache only

Environment:
  META_ACCESS_TOKEN     required unless --skip-meta
  META_AD_ACCOUNT_ID    optional override for benchmarks.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import (  # noqa: E402
    DEFAULT_SLEEP_BETWEEN_CALLS,
    MetaClient,
    download_image,
    load_config,
    normalize_creative,
)
from lib.text_features import compute_features, variant_id  # noqa: E402

CREATIVES_PATH = REPO_ROOT / "data" / "creatives" / "creatives.json"
CATEGORIES_PATH = REPO_ROOT / "data" / "creatives" / "categorizations.json"
IMAGES_DIR = REPO_ROOT / "data" / "creatives" / "images"
SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"

# Pattern: optional `PAUSED - ` prefix, then AD/ICD/Rev, then
# <vertical>, then Q<N>-<YYYY>. Matches campaigns like
# AD-Breweries-Q4-2025, AD-BBQ-Q1-2026, AD-Sustainable Main Street-
# Q1-2026, ICD-Health, Fitness & Personal Care-Q2-2026,
# PAUSED - AD-Creameries-Q1-2026.
VERTICAL_RE = re.compile(
    r"^(?:PAUSED\s*-\s*)?(?:AD|ICD|Rev\d*)-(.+?)-Q\d+-\d{4}$",
    re.IGNORECASE)
# Specific legacy / one-off campaign-name patterns that don't follow
# the standard AD-/ICD- structure but cleanly map to a vertical name.
LEGACY_VERTICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bwiner", re.IGNORECASE), "wineries"),
]
# Verticals to exclude from the dataset. "template" campaigns are
# infrastructure (e.g. Template-IC Conversion Event-4.8.2026 holds
# the IC pixel rather than running ads), not real audience segments.
EXCLUDED_VERTICALS = {"template", "unknown"}

# Floors below which per-ad performance is too thin to trust for
# decile ranking. Variants in low-spend ads still appear in the
# corpus index — the floor only gates inclusion in
# top_decile_ads / bottom_decile_ads.
MIN_SPEND_FOR_DECILE = 50.0
MIN_DAYS_ACTIVE_FOR_DECILE = 5


def extract_vertical(campaign_name: str | None) -> str:
    if not campaign_name:
        return "unknown"
    name = campaign_name.strip()
    m = VERTICAL_RE.match(name)
    if m:
        return m.group(1).strip().lower()
    if name.lower().startswith("template"):
        return "template"
    for pattern, slug in LEGACY_VERTICAL_PATTERNS:
        if pattern.search(name):
            return slug
    return name.lower()


def list_snapshot_dates_in_window(since: str, until: str) -> list[str]:
    if not SNAPSHOTS_DIR.exists():
        return []
    return sorted(
        p.name for p in SNAPSHOTS_DIR.iterdir()
        if p.is_dir() and since <= p.name <= until
    )


def aggregate_ad_performance(
        snapshot_dates: list[str]) -> dict[str, dict[str, Any]]:
    """Sum impressions / clicks / spend / ic_conversions per ad_id
    across the snapshot window. Track first/last active date and
    days_active. Pulls campaign_name from whichever insight row first
    has it (handles campaign renames mid-window by taking the most
    recent non-empty value)."""
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "impressions": 0, "clicks": 0, "spend": 0.0,
        "ic_conversions": 0, "first_date": None, "last_date": None,
        "active_dates": set(), "campaign_name": "", "ad_name": "",
    })
    for d in snapshot_dates:
        ins_path = SNAPSHOTS_DIR / d / "ad_insights.json"
        if not ins_path.exists():
            continue
        try:
            rows = json.loads(ins_path.read_text())
        except json.JSONDecodeError:
            logging.warning("malformed ad_insights for %s — skipping", d)
            continue
        for r in rows:
            ad_id = r.get("ad_id")
            if not ad_id:
                continue
            entry = agg[ad_id]
            imps = int(r.get("impressions") or 0)
            entry["impressions"] += imps
            entry["clicks"] += int(r.get("clicks") or 0)
            entry["spend"] += float(r.get("spend") or 0.0)
            entry["ic_conversions"] += int(r.get("ic_conversions") or 0)
            if imps > 0:
                entry["active_dates"].add(d)
                if not entry["first_date"] or d < entry["first_date"]:
                    entry["first_date"] = d
                if not entry["last_date"] or d > entry["last_date"]:
                    entry["last_date"] = d
            if r.get("campaign_name"):
                entry["campaign_name"] = r["campaign_name"]
            if r.get("ad_name"):
                entry["ad_name"] = r["ad_name"]
    for ad_id, entry in agg.items():
        entry["days_active"] = len(entry["active_dates"])
        del entry["active_dates"]
    return dict(agg)


def latest_ads_creative_map(latest_date: str) -> dict[str, dict[str, Any]]:
    """Resolve ad_id -> {creative_id, effective_status, ...} from the
    most recent ads.json snapshot. Drops ads whose creative_id is
    missing — those are ad shells without an attached creative."""
    path = SNAPSHOTS_DIR / latest_date / "ads.json"
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        logging.warning("malformed ads.json for %s — empty map", latest_date)
        return {}
    out = {}
    for r in rows:
        ad_id = r.get("ad_id")
        if ad_id and r.get("creative_id"):
            out[ad_id] = {
                "creative_id": r.get("creative_id"),
                "effective_status": r.get("effective_status"),
                "adset_id": r.get("adset_id"),
                "campaign_id": r.get("campaign_id"),
                "created_time": r.get("created_time"),
            }
    return out


def load_creatives_cache() -> dict[str, dict[str, Any]]:
    if not CREATIVES_PATH.exists():
        return {}
    try:
        payload = json.loads(CREATIVES_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("creatives cache unreadable (%s) — empty", exc)
        return {}
    return {c["creative_id"]: c
            for c in payload.get("creatives", [])
            if c.get("creative_id")}


def save_creatives_cache(cache: dict[str, dict[str, Any]]) -> None:
    CREATIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(cache),
        "creatives": sorted(cache.values(),
                            key=lambda c: c.get("creative_id") or ""),
    }
    with CREATIVES_PATH.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def ensure_creative_data(
        client: MetaClient,
        creative_ids: set[str],
        cache: dict[str, dict[str, Any]],
        ) -> tuple[dict[str, dict[str, Any]], int]:
    """For each creative_id, ensure the cache has variant arrays. Re-
    fetches from Meta for cache misses AND for cached entries that
    pre-date the lib/meta.py reshape (those have title/body=None and
    no bodies[]/titles[]/descriptions[]).

    Returns (updated cache, count of fresh fetches)."""
    today = datetime.now(timezone.utc).date().isoformat()
    fetched = 0
    for cid in creative_ids:
        cached = cache.get(cid)
        if cached and (cached.get("bodies") or cached.get("titles")
                        or cached.get("descriptions")):
            continue
        try:
            raw = client.creative(cid)
            normalized = normalize_creative(raw)
            cache[cid] = {
                **(cached or {}), **normalized,
                "first_seen_date": (cached or {}).get("first_seen_date")
                                    or today,
                "last_seen_date": today,
            }
            fetched += 1
        except RuntimeError as exc:
            logging.warning("creative %s fetch failed: %s", cid, exc)
    return cache, fetched


def resolve_and_cache_images(client: MetaClient,
                             cache: dict[str, dict[str, Any]],
                             ) -> dict[str, str]:
    """Resolve image_hashes via /adimages and download each to
    data/creatives/images/<hash>.jpg. Returns hash -> repo-relative
    path. Hashes that fail to resolve or download are omitted."""
    all_hashes: set[str] = set()
    for entry in cache.values():
        for h in (entry.get("image_hashes") or []):
            if h:
                all_hashes.add(h)

    needs_resolve = {
        h for h in all_hashes
        if not (IMAGES_DIR / f"{h}.jpg").exists()
        or (IMAGES_DIR / f"{h}.jpg").stat().st_size == 0
    }
    resolved_urls: dict[str, str] = {}
    if needs_resolve:
        logging.warning("resolving %d image hashes via /adimages",
                        len(needs_resolve))
        resolved = client.resolve_image_hashes(list(needs_resolve))
        for h, rec in resolved.items():
            if rec.get("url"):
                resolved_urls[h] = rec["url"]

    paths: dict[str, str] = {}
    for h in all_hashes:
        target = IMAGES_DIR / f"{h}.jpg"
        if target.exists() and target.stat().st_size > 0:
            paths[h] = str(target.relative_to(REPO_ROOT))
            continue
        url = resolved_urls.get(h)
        if not url:
            continue
        downloaded = download_image(h, url, IMAGES_DIR)
        if downloaded:
            paths[h] = str(downloaded.relative_to(REPO_ROOT))
    return paths


def collect_existing_image_paths(
        cache: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Catalogue locally-cached images without making any API calls."""
    paths: dict[str, str] = {}
    if not IMAGES_DIR.exists():
        return paths
    for entry in cache.values():
        for h in (entry.get("image_hashes") or []):
            if not h:
                continue
            target = IMAGES_DIR / f"{h}.jpg"
            if target.exists() and target.stat().st_size > 0:
                paths[h] = str(target.relative_to(REPO_ROOT))
    return paths


def load_categorizations() -> dict[str, dict[str, Any]]:
    """LLM tags produced by categorize_creative.py. Empty on first
    run before the categorizer has been pointed at the dataset."""
    if not CATEGORIES_PATH.exists():
        return {}
    try:
        return (json.loads(CATEGORIES_PATH.read_text())
                .get("categorizations", {}))
    except (json.JSONDecodeError, OSError):
        return {}


def build_variant_corpus(
        ad_to_creative: dict[str, str],
        cache: dict[str, dict[str, Any]],
        ) -> dict[str, dict[str, Any]]:
    """For each unique variant text across the creative pool, build an
    index entry with dimension, raw text, structural features, and the
    list of ad_ids it appears in (via creative_id linkage)."""
    variants: dict[str, dict[str, Any]] = {}
    for ad_id, creative_id in ad_to_creative.items():
        if not creative_id:
            continue
        creative = cache.get(creative_id)
        if not creative:
            continue
        for dimension, key in (("body", "bodies"),
                               ("title", "titles"),
                               ("description", "descriptions")):
            for text in (creative.get(key) or []):
                if not text:
                    continue
                vid = variant_id(text)
                entry = variants.get(vid)
                if entry is None:
                    entry = {
                        "variant_id": vid,
                        "dimension": dimension,
                        "text": text,
                        "structural": compute_features(text),
                        "appears_in_ads": [],
                    }
                    variants[vid] = entry
                if ad_id not in entry["appears_in_ads"]:
                    entry["appears_in_ads"].append(ad_id)
    return variants


def aggregate_variant_performance(
        variants: dict[str, dict[str, Any]],
        ad_performance: dict[str, dict[str, Any]],
        ) -> None:
    """In-place: for each variant, sum spend / impressions / IC across
    the ads where it appears."""
    for entry in variants.values():
        spend = 0.0
        imps = 0
        ic = 0
        for ad_id in entry["appears_in_ads"]:
            perf = ad_performance.get(ad_id) or {}
            spend += perf.get("spend", 0.0)
            imps += perf.get("impressions", 0)
            ic += perf.get("ic_conversions", 0)
        entry["ad_count"] = len(entry["appears_in_ads"])
        entry["total_spend"] = round(spend, 2)
        entry["total_impressions"] = imps
        entry["total_ic_conversions"] = ic
        entry["cpicp"] = round(spend / ic, 2) if ic > 0 else None


def find_side_by_side_pairs(
        ad_to_creative: dict[str, str],
        cache: dict[str, dict[str, Any]],
        ad_performance: dict[str, dict[str, Any]],
        ) -> list[dict[str, Any]]:
    """Find ad pairs that share at least one image_hash but differ on
    body text. The "same audience, same image, same time, different
    copy" comparison Tyler called load-bearing — Meta's audience
    targeting + image are held constant by the join, so any CPICP
    delta within a pair leans causally on the body difference."""
    by_image: dict[str, list[str]] = defaultdict(list)
    for ad_id, creative_id in ad_to_creative.items():
        creative = cache.get(creative_id) or {}
        for h in (creative.get("image_hashes") or []):
            if h:
                by_image[h].append(ad_id)

    out = []
    for image_hash, ads in by_image.items():
        if len(ads) < 2:
            continue
        ad_bodies = [
            (ad_id, set((cache.get(ad_to_creative[ad_id]) or {})
                        .get("bodies") or []))
            for ad_id in ads
        ]
        diff_pairs = []
        seen: set[tuple[str, str]] = set()
        for i, (ad_a, bodies_a) in enumerate(ad_bodies):
            for ad_b, bodies_b in ad_bodies[i + 1:]:
                only_a = bodies_a - bodies_b
                only_b = bodies_b - bodies_a
                if not (only_a or only_b):
                    continue
                key = tuple(sorted([ad_a, ad_b]))
                if key in seen:
                    continue
                seen.add(key)
                perf_a = ad_performance.get(ad_a) or {}
                perf_b = ad_performance.get(ad_b) or {}
                diff_pairs.append({
                    "ad_a": ad_a,
                    "ad_b": ad_b,
                    "ad_a_cpicp": (
                        round(perf_a["spend"] / perf_a["ic_conversions"], 2)
                        if perf_a.get("ic_conversions") else None),
                    "ad_b_cpicp": (
                        round(perf_b["spend"] / perf_b["ic_conversions"], 2)
                        if perf_b.get("ic_conversions") else None),
                    "bodies_only_in_a": list(only_a),
                    "bodies_only_in_b": list(only_b),
                })
        if diff_pairs:
            out.append({
                "image_hash": image_hash,
                "ad_count_sharing": len(ads),
                "differing_pairs": diff_pairs,
            })
    return out


def compute_decile_lists(
        ad_performance: dict[str, dict[str, Any]],
        ) -> tuple[list[str], list[str]]:
    """Return (top_decile_by_cpicp, bottom_decile_by_cpicp). Top = best
    = lowest CPICP. Both lists empty if fewer than 10 eligible ads."""
    eligible: list[tuple[str, float]] = []
    for ad_id, perf in ad_performance.items():
        if perf.get("ic_conversions", 0) <= 0:
            continue
        if perf.get("spend", 0.0) < MIN_SPEND_FOR_DECILE:
            continue
        if perf.get("days_active", 0) < MIN_DAYS_ACTIVE_FOR_DECILE:
            continue
        cpicp = perf["spend"] / perf["ic_conversions"]
        eligible.append((ad_id, cpicp))
    if len(eligible) < 10:
        return [], []
    eligible.sort(key=lambda kv: kv[1])
    n = max(1, len(eligible) // 10)
    return ([ad_id for ad_id, _ in eligible[:n]],
            [ad_id for ad_id, _ in eligible[-n:]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the Creative Intelligence variant dataset.")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--output", default="/tmp/creative_dataset.json")
    parser.add_argument("--sleep", type=float,
                        default=DEFAULT_SLEEP_BETWEEN_CALLS)
    parser.add_argument("--skip-meta", action="store_true",
                        help="Don't fetch fresh data from Meta; use cache only")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    account_id = (os.environ.get("META_AD_ACCOUNT_ID")
                  or config["account"]["id"])
    api_version = config["account"]["meta_api_version"]
    tz = ZoneInfo(config["account"]["timezone"])

    until_date = (datetime.now(tz) - timedelta(days=1)).date()
    since_date = until_date - timedelta(days=args.lookback_days - 1)

    snapshot_dates = list_snapshot_dates_in_window(
        since_date.isoformat(), until_date.isoformat())
    if not snapshot_dates:
        logging.error("No snapshots in %s..%s — run daily-data first",
                      since_date, until_date)
        return 2
    latest_date = snapshot_dates[-1]
    logging.warning("Snapshots %s..%s (%d days)",
                    snapshot_dates[0], latest_date, len(snapshot_dates))

    ad_performance = aggregate_ad_performance(snapshot_dates)
    ad_meta = latest_ads_creative_map(latest_date)
    ad_to_creative: dict[str, str] = {
        ad_id: meta["creative_id"]
        for ad_id, meta in ad_meta.items()
        if meta.get("creative_id")
    }
    ad_vertical = {ad_id: extract_vertical(perf.get("campaign_name"))
                   for ad_id, perf in ad_performance.items()}

    # Drop ads in excluded verticals (template, unknown) BEFORE the
    # variant corpus is built — keeps the dataset focused on real
    # audience segments and prevents Template-IC infrastructure
    # campaigns from appearing as a one-off "vertical" in briefs.
    excluded_ad_ids = {
        ad_id for ad_id, v in ad_vertical.items()
        if v in EXCLUDED_VERTICALS
    }
    if excluded_ad_ids:
        logging.warning("Excluding %d ads in template/unknown verticals",
                        len(excluded_ad_ids))
        for ad_id in excluded_ad_ids:
            ad_performance.pop(ad_id, None)
            ad_to_creative.pop(ad_id, None)
            ad_vertical.pop(ad_id, None)

    cache = load_creatives_cache()
    creative_ids_needed = {
        ad_to_creative[ad_id]
        for ad_id in ad_performance
        if ad_to_creative.get(ad_id)
    }

    if args.skip_meta:
        image_paths = collect_existing_image_paths(cache)
        fresh_count = 0
    else:
        token = os.environ.get("META_ACCESS_TOKEN")
        if not token:
            logging.error("META_ACCESS_TOKEN not set "
                          "(use --skip-meta for cache-only mode)")
            return 2
        client = MetaClient(account_id, api_version, token, args.sleep)
        cache, fresh_count = ensure_creative_data(
            client, creative_ids_needed, cache)
        if fresh_count > 0:
            save_creatives_cache(cache)
        image_paths = resolve_and_cache_images(client, cache)
    logging.warning("Re-fetched %d creatives; %d images cached",
                    fresh_count, len(image_paths))

    variants = build_variant_corpus(ad_to_creative, cache)
    aggregate_variant_performance(variants, ad_performance)

    # Tags are keyed by variant_id (text) OR image_hash (visual). The
    # categorizer namespaces them with `kind: copy` vs `kind: visual`
    # so we can attach the right field on each side.
    tags = load_categorizations()
    for vid, entry in variants.items():
        tag = tags.get(vid)
        if tag and tag.get("kind") == "copy":
            entry["llm_copy_angle"] = tag.get("copy_angle")
            entry["llm_copy_rationale"] = tag.get("rationale")
        else:
            entry["llm_copy_angle"] = None
            entry["llm_copy_rationale"] = None

    pairs = find_side_by_side_pairs(ad_to_creative, cache, ad_performance)
    top_ads, bottom_ads = compute_decile_lists(ad_performance)

    ads_out: list[dict[str, Any]] = []
    for ad_id, perf in ad_performance.items():
        if perf.get("impressions", 0) == 0:
            continue
        creative_id = ad_to_creative.get(ad_id)
        creative = cache.get(creative_id) or {} if creative_id else {}
        bodies = creative.get("bodies") or []
        titles = creative.get("titles") or []
        descriptions = creative.get("descriptions") or []
        local_paths = [
            image_paths[h]
            for h in (creative.get("image_hashes") or [])
            if h in image_paths
        ]
        # Visual styles come from the same categorizations cache,
        # keyed by image_hash. Each ad has up to 10 images, each with
        # its own style — the analysis layer wants the full list.
        visual_styles = []
        for h in (creative.get("image_hashes") or []):
            tag = tags.get(h)
            if tag and tag.get("kind") == "visual":
                visual_styles.append({
                    "image_hash": h,
                    "visual_style": tag.get("visual_style"),
                    "rationale": tag.get("rationale"),
                })
        cpicp = (round(perf["spend"] / perf["ic_conversions"], 2)
                 if perf.get("ic_conversions") else None)
        ads_out.append({
            "ad_id": ad_id,
            "ad_name": perf.get("ad_name"),
            "creative_id": creative_id,
            "vertical": ad_vertical.get(ad_id),
            "campaign_name": perf.get("campaign_name"),
            "effective_status": (ad_meta.get(ad_id) or {})
                                .get("effective_status"),
            "impressions": perf["impressions"],
            "clicks": perf["clicks"],
            "spend": round(perf["spend"], 2),
            "ic_conversions": perf["ic_conversions"],
            "cpicp": cpicp,
            "days_active": perf["days_active"],
            "first_active_date": perf["first_date"],
            "last_active_date": perf["last_date"],
            "body_count": len(bodies),
            "title_count": len(titles),
            "description_count": len(descriptions),
            "image_hashes": creative.get("image_hashes") or [],
            "local_image_paths": local_paths,
            "visual_styles": visual_styles,
            "variant_ids": {
                "bodies": [variant_id(t) for t in bodies],
                "titles": [variant_id(t) for t in titles],
                "descriptions": [variant_id(t) for t in descriptions],
            },
        })

    dataset = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_date.isoformat(),
        "until": until_date.isoformat(),
        "snapshot_count": len(snapshot_dates),
        "ad_count": len(ads_out),
        "variant_count": len(variants),
        "pair_group_count": len(pairs),
        "fresh_creative_fetches": fresh_count,
        "image_paths_cached": len(image_paths),
        "ads": sorted(ads_out, key=lambda a: a.get("cpicp") or 1e9),
        "variants": sorted(
            variants.values(),
            key=lambda v: v.get("cpicp") if v.get("cpicp") is not None
                          else 1e9),
        "side_by_side_pairs": pairs,
        "top_decile_ads": top_ads,
        "bottom_decile_ads": bottom_ads,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(dataset, f, indent=2, default=str)
    print(f"Wrote {output_path}: {len(ads_out)} ads, "
          f"{len(variants)} variants, {len(pairs)} pair groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
