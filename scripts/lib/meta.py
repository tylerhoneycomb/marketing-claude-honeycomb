"""Shared Meta Graph API client + normalization helpers.

Used by:
  - scripts/fetch_ad_data.py            (snapshot pipeline)
  - skills/daily-check/scripts/...      (daily-check skill)
  - skills/fatigue-monitor/scripts/...  (fatigue-monitor skill)

Single source for: HTTP retries, Meta error-code handling, paging,
field lists, IC conversion extraction, and row normalization. New
skills should import from here rather than duplicating the client.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "data" / "config" / "benchmarks.json"

# Standard Meta lead action types. These mirror collectMetaRows_ in
# apps-script/Code.js so ad-level totals reconcile with the campaign rollup.
LEAD_ACTION_TYPES = [
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "onsite_conversion.lead_grouped",
]

INSIGHTS_FIELDS_CAMPAIGN = [
    "campaign_id",
    "campaign_name",
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
    "created_time",
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

# Meta error codes that indicate throttling or transient failure.
# https://developers.facebook.com/docs/graph-api/guides/error-handling
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


def ic_action_type_from_config(config: dict[str, Any]) -> str:
    """Reconstruct the Meta `actions[]` action_type for the IC custom conversion."""
    cid = config["ic_tracking"]["custom_conversion_id"]
    return f"offsite_conversion.custom.{cid}"


class MetaClient:
    """Meta Graph API wrapper: paging, throttling, and exponential backoff."""

    def __init__(self, account_id: str, api_version: str, token: str,
                 sleep_between_calls: float = DEFAULT_SLEEP_BETWEEN_CALLS):
        self.account_id = account_id
        self.api_version = api_version
        self.token = token
        self.sleep_between_calls = sleep_between_calls
        self.base = f"https://graph.facebook.com/{api_version}"
        self._last_call_at: float = 0.0

    def _throttle(self) -> None:
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
            next_params = None
        return rows

    def insights(self, level: str, fields: list[str], since: str,
                 until: str | None = None, time_increment: int = 1,
                 extra_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Insights at level=campaign|adset|ad. Inclusive date range.

        For a single day, omit `until`. `time_increment=1` returns daily rows
        within the range; pass `time_increment="all_days"` for a single
        aggregated row per entity.
        """
        if until is None:
            until = since
        params: dict[str, Any] = {
            "fields": ",".join(fields),
            "level": level,
            "time_range": json.dumps({"since": since, "until": until}),
            "time_increment": time_increment,
            "limit": 200,
        }
        if extra_params:
            params.update(extra_params)
        url = f"{self.base}/{self.account_id}/insights"
        return self._paginate(url, params)

    def adsets(self, fields: list[str] | None = None,
               filtering: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": ",".join(fields or ADSET_OBJECT_FIELDS),
            "limit": 200,
        }
        if filtering:
            params["filtering"] = json.dumps(filtering)
        url = f"{self.base}/{self.account_id}/adsets"
        return self._paginate(url, params)

    def ads(self, fields: list[str] | None = None,
            filtering: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": ",".join(fields or AD_OBJECT_FIELDS),
            "limit": 200,
        }
        if filtering:
            params["filtering"] = json.dumps(filtering)
        url = f"{self.base}/{self.account_id}/ads"
        return self._paginate(url, params)

    def creative(self, creative_id: str) -> dict[str, Any]:
        params = {"fields": ",".join(CREATIVE_FIELDS)}
        url = f"{self.base}/{creative_id}"
        return self._request(url, params)


# ─── Action / row extraction ───────────────────────────────────────────────

def extract_conversions(actions: list[dict[str, Any]] | None, ic_action_type: str,
                        lead_action_types: list[str] | None = None
                        ) -> tuple[int, int]:
    """Return (conversions, ic_conversions) from Meta `actions[]`.

    `conversions`: first matching lead action_type wins (mirrors Apps Script).
    `ic_conversions`: sum of all entries with the IC action type.
    """
    leads = lead_action_types or LEAD_ACTION_TYPES
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
        if atype in leads and conversions == 0:
            conversions = value
        if atype == ic_action_type:
            ic_conversions += value
    return conversions, ic_conversions


def normalize_insights_row(row: dict[str, Any], ic_action_type: str,
                           lead_action_types: list[str] | None = None,
                           date: str | None = None) -> dict[str, Any]:
    """Flatten a Meta insights row. Use `date_start` if `date` not supplied."""
    conversions, ic_conversions = extract_conversions(
        row.get("actions"), ic_action_type, lead_action_types
    )
    return {
        "date": date or row.get("date_start"),
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
        "created_time": row.get("created_time"),
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
