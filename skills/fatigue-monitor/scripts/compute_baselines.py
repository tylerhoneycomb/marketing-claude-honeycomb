#!/usr/bin/env python3
"""Compute per-ad baseline CTR / CPC / CPM for the fatigue-monitor skill.

Reads fetch_fatigue_data.py output from stdin (or --input PATH). For each
active ad, picks one of three baseline strategies based on age:

  Path A — peak_window in current window:
    days 4–7 after the ad's created_time fall WITHIN the 14-day fetch
    range. Use those rows from data already in hand. No extra API call.

  Path B — peak_window via historical query:
    age > 14 days but ≤ 93 days. Days 4–7 fall before the current window.
    To avoid one API call per ad, we make ONE consolidated query covering
    the union of every Path-B ad's needed baseline window, with
    time_increment=1 and a filter restricting to just those ad_ids.

  Path C — estimated:
    no created_time, OR age > 93 days. Use the oldest 4 days of the
    current 14-day window as a proxy. Tagged baseline_type="estimated".

Outputs JSON to stdout: {"baselines": {ad_id: {ctr_baseline, cpc_baseline,
cpm_baseline, baseline_type, baseline_since, baseline_until,
created_time, days_active}}, "stats": {...}}.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import (  # noqa: E402
    DEFAULT_SLEEP_BETWEEN_CALLS,
    INSIGHTS_FIELDS_AD,
    MetaClient,
    ic_action_type_from_config,
    load_config,
    normalize_insights_row,
)

META_INSIGHTS_RETENTION_DAYS = 93


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def parse_created_time(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def safe_div(num: float, den: float) -> float | None:
    return (num / den) if den else None


def aggregate_window(rows: list[dict[str, Any]], window_start: date,
                     window_end: date) -> dict[str, float | None]:
    """Aggregate impressions/clicks/spend across rows whose date falls in
    [window_start, window_end] inclusive, then derive CTR/CPC/CPM."""
    impressions = 0
    clicks = 0
    spend = 0.0
    days = 0
    for r in rows:
        try:
            d = parse_date(r["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if d < window_start or d > window_end:
            continue
        impressions += int(r.get("impressions") or 0)
        clicks += int(r.get("clicks") or 0)
        spend += float(r.get("spend") or 0.0)
        days += 1
    if days == 0:
        return {"ctr": None, "cpc": None, "cpm": None,
                "impressions": 0, "clicks": 0, "spend": 0.0, "days": 0}
    ctr = safe_div(clicks, impressions)
    cpc = safe_div(spend, clicks)
    cpm = safe_div(spend, impressions)
    return {
        "ctr": round(ctr * 100, 4) if ctr is not None else None,
        "cpc": round(cpc, 4) if cpc is not None else None,
        "cpm": round(cpm * 1000, 4) if cpm is not None else None,
        "impressions": impressions,
        "clicks": clicks,
        "spend": round(spend, 2),
        "days": days,
    }


def classify_path(created: date | None, until: date,
                  current_since: date, current_until: date,
                  baseline_start_offset: int, baseline_end_offset: int
                  ) -> tuple[str, date | None, date | None]:
    """Return (path, baseline_since, baseline_until). path in {'A','B','C'}."""
    if created is None:
        return "C", None, None
    age = (until - created).days
    if age > META_INSIGHTS_RETENTION_DAYS:
        return "C", None, None

    bs = created + timedelta(days=baseline_start_offset)
    be = created + timedelta(days=baseline_end_offset)

    # If the baseline_end has not yet happened, clamp it to until — we'll
    # use however many days we have, but only as Path A.
    if be > until:
        be = until
    if bs > be:
        # Ad too new for any baseline window yet.
        return "C", None, None

    if bs >= current_since and be <= current_until:
        return "A", bs, be
    if age > 14:
        return "B", bs, be
    # Age <= 14 but baseline window straddles the start of current window.
    # Treat as Path A and slice from current data; the aggregate_window
    # filter will keep only the in-range days.
    return "A", max(bs, current_since), min(be, current_until)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute per-ad fatigue baselines.")
    parser.add_argument("--input", default=None,
                        help="Read fetch_fatigue_data.py payload from PATH (default: stdin).")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_BETWEEN_CALLS)
    parser.add_argument("--no-historical-query", action="store_true",
                        help="Skip the Path-B Meta query; degrade those ads to estimated.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.input:
        with open(args.input) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    config = load_config()
    fatigue_cfg = config["fatigue"]
    bs_offset = fatigue_cfg["baseline_window_start_day"]
    be_offset = fatigue_cfg["baseline_window_end_day"]

    current_since = parse_date(data["since"])
    current_until = parse_date(data["until"])

    ad_rows = data["ads"]
    ad_objects_by_id = {a["ad_id"]: a for a in data.get("ad_objects", [])
                         if a.get("ad_id")}
    rows_by_ad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in ad_rows:
        if r.get("ad_id"):
            rows_by_ad[r["ad_id"]].append(r)

    # Classify each ad with a created_time.
    classified: dict[str, dict[str, Any]] = {}
    for ad_id, ad_obj in ad_objects_by_id.items():
        created = parse_created_time(ad_obj.get("created_time"))
        path, bs, be = classify_path(
            created, current_until, current_since, current_until,
            bs_offset, be_offset,
        )
        days_active = (current_until - created).days if created else None
        classified[ad_id] = {
            "ad_id": ad_id,
            "path": path,
            "baseline_since": bs.isoformat() if bs else None,
            "baseline_until": be.isoformat() if be else None,
            "created_time": created.isoformat() if created else None,
            "days_active": days_active,
        }

    # Path A baselines: aggregate from current 14-day data per ad.
    for ad_id, info in classified.items():
        if info["path"] != "A":
            continue
        bs = parse_date(info["baseline_since"])
        be = parse_date(info["baseline_until"])
        agg = aggregate_window(rows_by_ad.get(ad_id, []), bs, be)
        info.update({
            "ctr_baseline": agg["ctr"],
            "cpc_baseline": agg["cpc"],
            "cpm_baseline": agg["cpm"],
            "baseline_type": "peak_window",
            "baseline_days_observed": agg["days"],
        })

    # Path B: ONE consolidated Meta query covering the union of needed
    # baseline windows, filtered to just the Path-B ad_ids.
    path_b_ids = [ad_id for ad_id, info in classified.items() if info["path"] == "B"]
    historical_query_count = 0
    if path_b_ids and not args.no_historical_query:
        token = os.environ.get("META_ACCESS_TOKEN")
        if not token:
            sys.stderr.write("ERROR: META_ACCESS_TOKEN required for Path B "
                             "(historical baselines). Use --no-historical-query "
                             "to fall back to estimated baselines.\n")
            return 2
        account_id = os.environ.get("META_AD_ACCOUNT_ID") or config["account"]["id"]
        api_version = config["account"]["meta_api_version"]
        ic_action_type = ic_action_type_from_config(config)

        starts = [parse_date(classified[a]["baseline_since"]) for a in path_b_ids]
        ends = [parse_date(classified[a]["baseline_until"]) for a in path_b_ids]
        union_since = min(starts).isoformat()
        union_until = max(ends).isoformat()

        logging.warning("path-B historical query: %s → %s, %d ad(s)",
                        union_since, union_until, len(path_b_ids))
        client = MetaClient(account_id, api_version, token, args.sleep)
        # Filter on ad.id IN [ids] — Meta supports up to ~50 IDs per filter,
        # so chunk if needed.
        historical_rows: list[dict[str, Any]] = []
        CHUNK = 50
        for i in range(0, len(path_b_ids), CHUNK):
            chunk = path_b_ids[i:i + CHUNK]
            filtering = [{"field": "ad.id", "operator": "IN", "value": chunk}]
            raw = client.insights("ad", INSIGHTS_FIELDS_AD,
                                   union_since, union_until,
                                   extra_params={"filtering": json.dumps(filtering)})
            historical_rows.extend(raw)
            historical_query_count += 1

        normalized = [normalize_insights_row(r, ic_action_type) for r in historical_rows]
        hist_by_ad: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in normalized:
            if r.get("ad_id"):
                hist_by_ad[r["ad_id"]].append(r)

        for ad_id in path_b_ids:
            info = classified[ad_id]
            bs = parse_date(info["baseline_since"])
            be = parse_date(info["baseline_until"])
            agg = aggregate_window(hist_by_ad.get(ad_id, []), bs, be)
            if agg["days"] > 0:
                info.update({
                    "ctr_baseline": agg["ctr"],
                    "cpc_baseline": agg["cpc"],
                    "cpm_baseline": agg["cpm"],
                    "baseline_type": "peak_window",
                    "baseline_days_observed": agg["days"],
                })
            else:
                # Fall through to estimated below.
                info["path"] = "C"
    elif path_b_ids:
        # --no-historical-query → demote Path-B ads to estimated.
        for ad_id in path_b_ids:
            classified[ad_id]["path"] = "C"

    # Path C: oldest-window proxy from current 14-day data.
    estimate_window_days = be_offset - bs_offset + 1  # default 4 days
    proxy_start = current_since
    proxy_end = current_since + timedelta(days=estimate_window_days - 1)
    if proxy_end > current_until:
        proxy_end = current_until
    for ad_id, info in classified.items():
        if info["path"] != "C":
            continue
        agg = aggregate_window(rows_by_ad.get(ad_id, []), proxy_start, proxy_end)
        info.update({
            "ctr_baseline": agg["ctr"],
            "cpc_baseline": agg["cpc"],
            "cpm_baseline": agg["cpm"],
            "baseline_type": "estimated",
            "baseline_since": proxy_start.isoformat(),
            "baseline_until": proxy_end.isoformat(),
            "baseline_days_observed": agg["days"],
        })

    # Stats
    counts = defaultdict(int)
    for info in classified.values():
        counts[info.get("baseline_type") or "unknown"] += 1
    payload = {
        "computed_at": datetime.now().isoformat(),
        "current_since": current_since.isoformat(),
        "current_until": current_until.isoformat(),
        "stats": {
            "ads_classified": len(classified),
            "by_baseline_type": dict(counts),
            "path_a_count": sum(1 for i in classified.values()
                                if i["path"] == "A"),
            "path_b_count": sum(1 for i in classified.values()
                                if i["path"] == "B"),
            "path_c_count": sum(1 for i in classified.values()
                                if i["path"] == "C"),
            "historical_api_calls": historical_query_count,
        },
        "baselines": classified,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
