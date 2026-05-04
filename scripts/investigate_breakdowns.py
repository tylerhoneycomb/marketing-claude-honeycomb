#!/usr/bin/env python3
"""
Round 2 investigation for Skill 4 (Creative Intelligence). Two
load-bearing questions remain after round 1 (which confirmed copy
text lives in asset_feed_spec.bodies[]/titles[]/descriptions[]):

  Q1. Does Meta expose ASSET-LEVEL BREAKDOWN insights for these
      asset_feed_spec ads via the standard insights endpoint with
      breakdowns=body_asset,title_asset,description_asset,image_asset?
      Without this, every per-variant claim is actually an ad-level
      claim wearing variant clothing — same audience, same image,
      same day, different bodies all get the same CPICP attributed.
      With it, we get a clean natural experiment per ad.

  Q2. Does image_hash → /act_X/adimages return a full-size URL? The
      top-level `image_url` field is empty for these ads, so the
      asset feed embeds image references as `hash` inside
      asset_feed_spec.images[]. We need to resolve those hashes to
      actual URLs.

Strategy:
  1. Pick an ad with recent traffic (find one in the most recent
     ad_insights snapshot with the largest impression count).
  2. Call /{ad_id}/insights with the breakdown dimensions over the
     last 30 days. Print row count, sample rows, which asset_id
     fields populate, whether impressions/spend/conversions are
     distributed unevenly across asset combinations (the signal we
     want).
  3. Fetch that ad's creative; pull image hashes out of
     asset_feed_spec.images[]. Call /act_X/adimages with those
     hashes; print whether we get full-size URLs back.

Run via the workflow_dispatch wrapper at
.github/workflows/investigate-breakdowns.yml. Output goes to the
"Run investigation" step log.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import MetaClient, ic_action_type_from_config, load_config  # noqa: E402

ASSET_BREAKDOWNS = "body_asset,title_asset,description_asset,image_asset"
# Note: asset breakdown dimensions are NOT listed in fields=. Meta
# returns them as row columns automatically when you set breakdowns=.
# Listing them in fields= triggers HTTP 400 ("not valid for fields
# param"). Standard insight metrics still need to be requested.
ASSET_INSIGHT_FIELDS = [
    "ad_id",
    "ad_name",
    "impressions",
    "clicks",
    "spend",
    "actions",
]

# Same expanded creative field set as round 1 — we need
# asset_feed_spec to mine image hashes.
EXPANDED_CREATIVE_FIELDS = [
    "id", "name", "asset_feed_spec", "effective_object_story_id",
    "image_hash", "thumbnail_url",
]


def find_ad_with_traffic() -> str | None:
    """Pick the ad_id with the most impressions from the most recent
    daily ad_insights snapshot. We want a creative that's actually
    been delivered enough for breakdowns to be informative."""
    snapshots_dir = REPO_ROOT / "data" / "snapshots"
    if not snapshots_dir.exists():
        return None
    dates = sorted([p.name for p in snapshots_dir.iterdir()
                    if p.is_dir()], reverse=True)
    for d in dates:
        insights_path = snapshots_dir / d / "ad_insights.json"
        if not insights_path.exists():
            continue
        try:
            rows = json.loads(insights_path.read_text())
        except json.JSONDecodeError:
            continue
        # Aggregate impressions per ad across this snapshot.
        agg: dict[str, int] = {}
        for r in rows:
            ad_id = r.get("ad_id")
            if not ad_id:
                continue
            agg[ad_id] = agg.get(ad_id, 0) + int(r.get("impressions") or 0)
        if not agg:
            continue
        winner = max(agg.items(), key=lambda kv: kv[1])
        if winner[1] > 0:
            print(f"  Picked ad {winner[0]} ({winner[1]} impressions in "
                  f"{d}'s ad_insights.json)")
            return winner[0]
    return None


def investigate_breakdowns(client: MetaClient, ad_id: str,
                           ic_action_type: str) -> None:
    print("=" * 70)
    print(f"Q1. Asset-level breakdown insights for ad {ad_id}")
    print("=" * 70)

    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=29)
    url = f"{client.base}/{ad_id}/insights"
    params = {
        "fields": ",".join(ASSET_INSIGHT_FIELDS),
        "breakdowns": ASSET_BREAKDOWNS,
        "time_range": json.dumps({"since": since.isoformat(),
                                  "until": until.isoformat()}),
        "level": "ad",
        "limit": 500,
    }
    print(f"GET {url}")
    print(f"  fields:     {','.join(ASSET_INSIGHT_FIELDS)}")
    print(f"  breakdowns: {ASSET_BREAKDOWNS}")
    print(f"  time_range: {since} → {until}")

    try:
        body = client._request(url, params)
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        print("  → Meta may not support these breakdowns for asset_feed_spec")
        print("    ads. Reassess pipeline architecture before proceeding.")
        return

    rows = body.get("data", []) or []
    print(f"\n  Returned {len(rows)} row(s).")
    if not rows:
        print("  → Either the ad had no impressions in the window or Meta")
        print("    declined the breakdown. If consistent across other ads,")
        print("    flag and reassess.")
        return

    # Are asset IDs actually populated? If every row has the same
    # asset_id values, breakdowns aren't really splitting.
    asset_field_population: Counter[str] = Counter()
    distinct_assets: dict[str, set[str]] = {
        "body_asset": set(),
        "title_asset": set(),
        "description_asset": set(),
        "image_asset": set(),
    }
    impressions_by_combo: dict[tuple, int] = {}
    conversions_by_combo: dict[tuple, int] = {}

    for row in rows:
        for key in ("body_asset", "title_asset",
                    "description_asset", "image_asset"):
            val = row.get(key)
            if val is not None and val != {}:
                asset_field_population[key] += 1
                # Asset breakdown returns a dict like
                # {"id": "1234", "text": "..."}.
                aid = (val.get("id") if isinstance(val, dict)
                       else str(val))
                if aid:
                    distinct_assets[key].add(aid)

        combo = tuple(
            (row.get(k) or {}).get("id") if isinstance(row.get(k), dict)
            else None
            for k in ("body_asset", "title_asset",
                      "description_asset", "image_asset")
        )
        impressions_by_combo[combo] = (
            impressions_by_combo.get(combo, 0)
            + int(row.get("impressions") or 0))

        # Extract IC conversions from actions[]
        actions = row.get("actions") or []
        ic = 0
        for a in actions:
            if a.get("action_type") == ic_action_type:
                try:
                    ic += int(float(a.get("value") or 0))
                except (TypeError, ValueError):
                    pass
        conversions_by_combo[combo] = (
            conversions_by_combo.get(combo, 0) + ic)

    print("\n  Asset-field population across rows:")
    for key in ("body_asset", "title_asset",
                "description_asset", "image_asset"):
        n = asset_field_population[key]
        distinct = len(distinct_assets[key])
        print(f"    {key:22s}  {n}/{len(rows)} rows populated, "
              f"{distinct} distinct asset id(s)")

    # Spread of delivery — are impressions concentrated in one combo
    # (Meta picked a winner and stopped exploring) or spread across many?
    combos_with_impressions = sum(1 for v in impressions_by_combo.values()
                                  if v > 0)
    combos_with_conversions = sum(1 for v in conversions_by_combo.values()
                                  if v > 0)
    print(f"\n  Distinct asset-combinations:        {len(impressions_by_combo)}")
    print(f"  Combos with > 0 impressions:        {combos_with_impressions}")
    print(f"  Combos with > 0 IC conversions:     {combos_with_conversions}")

    # Top 3 combos by impressions — sanity check that real asset IDs
    # are coming back.
    top = sorted(impressions_by_combo.items(),
                 key=lambda kv: kv[1], reverse=True)[:3]
    print("\n  Top 3 asset combinations by impressions:")
    for combo, imps in top:
        body_id, title_id, desc_id, img_id = combo
        ic = conversions_by_combo.get(combo, 0)
        print(f"    body={body_id} title={title_id} "
              f"desc={desc_id} image={img_id} → "
              f"{imps} imp, {ic} IC")

    print("\n  Sample raw row (first):")
    print(f"    {json.dumps(rows[0], indent=2, default=str)[:900]}")

    print("\n  → Verdict:")
    if (combos_with_impressions >= 3
            and any(len(s) >= 2 for s in distinct_assets.values())):
        print("    GREEN — breakdowns work. Multiple distinct asset IDs")
        print("    populated, multiple combos delivered. Per-variant CPICP")
        print("    is achievable.")
    elif combos_with_impressions == 1:
        print("    YELLOW — only 1 combo delivered. Meta may have")
        print("    converged on a winner. Test with a younger ad or one")
        print("    in learning phase before declaring this unworkable.")
    else:
        print("    RED — breakdowns are not splitting the data the way")
        print("    we need. Reassess the pipeline architecture.")


def investigate_image_resolution(client: MetaClient, account_id: str,
                                 ad_id: str) -> None:
    print()
    print("=" * 70)
    print(f"Q2. Image-hash resolution for creative attached to ad {ad_id}")
    print("=" * 70)

    # First, find the creative_id behind this ad.
    ad_url = f"{client.base}/{ad_id}"
    try:
        ad_row = client._request(ad_url, {"fields": "creative"})
    except Exception as exc:
        print(f"  Failed to fetch ad: {exc}")
        return
    creative_id = ((ad_row.get("creative") or {}).get("id"))
    if not creative_id:
        print("  Ad has no creative attached. Skipping.")
        return
    print(f"  Creative id: {creative_id}")

    creative_url = f"{client.base}/{creative_id}"
    try:
        creative = client._request(
            creative_url,
            {"fields": ",".join(EXPANDED_CREATIVE_FIELDS)},
        )
    except Exception as exc:
        print(f"  Failed to fetch creative: {exc}")
        return

    # Pull image hashes from asset_feed_spec.images[].
    afs = creative.get("asset_feed_spec") or {}
    images = afs.get("images") or []
    print(f"\n  asset_feed_spec.images[] count: {len(images)}")
    if images:
        print("  First image entry shape:")
        print(f"    keys: {sorted((images[0] or {}).keys())}")
        print(f"    sample: "
              f"{json.dumps(images[0], indent=2, default=str)[:400]}")

    hashes: list[str] = []
    for img in images:
        h = (img or {}).get("hash")
        if h:
            hashes.append(h)
    # Also include the top-level image_hash if populated.
    top_hash = creative.get("image_hash")
    if top_hash and top_hash not in hashes:
        hashes.append(top_hash)
    print(f"\n  Unique hashes to resolve: {len(hashes)} → {hashes[:5]}"
          f"{' (truncated)' if len(hashes) > 5 else ''}")

    if not hashes:
        print("  → No image hashes found. Visual analysis falls back to")
        print("    thumbnail_url (lower res but available).")
        return

    # Call /act_X/adimages?hashes=[...]
    adimages_url = f"{client.base}/act_{account_id.replace('act_', '')}/adimages"
    params = {
        "hashes": json.dumps(hashes[:10]),  # cap at 10 for the test
        "fields": "hash,url,permalink_url,width,height,name",
    }
    print(f"\n  GET {adimages_url}")
    print(f"    hashes: {hashes[:10]}")
    try:
        body = client._request(adimages_url, params)
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        print("  → Image-hash resolution failed. Visual analysis falls back")
        print("    to thumbnail_url.")
        return

    data = body.get("data", []) or []
    print(f"\n  Returned {len(data)} image record(s).")
    for rec in data[:3]:
        print(f"    hash={rec.get('hash')} "
              f"{rec.get('width')}x{rec.get('height')} "
              f"url={(rec.get('url') or '')[:80]}…")

    print("\n  → Verdict:")
    if data and any(r.get("url") for r in data):
        widths = [int(r.get("width") or 0) for r in data]
        max_w = max(widths) if widths else 0
        print(f"    GREEN — full-size URLs returned. Max width: {max_w}px.")
        print("    Use /adimages-resolved URLs for visual categorization.")
    else:
        print("    RED — endpoint returned nothing useful. Fall back to")
        print("    thumbnail_url.")


def main() -> int:
    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        sys.stderr.write(
            "ERROR: META_ACCESS_TOKEN not set. Export it before running.\n")
        return 2

    config = load_config()
    account_id = os.environ.get("META_AD_ACCOUNT_ID") or config["account"]["id"]
    api_version = config["account"]["meta_api_version"]
    ic_action_type = ic_action_type_from_config(config)

    client = MetaClient(account_id, api_version, token,
                        sleep_between_calls=0.4)

    print(f"Using account {account_id}, API {api_version}")
    print(f"IC action_type: {ic_action_type}\n")

    ad_id = find_ad_with_traffic()
    if not ad_id:
        sys.stderr.write(
            "ERROR: could not find an ad with recent traffic in "
            "data/snapshots/. Make sure daily-data has run at least once.\n")
        return 2

    investigate_breakdowns(client, ad_id, ic_action_type)
    investigate_image_resolution(client, account_id, ad_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
