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


def try_breakdown(client: MetaClient, ad_id: str, label: str,
                  breakdowns: str | None, include_actions: bool,
                  ic_action_type: str) -> dict[str, Any]:
    """Run one breakdown configuration and return a summary dict.

    Returns:
        {ok: bool, error: str | None, rows: int, distinct_assets: int,
         imp_combos: int, ic_combos: int}
    """
    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=29)
    fields = ["ad_id", "ad_name", "impressions", "clicks", "spend"]
    if include_actions:
        fields.append("actions")

    url = f"{client.base}/{ad_id}/insights"
    params: dict[str, Any] = {
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since.isoformat(),
                                  "until": until.isoformat()}),
        "level": "ad",
        "limit": 500,
    }
    if breakdowns:
        params["breakdowns"] = breakdowns

    try:
        body = client._request(url, params)
    except Exception as exc:
        msg = str(exc)
        return {"ok": False, "error": msg[:300], "rows": 0,
                "distinct_assets": 0, "imp_combos": 0, "ic_combos": 0}

    rows = body.get("data") or []
    if not rows:
        return {"ok": True, "error": None, "rows": 0,
                "distinct_assets": 0, "imp_combos": 0, "ic_combos": 0}

    # Pull distinct asset IDs from whatever breakdown was requested.
    breakdown_keys = (breakdowns or "").split(",") if breakdowns else []
    distinct: set[str] = set()
    for r in rows:
        for k in breakdown_keys:
            v = r.get(k)
            if isinstance(v, dict) and v.get("id"):
                distinct.add(f"{k}:{v['id']}")
            elif isinstance(v, str):
                distinct.add(f"{k}:{v}")

    imp_combos = sum(1 for r in rows if int(r.get("impressions") or 0) > 0)

    ic_combos = 0
    if include_actions:
        for r in rows:
            for a in (r.get("actions") or []):
                if a.get("action_type") == ic_action_type:
                    try:
                        if int(float(a.get("value") or 0)) > 0:
                            ic_combos += 1
                            break
                    except (TypeError, ValueError):
                        pass

    return {"ok": True, "error": None, "rows": len(rows),
            "distinct_assets": len(distinct),
            "imp_combos": imp_combos, "ic_combos": ic_combos,
            "sample": rows[0] if rows else None}


def investigate_breakdowns(client: MetaClient, ad_id: str,
                           ic_action_type: str) -> None:
    print("=" * 70)
    print(f"Q1. Asset-level breakdown insights for ad {ad_id}")
    print("=" * 70)
    print("Round-3 strategy: previous attempt with all 4 asset breakdowns")
    print("at once failed because the implicit action_type breakdown")
    print("(added when you query `actions`) collides with multiple asset")
    print("breakdowns simultaneously. This run probes which configurations")
    print("Meta accepts.\n")

    configs: list[tuple[str, str | None, bool]] = [
        # (label, breakdowns, include_actions)
        ("baseline (no breakdown, with actions)", None, True),
        ("body_asset only, with actions", "body_asset", True),
        ("title_asset only, with actions", "title_asset", True),
        ("description_asset only, with actions", "description_asset", True),
        ("image_asset only, with actions", "image_asset", True),
        ("body_asset + title_asset, with actions", "body_asset,title_asset", True),
        ("body_asset + image_asset, with actions",
         "body_asset,image_asset", True),
        ("all 4 asset breakdowns, NO actions",
         "body_asset,title_asset,description_asset,image_asset", False),
    ]

    results: list[tuple[str, dict[str, Any]]] = []
    for label, bds, with_actions in configs:
        print(f"  Testing: {label}")
        res = try_breakdown(client, ad_id, label, bds, with_actions,
                            ic_action_type)
        results.append((label, res))
        if res["ok"]:
            print(f"    OK — rows={res['rows']} "
                  f"distinct_assets={res['distinct_assets']} "
                  f"imp_combos={res['imp_combos']} "
                  f"ic_combos={res['ic_combos']}")
        else:
            print(f"    FAILED — {res['error']}")
        print()

    # Find the most useful working config: ideally one that gives us
    # rows broken down by asset AND includes IC conversions.
    print("=" * 70)
    print("Summary table:")
    print("=" * 70)
    print(f"  {'config':50s} {'ok':4s} {'rows':6s} {'distinct':10s} "
          f"{'with_imp':10s} {'with_ic':8s}")
    for label, r in results:
        ok = "Y" if r["ok"] else "N"
        print(f"  {label[:50]:50s} {ok:4s} {r['rows']:6d} "
              f"{r['distinct_assets']:10d} "
              f"{r['imp_combos']:10d} {r['ic_combos']:8d}")

    # Sample row from the most informative working call
    informative = [r for label, r in results
                   if r["ok"] and r["rows"] > 0 and r["distinct_assets"] > 1]
    if informative:
        best = max(informative, key=lambda r: r["distinct_assets"])
        print("\n  Sample row from the call with most distinct assets:")
        print(f"    {json.dumps(best.get('sample'), indent=2, default=str)[:900]}")

    print("\n  → Verdict:")
    body_only = next((r for label, r in results
                      if "body_asset only" in label), None)
    if body_only and body_only["ok"] and body_only["distinct_assets"] >= 2:
        print(f"    GREEN — single-dimension breakdowns work. Use 4 separate")
        print(f"    calls per ad (body, title, description, image) for")
        print(f"    per-dimension marginal CPICP. body_asset alone returned")
        print(f"    {body_only['distinct_assets']} distinct asset(s) across")
        print(f"    {body_only['rows']} row(s).")
    elif body_only and body_only["ok"] and body_only["distinct_assets"] < 2:
        print("    YELLOW — call succeeded but only 1 distinct body asset")
        print("    delivered. Either Meta converged on a single body, or")
        print("    the breakdown isn't actually splitting. Test on an")
        print("    earlier-stage ad before declaring this unworkable.")
    else:
        print("    RED — single-dimension breakdowns also fail. Architecture")
        print("    must be reassessed. Likely fall back to ad-level")
        print("    attribution + qualitative variant analysis.")


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
