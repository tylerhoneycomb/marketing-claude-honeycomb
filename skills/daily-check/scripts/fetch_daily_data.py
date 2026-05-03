#!/usr/bin/env python3
"""Fetch the data the daily-check skill needs from Meta.

Pulls 7 days (default) of insights at three levels — campaign, ad set, ad —
plus current ad-set objects (`learning_stage_info`, `daily_budget`, etc.) and
ad objects (`created_time`, `effective_status`). Emits a single JSON payload
to stdout for `analyze_daily.py` to consume.

Environment:
  META_ACCESS_TOKEN     required
  META_AD_ACCOUNT_ID    optional override for benchmarks.json `account.id`

Flags:
  --lookback-days N     default 7
  --until YYYY-MM-DD    inclusive end of range; default = yesterday in account TZ
  --sleep N             min seconds between Meta API calls (default 1.0)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import (  # noqa: E402
    AD_OBJECT_FIELDS,
    DEFAULT_SLEEP_BETWEEN_CALLS,
    INSIGHTS_FIELDS_AD,
    INSIGHTS_FIELDS_ADSET,
    INSIGHTS_FIELDS_CAMPAIGN,
    MetaClient,
    ic_action_type_from_config,
    load_config,
    normalize_ad,
    normalize_adset,
    normalize_insights_row,
)


def yesterday_in_tz(tz: ZoneInfo) -> date:
    return (datetime.now(tz) - timedelta(days=1)).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch 7d insights + objects for the daily-check skill."
    )
    parser.add_argument("--lookback-days", type=int, default=7,
                        help="Days back from --until (default 7).")
    parser.add_argument("--until", default=None,
                        help="Inclusive end of range, YYYY-MM-DD "
                             "(default: yesterday in account timezone).")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_BETWEEN_CALLS,
                        help=f"Min seconds between Meta calls (default {DEFAULT_SLEEP_BETWEEN_CALLS}).")
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

    logging.warning("fetching campaign insights %s → %s", since, until)
    raw_campaigns = client.insights("campaign", INSIGHTS_FIELDS_CAMPAIGN, since, until)
    campaigns = [normalize_insights_row(r, ic_action_type) for r in raw_campaigns]

    logging.warning("fetching adset insights %s → %s", since, until)
    raw_adsets = client.insights("adset", INSIGHTS_FIELDS_ADSET, since, until)
    adsets_insights = [normalize_insights_row(r, ic_action_type) for r in raw_adsets]

    logging.warning("fetching ad insights %s → %s", since, until)
    raw_ads = client.insights("ad", INSIGHTS_FIELDS_AD, since, until)
    ads_insights = [normalize_insights_row(r, ic_action_type) for r in raw_ads]

    # Filter on active/paused for object queries (per CLAUDE.md convention).
    active_filter = [{"field": "effective_status",
                      "operator": "IN", "value": ["ACTIVE", "PAUSED"]}]

    logging.warning("fetching adset objects (active+paused)")
    raw_adset_objects = client.adsets(filtering=active_filter)
    adset_objects = [normalize_adset(r) for r in raw_adset_objects]

    logging.warning("fetching ad objects (active+paused)")
    raw_ad_objects = client.ads(filtering=active_filter)
    ad_objects = [normalize_ad(r) for r in raw_ad_objects]

    payload = {
        "fetched_at": datetime.now(tz).isoformat(),
        "lookback_days": args.lookback_days,
        "since": since,
        "until": until,
        "timezone": config["account"]["timezone"],
        "campaigns": campaigns,
        "adsets": adsets_insights,
        "ads": ads_insights,
        "adset_objects": adset_objects,
        "ad_objects": ad_objects,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
