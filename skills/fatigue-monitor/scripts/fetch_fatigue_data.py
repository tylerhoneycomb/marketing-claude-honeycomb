#!/usr/bin/env python3
"""Fetch the data the fatigue-monitor skill needs from Meta.

Pulls 14 days of ad-level insights with daily breakdown, plus current ad
objects (created_time, effective_status) and creative metadata
(thumbnail_url, body, title) for every ad in the insights result. Creative
metadata is cached in data/creatives/creatives.json — only ads not already
in the cache trigger an extra API call.

Output: single JSON payload to stdout with keys:
  {
    "fetched_at", "lookback_days", "since", "until", "timezone",
    "ads":         [...daily insight rows...],
    "ad_objects":  [...current state per ad...],
    "creatives":   [...metadata per ad/creative...],
  }

Environment:
  META_ACCESS_TOKEN     required
  META_AD_ACCOUNT_ID    optional override for benchmarks.json `account.id`

Flags:
  --lookback-days N     default 14
  --until YYYY-MM-DD    inclusive end of range; default = yesterday in account TZ
  --sleep N             min seconds between Meta API calls (default 1.0)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import (  # noqa: E402
    DEFAULT_SLEEP_BETWEEN_CALLS,
    INSIGHTS_FIELDS_AD,
    MetaClient,
    download_image,
    ic_action_type_from_config,
    load_config,
    normalize_ad,
    normalize_creative,
    normalize_insights_row,
)

CREATIVES_PATH = REPO_ROOT / "data" / "creatives" / "creatives.json"
IMAGES_DIR = REPO_ROOT / "data" / "creatives" / "images"


def yesterday_in_tz(tz: ZoneInfo) -> date:
    return (datetime.now(tz) - timedelta(days=1)).date()


def load_creatives_cache() -> dict[str, dict[str, Any]]:
    if not CREATIVES_PATH.exists():
        return {}
    try:
        with CREATIVES_PATH.open() as f:
            payload = json.load(f)
        return {c["creative_id"]: c for c in payload.get("creatives", [])
                if c.get("creative_id")}
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("creatives cache unreadable (%s) — treating as empty", exc)
        return {}


def update_creatives_cache(today: str, new_creatives: list[dict[str, Any]]) -> None:
    """Append new creatives to data/creatives/creatives.json (preserving
    first_seen_date for known IDs). Best-effort — failures don't abort fetch."""
    if not new_creatives:
        return
    existing: dict[str, dict[str, Any]] = {}
    if CREATIVES_PATH.exists():
        try:
            with CREATIVES_PATH.open() as f:
                payload = json.load(f)
            for c in payload.get("creatives", []):
                if c.get("creative_id"):
                    existing[c["creative_id"]] = c
        except (json.JSONDecodeError, OSError):
            existing = {}

    for c in new_creatives:
        cid = c.get("creative_id")
        if not cid:
            continue
        prior = existing.get(cid)
        if prior:
            existing[cid] = {**prior, **c,
                             "first_seen_date": prior.get("first_seen_date") or today,
                             "last_seen_date": today}
        else:
            existing[cid] = {**c, "first_seen_date": today, "last_seen_date": today}

    try:
        CREATIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CREATIVES_PATH.open("w") as f:
            json.dump({
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "count": len(existing),
                "creatives": sorted(existing.values(),
                                    key=lambda x: x.get("creative_id") or ""),
            }, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as exc:
        logging.warning("failed to update creatives cache: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch 14d insights + ad/creative metadata for fatigue-monitor."
    )
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--until", default=None,
                        help="Inclusive YYYY-MM-DD (default: yesterday in account TZ).")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_BETWEEN_CALLS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    account_id = os.environ.get("META_AD_ACCOUNT_ID") or config["account"]["id"]
    api_version = config["account"]["meta_api_version"]
    tz = ZoneInfo(config["account"]["timezone"])
    ic_action_type = ic_action_type_from_config(config)

    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        sys.stderr.write("ERROR: META_ACCESS_TOKEN is not set\n")
        return 2

    until_date = (datetime.strptime(args.until, "%Y-%m-%d").date()
                  if args.until else yesterday_in_tz(tz))
    since_date = until_date - timedelta(days=args.lookback_days - 1)
    since = since_date.isoformat()
    until = until_date.isoformat()

    client = MetaClient(account_id, api_version, token, args.sleep)

    logging.warning("fetching ad insights %s → %s (daily breakdown)", since, until)
    raw_ads = client.insights("ad", INSIGHTS_FIELDS_AD, since, until)
    ads_insights = [normalize_insights_row(r, ic_action_type) for r in raw_ads]

    active_filter = [{"field": "effective_status",
                      "operator": "IN", "value": ["ACTIVE", "PAUSED"]}]

    logging.warning("fetching ad objects (active+paused)")
    raw_ad_objects = client.ads(filtering=active_filter)
    ad_objects = [normalize_ad(r) for r in raw_ad_objects]

    # Creative metadata: only fetch for ad_ids present in insights AND not
    # already cached. Cache lives in data/creatives/creatives.json and is
    # accreted across runs.
    cache = load_creatives_cache()
    ad_ids_with_data = {r["ad_id"] for r in ads_insights if r.get("ad_id")}
    ad_to_creative: dict[str, str] = {}
    for ad in ad_objects:
        if ad.get("ad_id") in ad_ids_with_data and ad.get("creative_id"):
            ad_to_creative[ad["ad_id"]] = ad["creative_id"]

    new_creatives: list[dict[str, Any]] = []
    creatives_for_output: list[dict[str, Any]] = []
    seen_creative_ids: set[str] = set()
    for ad_id, creative_id in ad_to_creative.items():
        if creative_id in seen_creative_ids:
            continue
        seen_creative_ids.add(creative_id)
        if creative_id in cache:
            cached = cache[creative_id]
            # Backfill local image for previously-cached creatives that
            # don't have one yet (common after enabling local image
            # caching for the first time on a populated cache).
            if not cached.get("local_image_path"):
                local = download_image(
                    creative_id,
                    cached.get("image_url") or cached.get("thumbnail_url"),
                    IMAGES_DIR,
                )
                if local:
                    cached = {**cached,
                              "local_image_path": str(local.relative_to(REPO_ROOT))}
                    # Re-stage as a "new" creative so update_creatives_cache
                    # writes the new local_image_path back to disk.
                    new_creatives.append(cached)
            creatives_for_output.append({**cached, "ad_id": ad_id})
            continue
        try:
            raw = client.creative(creative_id)
            normalized = normalize_creative(raw)
            local = download_image(
                creative_id,
                normalized.get("image_url") or normalized.get("thumbnail_url"),
                IMAGES_DIR,
            )
            if local:
                normalized["local_image_path"] = str(local.relative_to(REPO_ROOT))
            new_creatives.append(normalized)
            creatives_for_output.append({**normalized, "ad_id": ad_id})
        except RuntimeError as exc:
            logging.warning("creative %s fetch failed: %s", creative_id, exc)

    update_creatives_cache(until, new_creatives)

    payload = {
        "fetched_at": datetime.now(tz).isoformat(),
        "lookback_days": args.lookback_days,
        "since": since,
        "until": until,
        "timezone": config["account"]["timezone"],
        "ads": ads_insights,
        "ad_objects": ad_objects,
        "creatives": creatives_for_output,
        "creatives_fetched_this_run": len(new_creatives),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
