#!/usr/bin/env python3
"""Derive fatigue + winner/bleeder signals from ad-level snapshots.

Reads the latest N days of snapshots under data/snapshots/ and writes:
  data/derived/fatigue_signals.json   - per-ad CTR trend, frequency alerts
  data/derived/winner_bleeder.json    - per-ad ranking within ad set
  data/derived/summary.json           - top-line counts for the daily skill

Pure compute. No network calls. Safe to re-run any time.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.io import atomic_write_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "data" / "config" / "benchmarks.json"
SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"
DERIVED_DIR = REPO_ROOT / "data" / "derived"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def load_snapshot_dates(window_days: int) -> list[str]:
    """Return up to `window_days` most recent snapshot dates that exist on disk."""
    if not SNAPSHOTS_DIR.exists():
        return []
    dirs = sorted(
        (p.name for p in SNAPSHOTS_DIR.iterdir()
         if p.is_dir() and p.name[0:4].isdigit()),
        reverse=True,
    )
    return list(reversed(dirs[:window_days]))  # oldest → newest


def load_ad_insights(dates: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in dates:
        path = SNAPSHOTS_DIR / d / "ad_insights.json"
        if not path.exists():
            logging.warning("missing ad_insights for %s — skipping", d)
            continue
        with path.open() as f:
            rows.extend(json.load(f))
    return rows


def load_latest_adsets(dates: list[str]) -> dict[str, dict[str, Any]]:
    """Load the most recent adsets.json and key by adset_id."""
    for d in reversed(dates):
        path = SNAPSHOTS_DIR / d / "adsets.json"
        if path.exists():
            with path.open() as f:
                rows = json.load(f)
            return {r["adset_id"]: r for r in rows if r.get("adset_id")}
    return {}


def load_creatives() -> dict[str, dict[str, Any]]:
    """Load creative metadata keyed by creative_id."""
    path = REPO_ROOT / "data" / "creatives" / "creatives.json"
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            payload = json.load(f)
        return {c["creative_id"]: c for c in payload.get("creatives", [])
                if c.get("creative_id")}
    except (json.JSONDecodeError, OSError):
        return {}


def load_ad_to_creative(dates: list[str]) -> dict[str, str]:
    """ad_id → creative_id mapping from the most recent ads.json."""
    for d in reversed(dates):
        path = SNAPSHOTS_DIR / d / "ads.json"
        if path.exists():
            with path.open() as f:
                rows = json.load(f)
            return {r["ad_id"]: r.get("creative_id") for r in rows
                    if r.get("ad_id") and r.get("creative_id")}
    return {}


def row_leads(row: dict[str, Any]) -> int:
    """Leads for a row, tolerating pre-pivot snapshots.

    Snapshots written before the 2026-09-09 lead pivot carry only
    `conversions`; newer ones carry canonical `leads` plus `conversions` as a
    deprecated alias. Reading both keeps the 250-day history usable while the
    backfill lands.
    """
    value = row.get("leads")
    if value is None:
        value = row.get("conversions")
    return int(value or 0)


def safe_cpl(spend: float, leads: int) -> float | None:
    """Cost per lead, or None when there are no leads to divide by."""
    return round(spend / leads, 2) if leads else None


def linear_trend_slope(values: list[float]) -> float:
    """Slope of best-fit line through `values` indexed by their position.

    Returns the per-day change. Negative slope = declining metric.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def pct_change(current: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return (current - baseline) / baseline * 100.0


def group_by_ad(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        ad_id = r.get("ad_id")
        if not ad_id:
            continue
        grouped[ad_id].append(r)
    for ad_id in grouped:
        grouped[ad_id].sort(key=lambda r: r["date"])
    return grouped


def compute_ad_metrics(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute rolling metrics for a single ad's daily history (chronological)."""
    impressions = [r["impressions"] for r in history]
    clicks = [r["clicks"] for r in history]
    ctrs = [r["ctr"] for r in history]
    freqs = [r["frequency"] for r in history]
    spend = sum(r["spend"] for r in history)

    leads = [row_leads(r) for r in history]
    total_leads = sum(leads)

    ctr_7d = statistics.mean(ctrs) if ctrs else 0.0
    freq_7d = statistics.mean(freqs) if freqs else 0.0
    ctr_slope = linear_trend_slope(ctrs)

    if len(ctrs) >= 4:
        baseline = statistics.mean(ctrs[: len(ctrs) // 2])
        recent = statistics.mean(ctrs[len(ctrs) // 2 :])
        ctr_decline_pct = pct_change(recent, baseline)
    else:
        ctr_decline_pct = None

    # CPL inflation: split the window in half and compare cost per lead.
    # Positive means the ad got MORE expensive per lead in the recent half.
    cpl_inflation_pct = None
    if len(history) >= 4:
        mid = len(history) // 2
        early_spend = sum(r["spend"] for r in history[:mid])
        late_spend = sum(r["spend"] for r in history[mid:])
        early_leads = sum(leads[:mid])
        late_leads = sum(leads[mid:])
        if early_leads and late_leads:
            early_cpl = early_spend / early_leads
            late_cpl = late_spend / late_leads
            cpl_inflation_pct = pct_change(late_cpl, early_cpl)

    return {
        "days_active": len(history),
        "total_impressions": sum(impressions),
        "total_clicks": sum(clicks),
        "total_spend": round(spend, 2),
        "total_leads": total_leads,
        "total_prequal_decisions": sum(int(r.get("prequal_decisions") or 0) for r in history),
        "total_ic_conversions": sum(int(r.get("ic_conversions") or 0) for r in history),
        "cpl": safe_cpl(spend, total_leads),
        "cpl_inflation_pct": (round(cpl_inflation_pct, 2)
                              if cpl_inflation_pct is not None else None),
        "ctr_7d_rolling": round(ctr_7d, 4),
        "frequency_7d": round(freq_7d, 3),
        "ctr_slope": round(ctr_slope, 6),
        "ctr_decline_pct": round(ctr_decline_pct, 2) if ctr_decline_pct is not None else None,
        "first_date": history[0]["date"],
        "last_date": history[-1]["date"],
    }


def evaluate_fatigue(metrics: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []
    actionable = True

    if metrics["days_active"] < thresholds["min_days_active"]:
        actionable = False
        flags.append("below_min_days_active")
    if metrics["total_impressions"] < thresholds["min_impressions_for_signal"]:
        actionable = False
        flags.append("below_min_impressions")

    decline = metrics.get("ctr_decline_pct")
    if decline is not None and decline <= -thresholds["ctr_decline_pct_7d"]:
        flags.append("ctr_declining")

    freq = metrics["frequency_7d"]
    if freq >= thresholds["frequency_critical"]:
        flags.append("frequency_critical")
    elif freq >= thresholds["frequency_warning"]:
        flags.append("frequency_warning")

    # CPL inflation is the outcome-level fatigue signal: an ad can hold its
    # CTR while the leads it buys get steadily more expensive, which CTR
    # decline alone never catches.
    cpl_inflation = metrics.get("cpl_inflation_pct")
    if cpl_inflation is not None:
        if cpl_inflation >= thresholds["cpl_inflation_critical_pct"]:
            flags.append("cpl_inflation_critical")
        elif cpl_inflation >= thresholds["cpl_inflation_warning_pct"]:
            flags.append("cpl_inflation_warning")

    # Determine the underlying severity from flags regardless of
    # whether the row passes the actionability floor — so downstream
    # consumers can distinguish "below floor with flags" from
    # "below floor with no signals at all" (both used to land in the
    # "ok" bucket).
    raw_severity = "ok"
    if ("frequency_critical" in flags
            or "cpl_inflation_critical" in flags
            or ("ctr_declining" in flags and "frequency_warning" in flags)
            or ("cpl_inflation_warning" in flags and "ctr_declining" in flags)):
        raw_severity = "critical"
    elif ("ctr_declining" in flags
            or "frequency_warning" in flags
            or "cpl_inflation_warning" in flags):
        raw_severity = "warning"

    if actionable:
        severity = raw_severity
    elif raw_severity != "ok":
        # Has fatigue signal but below the actionability floor (days
        # or impressions). Distinct bucket so summary counts don't
        # conflate this with truly-clean ads.
        severity = "below_floor"
    else:
        severity = "ok"

    return {"flags": flags, "severity": severity, "actionable": actionable}


def compute_winner_bleeder(rows_by_ad: dict[str, list[dict[str, Any]]],
                           thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank ads within their ad set by COST PER LEAD.

    Pre-pivot this ranked on CTR and spend share, which meant a lone ad in an
    ad set scored spend_share 1.0 and ctr_vs_avg 1.0 and was always labelled a
    "winner" regardless of whether it produced a single lead. Two changes fix
    that:

      1. Leads, not clicks, decide the label. CTR is retained as a reported
         diagnostic and as the tiebreaker when no ad in the set has leads yet.
      2. Comparative labels require at least two delivering peers. A lone ad
         has nothing to be better or worse than, so it is labelled None with
         `label_reason` explaining why, and is judged only against the
         absolute CPL target.
    """
    min_peers = thresholds["min_peers_for_comparison"]
    target_cpl = thresholds["target_cpl"]
    warn_mult = thresholds["cpl_warning_multiple"]

    by_adset: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    for ad_id, history in rows_by_ad.items():
        adset_id = history[-1].get("adset_id")
        if adset_id:
            by_adset[adset_id].append((ad_id, history))

    results: list[dict[str, Any]] = []
    for adset_id, ads in by_adset.items():
        ads_summary: list[dict[str, Any]] = []
        for ad_id, history in ads:
            spend = sum(r["spend"] for r in history)
            impr = sum(r["impressions"] for r in history)
            clicks = sum(r["clicks"] for r in history)
            leads = sum(row_leads(r) for r in history)
            ads_summary.append({
                "ad_id": ad_id,
                "ad_name": history[-1].get("ad_name"),
                "spend": spend,
                "impressions": impr,
                "leads": leads,
                "ctr": (clicks / impr) if impr else 0.0,
                "cpl": safe_cpl(spend, leads),
            })

        adset_spend = sum(a["spend"] for a in ads_summary)
        delivering = [a for a in ads_summary
                      if a["impressions"] >= thresholds["min_impressions_for_signal"]]
        # Ad-set CPL is pooled (total spend / total leads), not a mean of
        # per-ad CPLs, so a low-spend outlier can't drag the benchmark.
        pooled_spend = sum(a["spend"] for a in delivering)
        pooled_leads = sum(a["leads"] for a in delivering)
        adset_cpl = safe_cpl(pooled_spend, pooled_leads)
        ctrs = [a["ctr"] for a in delivering]
        adset_avg_ctr = statistics.mean(ctrs) if ctrs else 0.0
        peers_with_leads = sum(1 for a in delivering if a["leads"] > 0)

        for a in ads_summary:
            spend_share = (a["spend"] / adset_spend) if adset_spend else 0.0
            ctr_vs_avg = (a["ctr"] / adset_avg_ctr) if adset_avg_ctr else 0.0
            cpl_vs_adset = (round(a["cpl"] / adset_cpl, 3)
                            if a["cpl"] is not None and adset_cpl else None)
            cpl_vs_target = (round(a["cpl"] / target_cpl, 3)
                             if a["cpl"] is not None and target_cpl else None)

            label: str | None = None
            reason: str | None = None

            if a["impressions"] < thresholds["min_impressions_for_signal"]:
                reason = "below_impression_floor"
            elif a["leads"] == 0 and a["spend"] > 0:
                # Spending with nothing to show is decidable without peers.
                label = "bleeder"
                reason = "spend_without_leads"
            elif len(delivering) < min_peers or peers_with_leads < min_peers:
                # Not comparable — fall back to the absolute target.
                reason = "insufficient_peers"
                if cpl_vs_target is not None and cpl_vs_target >= warn_mult:
                    label = "bleeder"
                    reason = "cpl_above_target"
            elif cpl_vs_adset is not None:
                if cpl_vs_adset <= 1.0:
                    label = "winner"
                    reason = "cpl_at_or_below_adset"
                elif cpl_vs_adset >= thresholds["bleeder_cpl_vs_adset"]:
                    label = "bleeder"
                    reason = "cpl_above_adset"

            results.append({
                "adset_id": adset_id,
                "ad_id": a["ad_id"],
                "ad_name": a["ad_name"],
                "spend": round(a["spend"], 2),
                "spend_share": round(spend_share, 4),
                "leads": a["leads"],
                "cpl": a["cpl"],
                "cpl_vs_adset_avg": cpl_vs_adset,
                "cpl_vs_target": cpl_vs_target,
                "ctr": round(a["ctr"], 4),
                "ctr_vs_adset_avg": round(ctr_vs_avg, 3),
                "peers_delivering": len(delivering),
                "label": label,
                "label_reason": reason,
            })
    return results


def in_learning_phase(adset: dict[str, Any]) -> bool:
    info = adset.get("learning_stage_info") or {}
    status = (info.get("status") or "").upper()
    return status == "LEARNING"


def run(window_days: int) -> int:
    config = load_config()
    # Map new benchmarks.json schema → the keys this script's helpers expect.
    # The new schema separates "fatigue" thresholds from "daily_check" so the
    # same numbers don't have to live in two places. compute_signals.py is the
    # audit-trail layer — its winner/bleeder definitions are heuristic and not
    # authoritative; the daily-check skill computes the canonical version.
    fatigue_cfg = config["fatigue"]
    daily_cfg = config["daily_check"]
    fatigue_thresholds = {
        "min_days_active": fatigue_cfg["min_days_active"],
        "min_impressions_for_signal": fatigue_cfg["min_impressions"],
        "ctr_decline_pct_7d": fatigue_cfg["ctr_early_decline_pct"],
        "frequency_warning": fatigue_cfg["frequency_warning"],
        "frequency_critical": fatigue_cfg["frequency_critical"],
        "cpl_inflation_warning_pct": fatigue_cfg["cpl_inflation_warning_pct"],
        "cpl_inflation_critical_pct": fatigue_cfg["cpl_inflation_critical_pct"],
    }
    econ_cfg = config["lead_economics"]
    perf_thresholds = {
        "min_impressions_for_signal": fatigue_cfg["min_impressions"],
        # An ad set needs at least this many delivering, lead-producing ads
        # before winner/bleeder is a meaningful comparison.
        "min_peers_for_comparison": 2,
        # A bleeder costs at least this multiple of its ad set's pooled CPL.
        "bleeder_cpl_vs_adset": econ_cfg["cpl_warning_multiple"],
        "target_cpl": econ_cfg["target_cpl_dollars"],
        "cpl_warning_multiple": econ_cfg["cpl_warning_multiple"],
    }

    dates = load_snapshot_dates(window_days)
    if not dates:
        logging.warning("no snapshots found in %s — nothing to compute", SNAPSHOTS_DIR)
        # Still write empty derived files so consumers don't crash.
        empty_run(window_days, [])
        return 0

    logging.info("loading insights for %d date(s): %s → %s",
                 len(dates), dates[0], dates[-1])
    rows = load_ad_insights(dates)
    adsets = load_latest_adsets(dates)
    creatives = load_creatives()
    ad_to_creative = load_ad_to_creative(dates)
    by_ad = group_by_ad(rows)
    logging.info("computing signals for %d ad(s)", len(by_ad))

    fatigue_rows: list[dict[str, Any]] = []
    learning_skipped = 0
    for ad_id, history in by_ad.items():
        metrics = compute_ad_metrics(history)
        evaluation = evaluate_fatigue(metrics, fatigue_thresholds)
        adset_id = history[-1].get("adset_id")
        adset = adsets.get(adset_id, {})
        learning = in_learning_phase(adset)
        if learning:
            learning_skipped += 1
            evaluation["actionable"] = False
            evaluation["flags"].append("adset_in_learning")
        creative_id = ad_to_creative.get(ad_id)
        creative = creatives.get(creative_id, {}) if creative_id else {}
        fatigue_rows.append({
            "ad_id": ad_id,
            "ad_name": history[-1].get("ad_name"),
            "adset_id": adset_id,
            "adset_name": history[-1].get("adset_name"),
            "adset_effective_status": adset.get("effective_status"),
            "campaign_id": history[-1].get("campaign_id"),
            "campaign_name": history[-1].get("campaign_name"),
            "creative_id": creative_id,
            "creative_thumbnail_url": creative.get("thumbnail_url"),
            **metrics,
            **evaluation,
        })

    fatigue_rows.sort(
        key=lambda r: (r["severity"] != "critical",
                       r["severity"] != "warning",
                       r["severity"] != "below_floor",
                       -(r.get("total_impressions") or 0)),
    )

    winner_bleeder = compute_winner_bleeder(by_ad, perf_thresholds)

    severity_counts = {"critical": 0, "warning": 0, "below_floor": 0, "ok": 0}
    for r in fatigue_rows:
        severity_counts[r["severity"]] = severity_counts.get(r["severity"], 0) + 1

    summary = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "snapshot_dates": dates,
        "ad_count": len(by_ad),
        "adset_count": len({r.get("adset_id") for r in rows if r.get("adset_id")}),
        "campaign_count": len({r.get("campaign_id") for r in rows if r.get("campaign_id")}),
        "fatigue_severity_counts": severity_counts,
        "actionable_critical": [r["ad_id"] for r in fatigue_rows
                                if r["severity"] == "critical" and r["actionable"]],
        "actionable_warning": [r["ad_id"] for r in fatigue_rows
                               if r["severity"] == "warning" and r["actionable"]],
        "winners": [r["ad_id"] for r in winner_bleeder if r["label"] == "winner"],
        "bleeders": [r["ad_id"] for r in winner_bleeder if r["label"] == "bleeder"],
        "learning_phase_adsets_skipped": learning_skipped,
    }

    atomic_write_json(DERIVED_DIR / "fatigue_signals.json", {
        "computed_at": summary["computed_at"],
        "window_days": window_days,
        "rows": fatigue_rows,
    })
    atomic_write_json(DERIVED_DIR / "winner_bleeder.json", {
        "computed_at": summary["computed_at"],
        "window_days": window_days,
        "rows": winner_bleeder,
    })
    atomic_write_json(DERIVED_DIR / "summary.json", summary)

    logging.info("derived signals written: critical=%d warning=%d "
                 "below_floor=%d ok=%d",
                 severity_counts["critical"], severity_counts["warning"],
                 severity_counts["below_floor"], severity_counts["ok"])
    return 0


def empty_run(window_days: int, dates: list[str]) -> None:
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    summary = {
        "computed_at": now,
        "window_days": window_days,
        "snapshot_dates": dates,
        "ad_count": 0,
        "adset_count": 0,
        "campaign_count": 0,
        "fatigue_severity_counts": {"critical": 0, "warning": 0,
                                    "below_floor": 0, "ok": 0},
        "actionable_critical": [],
        "actionable_warning": [],
        "winners": [],
        "bleeders": [],
        "learning_phase_adsets_skipped": 0,
        "note": "No snapshots found; derived files initialized empty.",
    }
    atomic_write_json(DERIVED_DIR / "fatigue_signals.json",
                      {"computed_at": now, "window_days": window_days, "rows": []})
    atomic_write_json(DERIVED_DIR / "winner_bleeder.json",
                      {"computed_at": now, "window_days": window_days, "rows": []})
    atomic_write_json(DERIVED_DIR / "summary.json", summary)


def main(argv: list[str] | None = None) -> int:
    # rolling_window_days isn't a key in the new schema; default to 7 days
    # (matches the fatigue skill's "current 7-day rolling" window).
    default_window = 7
    parser = argparse.ArgumentParser(description="Compute derived signals from ad snapshots.")
    parser.add_argument("--window-days", type=int, default=default_window,
                        help=f"Rolling window for trend analysis (default: {default_window}).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(args.window_days)


if __name__ == "__main__":
    sys.exit(main())
