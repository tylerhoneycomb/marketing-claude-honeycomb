#!/usr/bin/env python3
"""Daily Meta ad-set + ad data pull for the Honeycomb agent loop.

Writes JSON snapshots to data/snapshots/<YYYY-MM-DD>/ and merges newly
discovered creatives into data/creatives/creatives.json.

The campaign-level pipeline in apps-script/Code.js continues to run
independently and remains the source of truth for the dashboard. This
script produces the ad-set + ad granularity the agent needs for fatigue
detection and creative performance tracking.

Modes:
    Single day (default):    --date YYYY-MM-DD          (or yesterday UTC)
    Date range backfill:     --start YYYY-MM-DD --end YYYY-MM-DD

Range mode is idempotent: dates whose snapshot directory already
contains `_manifest.json` are skipped. Adsets / ads / creatives metadata
is fetched once (against the most recent date in the range) and written
into that date's directory only — not duplicated 122 times.

Environment:
    META_ACCESS_TOKEN     required unless --dry-run
    META_AD_ACCOUNT_ID    optional override for benchmarks.json `account.id`
    SNAPSHOT_DATE         optional override (YYYY-MM-DD) for single-day mode
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "data" / "config" / "benchmarks.json"
SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"
CREATIVES_PATH = REPO_ROOT / "data" / "creatives" / "creatives.json"

INSIGHTS_FIELDS_ADSET = [
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "impressions",
    "clicks",
    "spend",
    "reach",
    "frequency",
    "ctr",
    "cpc",
    "cpm",
    "actions",
]

INSIGHTS_FIELDS_AD = [
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "impressions",
    "clicks",
    "spend",
    "reach",
    "frequency",
    "ctr",
    "cpc",
    "cpm",
    "actions",
]

ADSET_OBJECT_FIELDS = [
    "id",
    "name",
    "campaign_id",
    "daily_budget",
    "lifetime_budget",
    "optimization_goal",
    "effective_status",
    "learning_stage_info",
    "issues_info",
]

AD_OBJECT_FIELDS = [
    "id",
    "name",
    "adset_id",
    "campaign_id",
    "effective_status",
    "creative",
]

CREATIVE_FIELDS = [
    "id",
    "name",
    "thumbnail_url",
    "image_hash",
    "object_story_spec",
    "effective_object_story_id",
    "title",
    "body",
    "call_to_action_type",
    "link_url",
]

# Meta API error codes that indicate throttling / transient failure.
# See https://developers.facebook.com/docs/graph-api/guides/error-handling
META_THROTTLE_ERROR_CODES = {
    1,        # API unknown / transient
    2,        # API service / temporary
    4,        # Application request limit reached
    17,       # User request limit reached
    32,       # Page-level throttling
    341,      # Application limit reached (variant)
    613,      # Custom-level throttling
    80000,    # Async insights rate limit
    80004,    # Insights call rate limit
}
DEFAULT_SLEEP_BETWEEN_CALLS = 1.0
MAX_RETRIES = 6


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


class MetaClient:
    """Thin wrapper around Meta Graph API with paging + retries."""

    def __init__(self, account_id: str, api_version: str, token: str,
                 sleep_between_calls: float = DEFAULT_SLEEP_BETWEEN_CALLS):
        self.account_id = account_id
        self.api_version = api_version
        self.token = token
        self.sleep_between_calls = sleep_between_calls
        self.base = f"https://graph.facebook.com/{api_version}"
        self._last_call_at: float = 0.0

    def _throttle(self) -> None:
        """Sleep so consecutive calls are at least sleep_between_calls apart."""
        if self.sleep_between_calls <= 0:
            return
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.sleep_between_calls and self._last_call_at > 0:
            time.sleep(self.sleep_between_calls - elapsed)
        self._last_call_at = time.monotonic()

    def _request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._throttle()
        params = dict(params or {})
        params["access_token"] = self.token
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            backoff = min(2 ** attempt, 60)
            try:
                resp = requests.get(url, params=params, timeout=60)
            except requests.RequestException as exc:
                last_err = exc
                logging.warning("network error %s (attempt %d/%d) — sleeping %ds",
                                exc, attempt + 1, MAX_RETRIES, backoff)
                time.sleep(backoff)
                continue

            # Try to parse JSON regardless of status — Meta sometimes returns
            # error envelopes with HTTP 400 carrying a retryable error.code.
            try:
                body = resp.json()
            except ValueError:
                body = None

            err = (body or {}).get("error") if isinstance(body, dict) else None
            err_code = err.get("code") if isinstance(err, dict) else None

            if resp.status_code == 200 and not err:
                return body  # type: ignore[return-value]

            if resp.status_code == 429 or err_code in META_THROTTLE_ERROR_CODES:
                logging.warning(
                    "throttle: HTTP %d code=%s on %s (attempt %d/%d) — sleeping %ds",
                    resp.status_code, err_code, url, attempt + 1, MAX_RETRIES, backoff,
                )
                time.sleep(backoff)
                continue

            if resp.status_code in (500, 502, 503, 504):
                logging.warning(
                    "transient %d on %s (attempt %d/%d) — sleeping %ds: %s",
                    resp.status_code, url, attempt + 1, MAX_RETRIES, backoff,
                    resp.text[:200],
                )
                time.sleep(backoff)
                continue

            # Hard failure — surface the error envelope verbatim.
            raise RuntimeError(
                f"Meta API error HTTP {resp.status_code} code={err_code} "
                f"on {url}: {resp.text[:500]}"
            )
        raise RuntimeError(
            f"Meta API request failed after {MAX_RETRIES} attempts: {last_err}"
        )

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params: dict[str, Any] | None = params
        while next_url:
            page = self._request(next_url, next_params)
            rows.extend(page.get("data", []))
            paging = page.get("paging") or {}
            next_url = paging.get("next")
            next_params = None  # `next` already encodes everything
        return rows

    def insights(self, level: str, fields: list[str], date: str) -> list[dict[str, Any]]:
        time_range = json.dumps({"since": date, "until": date})
        params = {
            "fields": ",".join(fields),
            "level": level,
            "time_range": time_range,
            "time_increment": 1,
            "limit": 200,
        }
        url = f"{self.base}/{self.account_id}/insights"
        return self._paginate(url, params)

    def adsets(self) -> list[dict[str, Any]]:
        params = {
            "fields": ",".join(ADSET_OBJECT_FIELDS),
            "limit": 200,
        }
        url = f"{self.base}/{self.account_id}/adsets"
        return self._paginate(url, params)

    def ads(self) -> list[dict[str, Any]]:
        params = {
            "fields": ",".join(AD_OBJECT_FIELDS),
            "limit": 200,
        }
        url = f"{self.base}/{self.account_id}/ads"
        return self._paginate(url, params)

    def creative(self, creative_id: str) -> dict[str, Any]:
        params = {"fields": ",".join(CREATIVE_FIELDS)}
        url = f"{self.base}/{creative_id}"
        return self._request(url, params)


def extract_conversions(actions: list[dict[str, Any]] | None, ic_action_type: str,
                        lead_action_types: list[str]) -> tuple[int, int]:
    """Return (conversions, ic_conversions) from a Meta `actions` array.

    Mirrors collectMetaRows_ in apps-script/Code.js so daily ad-level totals
    reconcile with the campaign-level rollup.
    """
    if not actions:
        return 0, 0
    conversions = 0
    ic_conversions = 0
    for a in actions:
        atype = a.get("action_type")
        try:
            value = int(float(a.get("value", 0)))
        except (TypeError, ValueError):
            value = 0
        if atype in lead_action_types and conversions == 0:
            conversions = value  # match Apps Script behavior: first matching wins
        if atype == ic_action_type:
            ic_conversions += value
    return conversions, ic_conversions


def normalize_insights_row(row: dict[str, Any], date: str, ic_action_type: str,
                           lead_action_types: list[str]) -> dict[str, Any]:
    conversions, ic_conversions = extract_conversions(
        row.get("actions"), ic_action_type, lead_action_types
    )
    return {
        "date": date,
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
        "adset_id": row.get("adset_id"),
        "adset_name": row.get("adset_name"),
        "ad_id": row.get("ad_id"),
        "ad_name": row.get("ad_name"),
        "impressions": int(row.get("impressions") or 0),
        "clicks": int(row.get("clicks") or 0),
        "spend": float(row.get("spend") or 0.0),
        "reach": int(row.get("reach") or 0),
        "frequency": float(row.get("frequency") or 0.0),
        "ctr": float(row.get("ctr") or 0.0),
        "cpc": float(row.get("cpc") or 0.0),
        "cpm": float(row.get("cpm") or 0.0),
        "conversions": conversions,
        "ic_conversions": ic_conversions,
    }


def normalize_adset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "adset_id": row.get("id"),
        "adset_name": row.get("name"),
        "campaign_id": row.get("campaign_id"),
        "daily_budget_cents": int(row["daily_budget"]) if row.get("daily_budget") else None,
        "lifetime_budget_cents": int(row["lifetime_budget"]) if row.get("lifetime_budget") else None,
        "optimization_goal": row.get("optimization_goal"),
        "effective_status": row.get("effective_status"),
        "learning_stage_info": row.get("learning_stage_info"),
        "issues_info": row.get("issues_info"),
    }


def normalize_ad(row: dict[str, Any]) -> dict[str, Any]:
    creative = row.get("creative") or {}
    return {
        "ad_id": row.get("id"),
        "ad_name": row.get("name"),
        "adset_id": row.get("adset_id"),
        "campaign_id": row.get("campaign_id"),
        "effective_status": row.get("effective_status"),
        "creative_id": creative.get("id"),
    }


def normalize_creative(row: dict[str, Any]) -> dict[str, Any]:
    story = row.get("object_story_spec") or {}
    link_data = story.get("link_data") or {}
    cta = link_data.get("call_to_action") or {}
    return {
        "creative_id": row.get("id"),
        "name": row.get("name"),
        "thumbnail_url": row.get("thumbnail_url"),
        "image_hash": row.get("image_hash") or link_data.get("image_hash"),
        "title": row.get("title") or link_data.get("name"),
        "body": row.get("body") or link_data.get("message"),
        "link_url": row.get("link_url") or link_data.get("link"),
        "call_to_action_type": row.get("call_to_action_type") or cta.get("type"),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def merge_creatives(today: str, new_creatives: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge creative metadata, preserving first_seen_date for known IDs."""
    existing: dict[str, dict[str, Any]] = {}
    if CREATIVES_PATH.exists():
        try:
            with CREATIVES_PATH.open() as f:
                payload = json.load(f)
            for c in payload.get("creatives", []):
                if c.get("creative_id"):
                    existing[c["creative_id"]] = c
        except (json.JSONDecodeError, OSError) as exc:
            logging.warning("could not read creatives.json (%s); rebuilding", exc)
            existing = {}

    for c in new_creatives:
        cid = c.get("creative_id")
        if not cid:
            continue
        prior = existing.get(cid)
        if prior:
            first_seen = prior.get("first_seen_date") or today
            existing[cid] = {**prior, **c, "first_seen_date": first_seen,
                             "last_seen_date": today}
        else:
            existing[cid] = {**c, "first_seen_date": today, "last_seen_date": today}

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(existing),
        "creatives": sorted(existing.values(), key=lambda x: x.get("creative_id") or ""),
    }


def write_manifest(out_dir: Path, date: str, counts: dict[str, int]) -> None:
    manifest = {
        "snapshot_date": date,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "files": [
            "campaigns.json",
            "adsets.json",
            "ads.json",
            "ad_insights.json",
            "adset_insights.json",
        ],
    }
    write_json(out_dir / "_manifest.json", manifest)


def resolve_account_id(config: dict[str, Any]) -> str:
    """Env var takes precedence over benchmarks.json."""
    return os.environ.get("META_AD_ACCOUNT_ID") or config["account"]["id"]


def enumerate_dates(start: str, end: str) -> list[str]:
    """Return YYYY-MM-DD strings from start through end inclusive."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    if end_dt < start_dt:
        raise ValueError(f"end ({end}) is before start ({start})")
    out: list[str] = []
    cur = start_dt
    while cur <= end_dt:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def has_snapshot(date: str) -> bool:
    return (SNAPSHOTS_DIR / date / "_manifest.json").exists()


def fetch_insights_for_day(client: "MetaClient", date: str, ic_action_type: str,
                           lead_action_types: list[str]) -> tuple[list[dict[str, Any]],
                                                                   list[dict[str, Any]]]:
    raw_adset_insights = client.insights("adset", INSIGHTS_FIELDS_ADSET, date)
    adset_insights = [
        normalize_insights_row(r, date, ic_action_type, lead_action_types)
        for r in raw_adset_insights
    ]
    raw_ad_insights = client.insights("ad", INSIGHTS_FIELDS_AD, date)
    ad_insights = [
        normalize_insights_row(r, date, ic_action_type, lead_action_types)
        for r in raw_ad_insights
    ]
    return adset_insights, ad_insights


def write_day_snapshot(date: str, adset_insights: list[dict[str, Any]],
                        ad_insights: list[dict[str, Any]],
                        adsets: list[dict[str, Any]] | None = None,
                        ads: list[dict[str, Any]] | None = None,
                        new_creatives_count: int = 0) -> None:
    out_dir = SNAPSHOTS_DIR / date
    write_json(out_dir / "adset_insights.json", adset_insights)
    write_json(out_dir / "ad_insights.json", ad_insights)

    campaigns: dict[str, dict[str, Any]] = {}
    for r in adset_insights + ad_insights:
        cid = r.get("campaign_id")
        if cid and cid not in campaigns:
            campaigns[cid] = {
                "campaign_id": cid,
                "campaign_name": r.get("campaign_name"),
            }
    write_json(out_dir / "campaigns.json",
               sorted(campaigns.values(), key=lambda x: x["campaign_id"]))

    counts = {
        "campaigns": len(campaigns),
        "adset_insights": len(adset_insights),
        "ad_insights": len(ad_insights),
    }
    if adsets is not None:
        write_json(out_dir / "adsets.json", adsets)
        counts["adsets"] = len(adsets)
    if ads is not None:
        write_json(out_dir / "ads.json", ads)
        counts["ads"] = len(ads)
    counts["new_creatives"] = new_creatives_count

    write_manifest(out_dir, date, counts)


def run(date: str, dry_run: bool = False,
        sleep_between_calls: float = DEFAULT_SLEEP_BETWEEN_CALLS) -> int:
    config = load_config()
    account_id = resolve_account_id(config)
    api_version = config["account"]["api_version"]
    ic_action_type = config["ic_tracking"]["action_type"]
    lead_action_types = config["ic_tracking"]["lead_action_types"]

    out_dir = SNAPSHOTS_DIR / date

    if dry_run:
        logging.info("dry-run: would fetch %s for %s into %s",
                     account_id, date, out_dir)
        return 0

    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        logging.error("META_ACCESS_TOKEN is not set")
        return 2

    client = MetaClient(account_id, api_version, token, sleep_between_calls)

    logging.info("fetching adsets metadata")
    raw_adsets = client.adsets()
    adsets = [normalize_adset(r) for r in raw_adsets]

    logging.info("fetching ads metadata")
    raw_ads = client.ads()
    ads = [normalize_ad(r) for r in raw_ads]

    logging.info("fetching adset-level insights for %s", date)
    raw_adset_insights = client.insights("adset", INSIGHTS_FIELDS_ADSET, date)
    adset_insights = [
        normalize_insights_row(r, date, ic_action_type, lead_action_types)
        for r in raw_adset_insights
    ]

    logging.info("fetching ad-level insights for %s", date)
    raw_ad_insights = client.insights("ad", INSIGHTS_FIELDS_AD, date)
    ad_insights = [
        normalize_insights_row(r, date, ic_action_type, lead_action_types)
        for r in raw_ad_insights
    ]

    # Build campaigns.json from the union of campaign_ids referenced today.
    campaigns: dict[str, dict[str, Any]] = {}
    for r in adset_insights + ad_insights:
        cid = r.get("campaign_id")
        if cid and cid not in campaigns:
            campaigns[cid] = {
                "campaign_id": cid,
                "campaign_name": r.get("campaign_name"),
            }
    campaigns_list = sorted(campaigns.values(), key=lambda x: x["campaign_id"])

    # Pull creative metadata for any new creative_ids we haven't seen before.
    known_ids: set[str] = set()
    if CREATIVES_PATH.exists():
        try:
            with CREATIVES_PATH.open() as f:
                payload = json.load(f)
            known_ids = {c["creative_id"] for c in payload.get("creatives", [])
                         if c.get("creative_id")}
        except (json.JSONDecodeError, OSError):
            known_ids = set()

    new_creatives: list[dict[str, Any]] = []
    for ad in ads:
        cid = ad.get("creative_id")
        if not cid or cid in known_ids:
            continue
        try:
            raw = client.creative(cid)
            new_creatives.append(normalize_creative(raw))
        except RuntimeError as exc:
            logging.warning("creative %s fetch failed: %s", cid, exc)

    logging.info("writing snapshots to %s", out_dir)
    write_json(out_dir / "campaigns.json", campaigns_list)
    write_json(out_dir / "adsets.json", adsets)
    write_json(out_dir / "ads.json", ads)
    write_json(out_dir / "adset_insights.json", adset_insights)
    write_json(out_dir / "ad_insights.json", ad_insights)

    if new_creatives:
        logging.info("merging %d new creative(s) into creatives.json", len(new_creatives))
        merged = merge_creatives(date, new_creatives)
        write_json(CREATIVES_PATH, merged)
    elif not CREATIVES_PATH.exists():
        write_json(CREATIVES_PATH, {"updated_at": datetime.now(timezone.utc).isoformat(),
                                     "count": 0, "creatives": []})

    write_manifest(out_dir, date, {
        "campaigns": len(campaigns_list),
        "adsets": len(adsets),
        "ads": len(ads),
        "adset_insights": len(adset_insights),
        "ad_insights": len(ad_insights),
        "new_creatives": len(new_creatives),
    })
    logging.info("snapshot complete: %d campaigns / %d adsets / %d ads / %d ad rows",
                 len(campaigns_list), len(adsets), len(ads), len(ad_insights))
    return 0


def run_range(start: str, end: str, dry_run: bool = False,
              sleep_between_calls: float = DEFAULT_SLEEP_BETWEEN_CALLS) -> int:
    """Backfill a date range. Idempotent — skips dates that already have a manifest."""
    config = load_config()
    account_id = resolve_account_id(config)
    api_version = config["account"]["api_version"]
    ic_action_type = config["ic_tracking"]["action_type"]
    lead_action_types = config["ic_tracking"]["lead_action_types"]

    all_dates = enumerate_dates(start, end)
    pending = [d for d in all_dates if not has_snapshot(d)]
    skipped = [d for d in all_dates if has_snapshot(d)]
    logging.info("range: %s → %s (%d days), pending=%d, already-snapshot=%d",
                 start, end, len(all_dates), len(pending), len(skipped))

    if dry_run:
        logging.info("dry-run: would backfill %d date(s) from %s through %s",
                     len(pending), start, end)
        return 0

    if not pending:
        logging.info("no pending dates — all snapshots already exist")
        return 0

    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        logging.error("META_ACCESS_TOKEN is not set")
        return 2

    client = MetaClient(account_id, api_version, token, sleep_between_calls)

    # Fetch object-graph metadata + creatives ONCE for the most recent pending
    # date. Historical dirs intentionally lack adsets/ads — those reflect
    # current state, not historical state, so duplicating them per day would
    # be misleading. compute_signals.py reads the most recent metadata.
    latest_date = pending[-1]
    logging.info("fetching adsets metadata (will write to %s only)", latest_date)
    raw_adsets = client.adsets()
    adsets_norm = [normalize_adset(r) for r in raw_adsets]
    logging.info("fetching ads metadata (will write to %s only)", latest_date)
    raw_ads = client.ads()
    ads_norm = [normalize_ad(r) for r in raw_ads]

    # Discover new creatives once.
    known_ids: set[str] = set()
    if CREATIVES_PATH.exists():
        try:
            with CREATIVES_PATH.open() as f:
                payload = json.load(f)
            known_ids = {c["creative_id"] for c in payload.get("creatives", [])
                         if c.get("creative_id")}
        except (json.JSONDecodeError, OSError):
            known_ids = set()

    new_creatives: list[dict[str, Any]] = []
    for ad in ads_norm:
        cid = ad.get("creative_id")
        if not cid or cid in known_ids:
            continue
        try:
            raw = client.creative(cid)
            new_creatives.append(normalize_creative(raw))
            known_ids.add(cid)
        except RuntimeError as exc:
            logging.warning("creative %s fetch failed: %s", cid, exc)

    if new_creatives:
        merged = merge_creatives(latest_date, new_creatives)
        write_json(CREATIVES_PATH, merged)
        logging.info("merged %d new creative(s) into creatives.json",
                     len(new_creatives))
    elif not CREATIVES_PATH.exists():
        write_json(CREATIVES_PATH, {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": 0, "creatives": [],
        })

    # Per-day insights loop.
    success_count = 0
    failed_dates: list[str] = []
    for i, date in enumerate(pending, 1):
        logging.info("[%d/%d] fetching insights for %s", i, len(pending), date)
        try:
            adset_insights, ad_insights = fetch_insights_for_day(
                client, date, ic_action_type, lead_action_types
            )
            attach_meta = (date == latest_date)
            write_day_snapshot(
                date, adset_insights, ad_insights,
                adsets=adsets_norm if attach_meta else None,
                ads=ads_norm if attach_meta else None,
                new_creatives_count=len(new_creatives) if attach_meta else 0,
            )
            success_count += 1
            logging.info("[%d/%d] %s wrote %d adset rows / %d ad rows",
                         i, len(pending), date, len(adset_insights), len(ad_insights))
        except RuntimeError as exc:
            logging.error("[%d/%d] %s FAILED: %s", i, len(pending), date, exc)
            failed_dates.append(date)
            # Continue — don't abort the whole backfill on a single bad day.

    logging.info("backfill complete: %d/%d days succeeded, %d failed, %d skipped (preexisting)",
                 success_count, len(pending), len(failed_dates), len(skipped))
    if failed_dates:
        logging.warning("failed dates: %s", ", ".join(failed_dates))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Meta ad-level data for the daily snapshot or a backfill range.",
    )
    parser.add_argument("--date", default=None,
                        help="Single-day snapshot date YYYY-MM-DD "
                             "(default: SNAPSHOT_DATE env or yesterday UTC).")
    parser.add_argument("--start", default=None,
                        help="Backfill range start (YYYY-MM-DD, inclusive). "
                             "Requires --end.")
    parser.add_argument("--end", default=None,
                        help="Backfill range end (YYYY-MM-DD, inclusive). "
                             "Requires --start.")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_BETWEEN_CALLS,
                        help=f"Min seconds between Meta API calls "
                             f"(default: {DEFAULT_SLEEP_BETWEEN_CALLS}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip API calls; verify config + paths only.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be provided together")

    if args.start and args.end:
        return run_range(args.start, args.end,
                         dry_run=args.dry_run,
                         sleep_between_calls=args.sleep)

    date = args.date or os.environ.get("SNAPSHOT_DATE") or yesterday_utc()
    return run(date, dry_run=args.dry_run, sleep_between_calls=args.sleep)


if __name__ == "__main__":
    sys.exit(main())
