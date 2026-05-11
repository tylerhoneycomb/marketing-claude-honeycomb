#!/usr/bin/env python3
"""compute_scaling_profiles.py — per-vertical structural scaling diagnoses.

Pairs with compute_reallocation.py. This script writes the structural
read; the second script turns it into a budget proposal.

Reads:
  - weekly_rollup via /exec?action=rollup
  - campaign_mapping via /exec?action=mappings
  - data/snapshots/<date>/adset_insights.json (frequency, CPM)
  - Meta Graph: current daily_budget per campaign
  - /exec?action=scaling-queue-read&since=<prev_tuesday> (executed budget_queue rows)
  - /exec?action=get_spend_goal (dynamic TARGET_WEEKLY_SPEND + WEEKLY_SPEND_TOLERANCE)
  - data/config/benchmarks.json

Writes: data/derived/scaling_profiles.json

Per-vertical: classification (scalable | stable | saturating | over-invested),
new_audience_needed modifier, confidence (confident | directional | insufficient),
elasticity_r, ic_rate, cpicp, frequency + CPM trends, spend_share.

Per-campaign: weekly_consumed_pct (absolute sum of |change_pct| across all
sources since previous Tuesday — optimizer + knockdown + any prior strategic),
weekly_remaining_pct, knockdown_applied_this_week, lifetime_ic_conversions.

Headroom is empirical, not theoretical: it counts what actually executed in
budget_queue, because the optimizer runs daily but doesn't always produce
changes (proposals can expire unapproved).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import MetaClient, load_config  # noqa: E402
from lib.io import atomic_write_json  # noqa: E402

SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"
DERIVED_DIR = REPO_ROOT / "data" / "derived"
DEFAULT_OUTPUT = DERIVED_DIR / "scaling_profiles.json"

# Vertical extraction. DUPLICATED from
# skills/creative-intelligence/scripts/build_creative_dataset.py:VERTICAL_RE
# so the two skills agree on vertical assignment. KEEP IN SYNC: a new
# campaign-name pattern handled in one file but not the other will
# produce silently divergent classifications. Follow-up: extract to
# scripts/lib/verticals.py.
VERTICAL_RE = re.compile(
    r"^(?:PAUSED\s*-\s*)?(?:AD|ICD|Rev\d*)-(.+?)-Q\d+-\d{4}$",
    re.IGNORECASE)
LEGACY_VERTICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bwiner", re.IGNORECASE), "wineries"),
]
EXCLUDED_VERTICALS = {"template", "unknown"}

# Trend label thresholds. A weekly slope larger than this fraction of the
# series mean reads as "rising" / "falling"; otherwise "flat". Tuned for
# 4-week windows where week-to-week noise is meaningful but not classifiable.
TREND_FLAT_BAND = 0.05


def extract_vertical(campaign_name: str | None) -> str:
    if not campaign_name:
        return "unknown"
    name = str(campaign_name).strip()
    m = VERTICAL_RE.match(name)
    if m:
        return m.group(1).strip().lower()
    if name.lower().startswith("template"):
        return "template"
    for pattern, slug in LEGACY_VERTICAL_PATTERNS:
        if pattern.search(name):
            return slug
    return name.lower()


def linear_slope(values: list[float]) -> float:
    """Slope of best-fit line through `values` indexed by position."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    return (num / den) if den else 0.0


def pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation. Returns None if undefined (n<2 or zero variance)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den_x = sum((xs[i] - mean_x) ** 2 for i in range(n)) ** 0.5
    den_y = sum((ys[i] - mean_y) ** 2 for i in range(n)) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def classify_trend(values: list[float], flat_band: float = TREND_FLAT_BAND) -> str:
    """rising / flat / falling for a short time-series."""
    if len(values) < 2:
        return "flat"
    mean = sum(values) / len(values)
    if mean == 0:
        return "flat"
    slope = linear_slope(values)
    normalized = slope / mean
    if normalized > flat_band:
        return "rising"
    if normalized < -flat_band:
        return "falling"
    return "flat"


def parse_iso_date(s: Any) -> date | None:
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()
    if not s:
        return None
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def week_start(d: date) -> date:
    """Monday of the week containing `d`."""
    return d - timedelta(days=d.weekday())


def previous_tuesday(today: date) -> date:
    """Most recent Tuesday strictly before `today`. Used for headroom window."""
    # weekday(): Mon=0 ... Tue=1 ... Sun=6
    days_since = (today.weekday() - 1) % 7
    if days_since == 0:
        days_since = 7  # if today is Tuesday, the relevant window starts a week ago
    return today - timedelta(days=days_since)


def fetch_json(url: str, params: dict[str, Any] | None = None,
               retries: int = 3, timeout: int = 30) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            logging.warning("fetch_json attempt %d/%d failed: %s",
                            attempt + 1, retries, exc)
    raise RuntimeError(f"fetch_json exhausted retries: {last}")


def load_snapshot_adset_rows(since: date, until: date) -> list[dict[str, Any]]:
    """Concatenate adset_insights.json across the date window. Each row is
    a daily adset record with campaign_name + frequency + cpm + spend.
    """
    rows: list[dict[str, Any]] = []
    if not SNAPSHOTS_DIR.exists():
        logging.warning("Snapshots dir missing — skipping snapshot-derived signals")
        return rows
    for day_dir in sorted(SNAPSHOTS_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        d = parse_iso_date(day_dir.name)
        if not d or d < since or d > until:
            continue
        f = day_dir / "adset_insights.json"
        if not f.exists():
            continue
        try:
            with f.open() as fh:
                rows.extend(json.load(fh))
        except (OSError, ValueError) as exc:
            logging.warning("Could not read %s: %s", f, exc)
    return rows


def aggregate_rollup_by_vertical_week(
    rollup_rows: list[dict[str, Any]],
) -> dict[tuple[str, date], dict[str, float]]:
    """Group weekly_rollup → (vertical, week_start) → summed metrics.

    weekly_rollup columns: week_start, campaign_name, utm_campaign, spend,
    impressions, clicks, reach, avg_frequency, ctr, meta_conversions,
    ic_conversions, icps_attributed, estimated_icps, attribution_rate, cpl,
    cpicp_attributed, cpicp_blended, ...
    """
    out: dict[tuple[str, date], dict[str, float]] = defaultdict(lambda: {
        "spend": 0.0, "conversions": 0, "ic_conversions": 0,
        "estimated_icps": 0.0, "campaign_names": set(),
    })
    for row in rollup_rows:
        ws = parse_iso_date(row.get("week_start"))
        if not ws:
            continue
        vertical = extract_vertical(row.get("campaign_name"))
        if vertical in EXCLUDED_VERTICALS:
            continue
        bucket = out[(vertical, ws)]
        bucket["spend"] += float(row.get("spend") or 0.0)
        bucket["conversions"] += int(row.get("meta_conversions") or 0)
        bucket["ic_conversions"] += int(row.get("ic_conversions") or 0)
        bucket["estimated_icps"] += float(row.get("estimated_icps") or 0.0)
        bucket["campaign_names"].add(row.get("campaign_name") or "")
    # Sets aren't JSON-serializable; convert at the boundary.
    for k in out:
        out[k]["campaign_names"] = sorted(out[k]["campaign_names"])
    return out


def aggregate_snapshot_by_vertical_week(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, date], dict[str, float]]:
    """Daily adset rows → (vertical, week_start) → spend-weighted frequency, CPM."""
    bucket: dict[tuple[str, date], dict[str, float]] = defaultdict(lambda: {
        "spend": 0.0, "freq_x_spend": 0.0, "cpm_x_spend": 0.0,
        "impressions": 0,
    })
    for r in rows:
        d = parse_iso_date(r.get("date"))
        if not d:
            continue
        vertical = extract_vertical(r.get("campaign_name"))
        if vertical in EXCLUDED_VERTICALS:
            continue
        spend = float(r.get("spend") or 0.0)
        freq = float(r.get("frequency") or 0.0)
        cpm = float(r.get("cpm") or 0.0)
        impressions = int(r.get("impressions") or 0)
        b = bucket[(vertical, week_start(d))]
        b["spend"] += spend
        b["freq_x_spend"] += freq * spend
        b["cpm_x_spend"] += cpm * spend
        b["impressions"] += impressions
    out: dict[tuple[str, date], dict[str, float]] = {}
    for k, b in bucket.items():
        out[k] = {
            "spend": b["spend"],
            "avg_frequency": (b["freq_x_spend"] / b["spend"]) if b["spend"] else 0.0,
            "avg_cpm": (b["cpm_x_spend"] / b["spend"]) if b["spend"] else 0.0,
            "impressions": b["impressions"],
        }
    return out


def compute_elasticity(
    weekly: list[dict[str, float]],
    min_weekly_conversions: int,
) -> tuple[float | None, int]:
    """Pearson r of weekly spend vs weekly CPL. Returns (r, n_weeks_used)."""
    spends = []
    cpls = []
    for w in weekly:
        if w["conversions"] < min_weekly_conversions:
            continue
        if w["spend"] <= 0:
            continue
        cpl = w["spend"] / w["conversions"]
        spends.append(w["spend"])
        cpls.append(cpl)
    return pearson_r(spends, cpls), len(spends)


def high_spend_cpl_degradation(weekly: list[dict[str, float]],
                               min_weekly_conversions: int) -> float | None:
    """Median-split the weeks by spend; compare avg CPL of high-spend vs
    low-spend. Returns degradation pct (positive = high-spend weeks are
    more expensive). None if insufficient data.
    """
    qualifying = [
        w for w in weekly
        if w["conversions"] >= min_weekly_conversions and w["spend"] > 0
    ]
    if len(qualifying) < 4:
        return None
    sorted_by_spend = sorted(qualifying, key=lambda w: w["spend"])
    half = len(sorted_by_spend) // 2
    low = sorted_by_spend[:half]
    high = sorted_by_spend[-half:]
    low_cpl = sum(w["spend"] / w["conversions"] for w in low) / len(low)
    high_cpl = sum(w["spend"] / w["conversions"] for w in high) / len(high)
    if low_cpl == 0:
        return None
    return (high_cpl - low_cpl) / low_cpl


def confidence_label(weeks_with_conversions: int,
                     min_confident: int, min_directional: int) -> str:
    if weeks_with_conversions >= min_confident:
        return "confident"
    if weeks_with_conversions >= min_directional:
        return "directional"
    return "insufficient"


def classify_vertical(metrics: dict[str, Any], benchmarks: dict[str, Any],
                      portfolio_median_cpicp: float | None) -> str:
    """scalable / stable / saturating / over-invested.

    `over-invested` is the saturating verticals whose CPICP is also worse
    than portfolio median. `new_audience_needed` is a separate modifier
    computed in compute_new_audience_needed().
    """
    r = metrics.get("elasticity_r")
    if r is None:
        return "stable"
    abs_r = abs(r)
    cpl_degradation = metrics.get("high_spend_cpl_degradation_pct")
    cpl_deg_threshold = benchmarks["high_spend_cpl_degradation_threshold"]
    cpicp = metrics.get("cpicp")

    if abs_r < benchmarks["elasticity_scalable_threshold"]:
        return "scalable"
    if abs_r < benchmarks["elasticity_saturating_threshold"]:
        return "stable"
    # r >= saturating_threshold
    if (cpl_degradation is not None and cpl_degradation > cpl_deg_threshold):
        if (cpicp is not None and portfolio_median_cpicp is not None
                and cpicp > portfolio_median_cpicp):
            return "over-invested"
        return "saturating"
    return "stable"


def compute_new_audience_needed(metrics: dict[str, Any],
                                benchmarks: dict[str, Any]) -> bool:
    """Vertical-level early warning, decoupled from any single campaign's
    frequency hitting 2.0. Fires when frequency AND CPM trend up over the
    same 4-week window (per benchmarks: frequency_trend_saturation_weeks
    and cpm_trend_weeks both default 4).
    """
    return (metrics.get("frequency_trend") == "rising"
            and metrics.get("cpm_trend") == "rising")


def fetch_executed_queue_rows(exec_url: str, since: date) -> list[dict[str, Any]]:
    """All budget_queue rows since `since` (inclusive). The new
    /exec?action=scaling-queue-read handler returns whatever rows exist
    regardless of status — caller filters to executed for headroom math.
    """
    body = fetch_json(exec_url, {
        "action": "scaling-queue-read",
        "since": since.strftime("%Y-%m-%d"),
    })
    if isinstance(body, dict) and body.get("error"):
        logging.warning("scaling-queue-read returned error: %s", body["error"])
        return []
    rows = body.get("rows") if isinstance(body, dict) else body
    return rows or []


def compute_per_campaign_headroom(
    executed_rows: list[dict[str, Any]],
    max_weekly_pct: float,
) -> dict[str, dict[str, Any]]:
    """Sum |change_pct| per campaign across all executed rows in window.
    Knockdown is identified by substring match on signal_reasons — a
    diagnostic flag, not a filter.

    Status filter: only `executed` rows count. Other statuses
    (`pending`, `approved`, `rejected`, `expired`, `failed`) are
    excluded because they never moved Meta's actual budget. An
    `approved`-but-not-yet-executed optimizer proposal does NOT count
    against this week's cap; it'll count after the 3 AM execution
    trigger transitions it to `executed` (Code.js:3429).
    """
    consumed: dict[str, float] = defaultdict(float)
    knockdown_seen: dict[str, bool] = defaultdict(bool)
    for r in executed_rows:
        if str(r.get("status", "")).lower() != "executed":
            continue
        cid = str(r.get("campaign_id") or "")
        if not cid:
            continue
        try:
            pct = abs(float(r.get("change_pct") or 0.0))
        except (TypeError, ValueError):
            pct = 0.0
        # change_pct is stored as a percent (Code.js writeToQueue_ at line 3167:
        # Math.round((change/current) * 10000) / 100 → e.g. 2.0 = 2%).
        # Normalize to fractions for consistency with benchmarks thresholds.
        consumed[cid] += pct / 100.0
        if "portfolio knockdown" in str(r.get("signal_reasons") or "").lower():
            knockdown_seen[cid] = True
    out: dict[str, dict[str, Any]] = {}
    for cid, pct in consumed.items():
        out[cid] = {
            "weekly_consumed_pct": round(pct, 4),
            "weekly_remaining_pct": round(max(0.0, max_weekly_pct - pct), 4),
            "knockdown_applied_this_week": knockdown_seen.get(cid, False),
        }
    return out


def fetch_current_campaign_budgets(client: MetaClient) -> dict[str, dict[str, Any]]:
    """Map campaign_id → {daily_budget_cents, name, effective_status}.
    Filters to ACTIVE/PAUSED. Skips campaigns without a daily budget
    (lifetime-budget campaigns are out of scope for the daily optimizer).
    """
    rows = client.campaigns(filtering=[{
        "field": "effective_status",
        "operator": "IN",
        "value": ["ACTIVE", "PAUSED"],
    }])
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("daily_budget"):
            continue
        out[row["id"]] = {
            "daily_budget_cents": int(row["daily_budget"]),
            "name": row.get("name"),
            "effective_status": row.get("effective_status"),
        }
    return out


def lifetime_ic_per_campaign(rollup_rows: list[dict[str, Any]],
                             campaign_id_by_name: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rollup_rows:
        cid = campaign_id_by_name.get(r.get("campaign_name") or "")
        if not cid:
            continue
        out[cid] += int(r.get("ic_conversions") or 0)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", default=str(DEFAULT_OUTPUT),
                   help=f"Output path (default {DEFAULT_OUTPUT})")
    p.add_argument("--exec-url", default=os.environ.get("EXEC_ENDPOINT"),
                   help="Override /exec endpoint (defaults to benchmarks.json)")
    p.add_argument("--token", default=os.environ.get("META_ACCESS_TOKEN"),
                   help="Meta API token (env META_ACCESS_TOKEN)")
    p.add_argument("--today", default=None,
                   help="Override today's date (YYYY-MM-DD, for backtests)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config()
    benchmarks = config["scaling"]
    exec_url = args.exec_url or config["exec_endpoint"]
    if not args.token:
        logging.error("META_ACCESS_TOKEN missing")
        return 2

    today = (parse_iso_date(args.today)
             or datetime.now(timezone.utc).date())
    prev_tue = previous_tuesday(today)
    elasticity_window_weeks = benchmarks["elasticity_window_weeks"]
    cutoff_week = week_start(today) - timedelta(weeks=elasticity_window_weeks - 1)
    logging.info("today=%s prev_tuesday=%s elasticity_window_start=%s",
                 today, prev_tue, cutoff_week)

    # ─── Fetch all inputs ──────────────────────────────────────────────
    logging.info("Fetching weekly_rollup")
    rollup = fetch_json(exec_url, {"action": "rollup"}) or []
    logging.info("Fetching campaign_mapping")
    mappings = fetch_json(exec_url, {"action": "mappings"}) or []
    logging.info("Fetching dynamic spend goal")
    spend_goal_body = fetch_json(exec_url, {"action": "get_spend_goal"}) or {}
    target_weekly = float(
        spend_goal_body.get("target_weekly_spend") or 10000)
    tolerance_weekly = float(
        spend_goal_body.get("weekly_spend_tolerance") or 500)

    logging.info("Fetching executed budget_queue rows since %s", prev_tue)
    executed_rows = fetch_executed_queue_rows(exec_url, prev_tue)
    logging.info("Got %d budget_queue rows", len(executed_rows))

    client = MetaClient(
        account_id=config["account"]["id"],
        api_version=config["account"]["meta_api_version"],
        token=args.token,
    )
    logging.info("Fetching current campaign daily_budget from Meta")
    current_budgets = fetch_current_campaign_budgets(client)
    logging.info("Got daily budgets for %d campaigns", len(current_budgets))

    # Snapshot window: 12 weeks back through yesterday for trend analysis.
    snapshot_until = today - timedelta(days=1)
    snapshot_since = cutoff_week
    logging.info("Loading adset snapshots %s → %s",
                 snapshot_since, snapshot_until)
    adset_rows = load_snapshot_adset_rows(snapshot_since, snapshot_until)
    logging.info("Got %d adset-day rows", len(adset_rows))

    # ─── Per-campaign headroom + lifetime IC ───────────────────────────
    headroom_by_campaign = compute_per_campaign_headroom(
        executed_rows, benchmarks["max_weekly_total_change_pct"]
    )
    campaign_id_by_name: dict[str, str] = {}
    for m in mappings:
        cid = str(m.get("campaign_id") or "").strip()
        cname = str(m.get("campaign_name") or "").strip()
        if cid and cname:
            campaign_id_by_name[cname] = cid
    lifetime_ic = lifetime_ic_per_campaign(rollup, campaign_id_by_name)

    # ─── Per-vertical aggregation ──────────────────────────────────────
    rollup_by_vw = aggregate_rollup_by_vertical_week(rollup)
    snap_by_vw = aggregate_snapshot_by_vertical_week(adset_rows)

    verticals = sorted({k[0] for k in rollup_by_vw.keys()})
    logging.info("Verticals in rollup: %s", verticals)

    vertical_metrics: dict[str, dict[str, Any]] = {}
    portfolio_total_spend = 0.0
    portfolio_total_ic = 0
    portfolio_cpicps: list[float] = []
    portfolio_ic_rates: list[float] = []

    for vertical in verticals:
        weeks = []
        for ws in sorted({k[1] for k in rollup_by_vw.keys()}):
            if ws < cutoff_week or ws > week_start(today):
                continue
            r = rollup_by_vw.get((vertical, ws))
            if not r:
                continue
            weeks.append({
                "week_start": ws,
                "spend": r["spend"],
                "conversions": r["conversions"],
                "ic_conversions": r["ic_conversions"],
            })
        if not weeks:
            continue

        total_spend = sum(w["spend"] for w in weeks)
        total_conv = sum(w["conversions"] for w in weeks)
        total_ic = sum(w["ic_conversions"] for w in weeks)
        weeks_with_conv = sum(
            1 for w in weeks
            if w["conversions"] >= benchmarks["min_weekly_conversions"]
        )

        elasticity, n_eligible_weeks = compute_elasticity(
            weeks, benchmarks["min_weekly_conversions"]
        )
        cpl_degradation = high_spend_cpl_degradation(
            weeks, benchmarks["min_weekly_conversions"]
        )

        # Frequency / CPM trend over the last `frequency_trend_saturation_weeks`
        # and `cpm_trend_weeks` windows from the snapshot data.
        freq_window = benchmarks["frequency_trend_saturation_weeks"]
        cpm_window = benchmarks["cpm_trend_weeks"]
        recent_weeks_freq: list[float] = []
        recent_weeks_cpm: list[float] = []
        recent_weeks_freq_spend = 0.0
        recent_freq_x_spend = 0.0
        for ws in sorted(
            (k[1] for k in snap_by_vw.keys() if k[0] == vertical),
        )[-max(freq_window, cpm_window):]:
            s = snap_by_vw[(vertical, ws)]
            if s["spend"] <= 0:
                continue
            recent_weeks_freq.append(s["avg_frequency"])
            recent_weeks_cpm.append(s["avg_cpm"])
            recent_weeks_freq_spend += s["spend"]
            recent_freq_x_spend += s["avg_frequency"] * s["spend"]

        # Truncate freq/cpm to their respective windows.
        recent_weeks_freq = recent_weeks_freq[-freq_window:]
        recent_weeks_cpm = recent_weeks_cpm[-cpm_window:]
        avg_frequency = (
            recent_freq_x_spend / recent_weeks_freq_spend
            if recent_weeks_freq_spend else None
        )
        cpicp = (total_spend / total_ic) if total_ic > 0 else None
        ic_rate = (total_ic / total_conv) if total_conv > 0 else None

        if cpicp is not None:
            portfolio_cpicps.append(cpicp)
        if ic_rate is not None:
            portfolio_ic_rates.append(ic_rate)
        portfolio_total_spend += total_spend
        portfolio_total_ic += total_ic

        # Optimizer eligibility: at least one campaign in this vertical clears
        # the LIFETIME_MIN_CONVERSIONS gate (10).
        all_camp_names: set[str] = set()
        for ws in sorted({k[1] for k in rollup_by_vw.keys()}):
            r = rollup_by_vw.get((vertical, ws))
            if r:
                all_camp_names.update(r["campaign_names"])
        camp_ids = [campaign_id_by_name.get(n) for n in all_camp_names]
        camp_ids = [c for c in camp_ids if c]
        optimizer_eligible = any(
            lifetime_ic.get(cid, 0) >= 10 for cid in camp_ids
        )

        vertical_metrics[vertical] = {
            "weeks_in_window": len(weeks),
            "weeks_with_conversions": weeks_with_conv,
            "elasticity_r": (round(elasticity, 4)
                             if elasticity is not None else None),
            "elasticity_n_weeks": n_eligible_weeks,
            "high_spend_cpl_degradation_pct": (
                round(cpl_degradation, 4) if cpl_degradation is not None else None
            ),
            "ic_rate": round(ic_rate, 4) if ic_rate is not None else None,
            "cpicp": round(cpicp, 2) if cpicp is not None else None,
            "total_spend": round(total_spend, 2),
            "total_conversions": int(total_conv),
            "total_ic_conversions": int(total_ic),
            "avg_frequency": (round(avg_frequency, 3)
                              if avg_frequency is not None else None),
            "frequency_trend": classify_trend(recent_weeks_freq),
            "cpm_trend": classify_trend(recent_weeks_cpm),
            "frequency_series": [round(v, 3) for v in recent_weeks_freq],
            "cpm_series": [round(v, 2) for v in recent_weeks_cpm],
            "campaign_ids": camp_ids,
            "campaign_names": sorted(all_camp_names),
            "optimizer_eligible": optimizer_eligible,
        }

    # Portfolio medians.
    portfolio_median_cpicp = (statistics.median(portfolio_cpicps)
                              if portfolio_cpicps else None)
    portfolio_median_ic_rate = (statistics.median(portfolio_ic_rates)
                                if portfolio_ic_rates else None)

    # ─── Classification + confidence + new_audience_needed ─────────────
    for vertical, m in vertical_metrics.items():
        m["classification"] = classify_vertical(
            m, benchmarks, portfolio_median_cpicp
        )
        m["new_audience_needed"] = compute_new_audience_needed(m, benchmarks)
        m["confidence"] = confidence_label(
            m["weeks_with_conversions"],
            benchmarks["min_weeks_confident"],
            benchmarks["min_weeks_directional"],
        )
        m["spend_share_pct"] = (
            round(m["total_spend"] / portfolio_total_spend * 100, 2)
            if portfolio_total_spend > 0 else 0.0
        )
        # Insufficient verticals get classification cleared. They appear in
        # the brief as informational only — no proposals.
        if m["confidence"] == "insufficient":
            m["classification"] = "insufficient"

    # ─── Per-campaign block ────────────────────────────────────────────
    per_campaign: dict[str, dict[str, Any]] = {}
    for cid, budget_info in current_budgets.items():
        head = headroom_by_campaign.get(cid, {
            "weekly_consumed_pct": 0.0,
            "weekly_remaining_pct": benchmarks["max_weekly_total_change_pct"],
            "knockdown_applied_this_week": False,
        })
        per_campaign[cid] = {
            "campaign_id": cid,
            "campaign_name": budget_info["name"],
            "effective_status": budget_info["effective_status"],
            "daily_budget_cents": budget_info["daily_budget_cents"],
            "lifetime_ic_conversions": int(lifetime_ic.get(cid, 0)),
            "vertical": extract_vertical(budget_info["name"]),
            "weekly_consumed_pct": head["weekly_consumed_pct"],
            "weekly_remaining_pct": head["weekly_remaining_pct"],
            "knockdown_applied_this_week": head["knockdown_applied_this_week"],
        }

    # ─── Portfolio block ───────────────────────────────────────────────
    current_total_daily_cents = sum(
        b["daily_budget_cents"] for b in current_budgets.values()
    )
    target_weekly_dollars = target_weekly
    target_daily_dollars = target_weekly_dollars / 7
    tolerance_daily_dollars = tolerance_weekly / 7
    tolerance_headroom_daily = round(
        tolerance_daily_dollars * 100
        - max(0, current_total_daily_cents - target_daily_dollars * 100), 0,
    )

    optimizer_cycles = len({
        r.get("token") for r in executed_rows
        if r.get("token") and str(r.get("status", "")).lower() == "executed"
    })

    portfolio = {
        "current_total_daily_cents": int(current_total_daily_cents),
        "current_total_weekly_dollars": round(current_total_daily_cents / 100 * 7, 2),
        "target_weekly_spend": target_weekly_dollars,
        "weekly_spend_tolerance": tolerance_weekly,
        "tolerance_headroom_daily_cents": int(tolerance_headroom_daily),
        "median_cpicp": (round(portfolio_median_cpicp, 2)
                         if portfolio_median_cpicp is not None else None),
        "median_ic_rate": (round(portfolio_median_ic_rate, 4)
                           if portfolio_median_ic_rate is not None else None),
        "optimizer_cycles_this_week": optimizer_cycles,
    }

    output = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "today": today.isoformat(),
        "previous_tuesday": prev_tue.isoformat(),
        "elasticity_window_weeks": elasticity_window_weeks,
        "benchmarks": benchmarks,
        "portfolio": portfolio,
        "verticals": vertical_metrics,
        "campaigns": per_campaign,
    }

    out_path = Path(args.output)
    atomic_write_json(out_path, output, default=str)
    logging.info("Wrote %s (%d verticals, %d campaigns)",
                 out_path, len(vertical_metrics), len(per_campaign))

    # Stdout summary for the workflow.
    summary = {
        "verticals": {v: m["classification"]
                      for v, m in vertical_metrics.items()},
        "scalable": [v for v, m in vertical_metrics.items()
                     if m["classification"] == "scalable"],
        "saturating": [v for v, m in vertical_metrics.items()
                       if m["classification"] == "saturating"],
        "over_invested": [v for v, m in vertical_metrics.items()
                          if m["classification"] == "over-invested"],
        "new_audience_needed": [v for v, m in vertical_metrics.items()
                                if m.get("new_audience_needed")],
        "portfolio": portfolio,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
