"""Shared Meta Graph API client + normalization helpers.

Used by:
  - scripts/fetch_ad_data.py            (snapshot pipeline)
  - skills/daily-check/scripts/...      (daily-check skill)
  - skills/fatigue-monitor/scripts/...  (fatigue-monitor skill)

Single source for: HTTP retries, Meta error-code handling, paging,
field lists, funnel-conversion extraction, and row normalization. New
skills should import from here rather than duplicating the client.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "data" / "config" / "benchmarks.json"

# Fallback lead priority chain, used only when benchmarks.json carries no
# `conversions.primary.action_type_priority`. These action types are
# ALTERNATIVE COUNTS OF THE SAME EVENT, not addends: verified 2026-09-09
# against both live Q3 campaigns, `lead` / `offsite_conversion.fb_pixel_lead`
# / `onsite_web_lead` each returned identical values (215 and 180). Summing
# them would multiply-count leads. First present type wins.
LEAD_ACTION_PRIORITY = [
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "onsite_web_lead",
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
    "image_url",
    "object_story_spec",
    "effective_object_story_id",
    "asset_feed_spec",
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
# Cap per-retry backoff at 5 minutes. Meta's per-app rate-limit
# windows can be 5-60 minutes long; the previous 60-second cap
# burned through 6 retries in ~63s, then crashed, when the right
# behavior was to wait it out.
MAX_BACKOFF_SECONDS = 300
# Cap pagination loops as a safeguard against a misbehaving cursor
# or runaway loop. At limit=200 per page (insights, adsets, ads),
# this gives 40,000 rows of headroom — far above the current
# Honeycomb ad-account size.
MAX_PAGES = 200


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _custom_action_type(conversion_id: str) -> str:
    return f"offsite_conversion.custom.{conversion_id}"


@dataclass(frozen=True)
class FunnelSpec:
    """Which Meta action types map to each tier of the Honeycomb funnel.

    Built from `conversions` in benchmarks.json so the funnel is defined in
    exactly one place. `lead_priority` is an ordered preference list, NOT a
    set to sum (see LEAD_ACTION_PRIORITY). `subtypes` maps an output field
    name onto the custom-conversion action type that populates it.
    """

    lead_priority: tuple[str, ...]
    quality_action_type: str | None
    quality_field: str
    subtypes: tuple[tuple[str, str], ...]

    @property
    def count_fields(self) -> tuple[str, ...]:
        """Every numeric conversion field this spec emits, in row order."""
        return ("leads", self.quality_field) + tuple(f for f, _ in self.subtypes)


def funnel_from_config(config: dict[str, Any]) -> FunnelSpec:
    """Build a FunnelSpec from benchmarks.json."""
    conv = config.get("conversions") or {}

    primary = conv.get("primary") or {}
    lead_priority = tuple(primary.get("action_type_priority") or LEAD_ACTION_PRIORITY)

    quality = conv.get("quality") or {}
    quality_id = quality.get("custom_conversion_id")
    quality_field = quality.get("field") or "prequal_decisions"

    subtypes: list[tuple[str, str]] = []
    for name, spec in sorted((conv.get("subtypes") or {}).items()):
        if not isinstance(spec, dict):
            continue  # skip the "_doc" string key
        cid = spec.get("custom_conversion_id")
        if cid:
            subtypes.append((spec.get("field") or f"{name}_conversions",
                             _custom_action_type(cid)))

    return FunnelSpec(
        lead_priority=lead_priority,
        quality_action_type=_custom_action_type(quality_id) if quality_id else None,
        quality_field=quality_field,
        subtypes=tuple(subtypes),
    )


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
            backoff = min(2 ** attempt, MAX_BACKOFF_SECONDS)
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
        pages_seen = 0
        while next_url:
            page = self._request(next_url, next_params)
            rows.extend(page.get("data", []))
            paging = page.get("paging") or {}
            next_url = paging.get("next")
            next_params = None
            pages_seen += 1
            if pages_seen >= MAX_PAGES and next_url:
                logging.warning(
                    "_paginate hit MAX_PAGES=%d cap on %s — truncating "
                    "(%d rows so far). Investigate if this isn't expected.",
                    MAX_PAGES, url, len(rows),
                )
                break
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

    def campaigns(self, fields: list[str] | None = None,
                  filtering: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": ",".join(fields or ["id", "name", "daily_budget",
                                          "lifetime_budget", "effective_status"]),
            "limit": 200,
        }
        if filtering:
            params["filtering"] = json.dumps(filtering)
        url = f"{self.base}/{self.account_id}/campaigns"
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

    def resolve_image_hashes(self, hashes: list[str],
                             chunk_size: int = 50) -> dict[str, dict[str, Any]]:
        """Resolve image_hash values to full-size image records.

        Asset Feed dynamic creative ads embed image references as
        bare hashes in asset_feed_spec.images[]. The top-level
        image_url is empty for these ads. /act_X/adimages takes a
        hashes=[...] param and returns one record per hash with the
        full-size url, dimensions, and metadata.

        Returns: dict keyed by hash. Hashes that don't resolve are
        omitted. Results are aggregated across multiple paged
        requests if the input exceeds chunk_size.
        """
        if not hashes:
            return {}
        unique = list({h for h in hashes if h})
        out: dict[str, dict[str, Any]] = {}
        url = f"{self.base}/{self.account_id}/adimages"
        fields = "hash,url,permalink_url,width,height,name"
        for i in range(0, len(unique), chunk_size):
            batch = unique[i:i + chunk_size]
            params = {
                "hashes": json.dumps(batch),
                "fields": fields,
            }
            try:
                body = self._request(url, params)
            except RuntimeError as exc:
                logging.warning("resolve_image_hashes: batch failed: %s", exc)
                continue
            for rec in (body.get("data") or []):
                h = rec.get("hash")
                if h:
                    out[h] = rec
        return out


# ─── Action / row extraction ───────────────────────────────────────────────

def extract_conversions(actions: list[dict[str, Any]] | None,
                        funnel: FunnelSpec) -> dict[str, int]:
    """Return one count per funnel tier from Meta `actions[]`.

    Leads resolve by PRIORITY, not by summing: the first action type present
    in `funnel.lead_priority` wins outright, because those types are
    alternative counts of the same event (see LEAD_ACTION_PRIORITY). Quality
    and subtype tiers are distinct custom conversions and are read directly.
    """
    counts = {field: 0 for field in funnel.count_fields}
    if not actions:
        return counts

    by_type: dict[str, int] = {}
    for entry in actions:
        atype = entry.get("action_type")
        if not atype:
            continue
        try:
            value = int(float(entry.get("value", 0)))
        except (TypeError, ValueError):
            value = 0
        by_type[atype] = by_type.get(atype, 0) + value

    for atype in funnel.lead_priority:
        if atype in by_type:
            counts["leads"] = by_type[atype]
            break

    if funnel.quality_action_type:
        counts[funnel.quality_field] = by_type.get(funnel.quality_action_type, 0)

    for field, atype in funnel.subtypes:
        counts[field] = by_type.get(atype, 0)

    return counts


def normalize_insights_row(row: dict[str, Any], funnel: FunnelSpec,
                           date: str | None = None) -> dict[str, Any]:
    """Flatten a Meta insights row. Use `date_start` if `date` not supplied."""
    counts = extract_conversions(row.get("actions"), funnel)
    normalized = {
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
    }
    normalized.update(counts)
    # Deprecated alias: pre-pivot snapshots and the Apps Script campaign
    # rollup both key on `conversions`. Kept so old and new snapshots stay
    # mutually readable; `leads` is canonical. Remove once the 250-day
    # backfill has landed and no reader references `conversions`.
    normalized["conversions"] = counts["leads"]
    return normalized


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
    """Flatten a creative row into the shape downstream consumers expect.

    Honeycomb's ad mix is dominated by Asset Feed dynamic creative
    (asset_feed_spec) — each ad carries up to 5 bodies + 5 titles +
    5 descriptions + 10 images, and Meta's optimizer mixes-and-
    matches at delivery time. The `bodies`, `titles`, `descriptions`,
    `image_hashes` arrays preserve the raw variant pool for the
    Creative Intelligence skill.

    Top-level scalar fields (`title`, `body`, `image_hash`,
    `call_to_action_type`) are aliased to index 0 of their array for
    backward compatibility with callers that consume a single value
    (the fatigue-monitor skill, the snapshot pipeline). For static
    link ads without an asset_feed_spec we fall back to the legacy
    object_story_spec.link_data path.
    """
    story = row.get("object_story_spec") or {}
    link_data = story.get("link_data") or {}
    cta_legacy = link_data.get("call_to_action") or {}
    afs = row.get("asset_feed_spec") or {}

    def _texts(items: list[dict[str, Any]] | None) -> list[str]:
        if not items:
            return []
        return [str(it.get("text")).strip()
                for it in items
                if isinstance(it, dict) and it.get("text")
                and str(it.get("text")).strip()]

    bodies = _texts(afs.get("bodies"))
    titles = _texts(afs.get("titles"))
    descriptions = _texts(afs.get("descriptions"))
    image_hashes = [
        h for h in (
            (img or {}).get("hash") for img in (afs.get("images") or []))
        if h
    ]
    cta_types = [
        c.get("type") for c in (afs.get("call_to_action_types") or [])
        if isinstance(c, dict) and c.get("type")
    ]
    link_urls = [
        u.get("website_url") or u.get("link")
        for u in (afs.get("link_urls") or [])
        if isinstance(u, dict) and (u.get("website_url") or u.get("link"))
    ]

    image_url = (row.get("image_url")
                 or link_data.get("picture")
                 or row.get("thumbnail_url"))

    return {
        "creative_id": row.get("id"),
        "name": row.get("name"),
        "thumbnail_url": row.get("thumbnail_url"),
        "image_url": image_url,
        "effective_object_story_id": row.get("effective_object_story_id"),
        # Asset-feed variant arrays (raw text preserved end-to-end):
        "bodies": bodies,
        "titles": titles,
        "descriptions": descriptions,
        "image_hashes": image_hashes,
        "cta_types": cta_types,
        "link_urls": link_urls,
        # Backward-compat scalar aliases — first variant for asset-feed
        # ads, legacy fields for static link ads:
        "image_hash": (image_hashes[0] if image_hashes
                       else row.get("image_hash")
                       or link_data.get("image_hash")),
        "title": titles[0] if titles else (
            row.get("title") or link_data.get("name")),
        "body": bodies[0] if bodies else (
            row.get("body") or link_data.get("message")),
        "link_url": link_urls[0] if link_urls else (
            row.get("link_url") or link_data.get("link")),
        "call_to_action_type": (cta_types[0] if cta_types
                                else row.get("call_to_action_type")
                                or cta_legacy.get("type")),
    }


def download_image(creative_id_or_hash: str, url: str | None,
                   dest_dir: Path) -> Path | None:
    """Download a creative image to dest_dir/<creative_id_or_hash>.jpg.

    Idempotent (skips if file already exists and is non-empty), atomic
    (tmp + rename), and best-effort (one retry on transient error;
    logs a warning and returns None on hard failure rather than
    aborting the caller).

    Honeycomb's ads expose full-size URLs only via /act_X/adimages
    after resolving image_hash; thumbnail_url is the fallback when
    that resolution isn't available. This helper is URL-agnostic —
    callers pick the source.
    """
    if not url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{creative_id_or_hash}.jpg"
    if target.exists() and target.stat().st_size > 0:
        return target

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=20, stream=True)
            if resp.status_code != 200:
                last_err = RuntimeError(
                    f"HTTP {resp.status_code} on {url[:120]}")
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                break
            tmp = target.with_suffix(".jpg.tmp")
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            tmp.rename(target)
            return target
        except requests.RequestException as exc:
            last_err = exc
            if attempt == 0:
                time.sleep(1)
                continue
            break

    logging.warning("download_image: failed for %s: %s",
                    creative_id_or_hash, last_err)
    return None
