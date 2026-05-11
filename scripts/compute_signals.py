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

    ctr_7d = statistics.mean(ctrs) if ctrs else 0.0
    freq_7d = statistics.mean(freqs) if freqs else 0.0
    ctr_slope = linear_trend_slope(ctrs)

    if len(ctrs) >= 4:
        baseline = statistics.mean(ctrs[: len(ctrs) // 2])
        recent = statistics.mean(ctrs[len(ctrs) // 2 :])
        ctr_decline_pct = pct_change(recent, baseline)
    else:
        ctr_decline_pct = None

    return {
        "days_active": len(history),
        "total_impressions": sum(impressions),
        "total_clicks": sum(clicks),
        "total_spend": round(spend, 2),
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

    severity = "ok"
    if actionable:
        if "frequency_critical" in flags or ("ctr_declining" in flags and "frequency_warning" in flags):
            severity = "critical"
        elif "ctr_declining" in flags or "frequency_warning" in flags:
            severity = "warning"

    return {"flags": flags, "severity": severity, "actionable": actionable}


def compute_winner_bleeder(rows_by_ad: dict[str, list[dict[str, Any]]],
                           thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank ads within their ad set by CTR and spend share."""
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
            ctr = (clicks / impr) if impr else 0.0
            ads_summary.append({
                "ad_id": ad_id,
                "ad_name": history[-1].get("ad_name"),
                "spend": spend,
                "impressions": impr,
                "ctr": ctr,
            })
        adset_spend = sum(a["spend"] for a in ads_summary)
        ctrs = [a["ctr"] for a in ads_summary if a["impressions"] > 0]
        adset_avg_ctr = statistics.mean(ctrs) if ctrs else 0.0

        for a in ads_summary:
            spend_share = (a["spend"] / adset_spend) if adset_spend else 0.0
            ctr_vs_avg = (a["ctr"] / adset_avg_ctr) if adset_avg_ctr else 0.0
            label = None
            if a["impressions"] >= thresholds["min_impressions_for_signal"]:
                if spend_share >= thresholds["winner_spend_share_min"] and ctr_vs_avg >= 1.0:
                    label = "winner"
                elif ctr_vs_avg <= thresholds["bleeder_ctr_vs_adset_avg"]:
                    label = "bleeder"
            results.append({
                "adset_id": adset_id,
                "ad_id": a["ad_id"],
                "ad_name": a["ad_name"],
                "spend": round(a["spend"], 2),
                "spend_share": round(spend_share, 4),
                "ctr": round(a["ctr"], 4),
                "ctr_vs_adset_avg": round(ctr_vs_avg, 3),
                "label": label,
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
    }
    perf_thresholds = {
        "bleeder_ctr_vs_adset_avg": daily_cfg["bleeder_ctr_vs_adset_avg_pct"] / 100.0,
        "winner_spend_share_min": daily_cfg["bleeder_min_spend_share_pct"] / 100.0,
        "min_impressions_for_signal": fatigue_cfg["min_impressions"],
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
                       -(r.get("total_impressions") or 0)),
    )

    winner_bleeder = compute_winner_bleeder(by_ad, perf_thresholds)

    severity_counts = {"critical": 0, "warning": 0, "ok": 0}
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

    logging.info("derived signals written: critical=%d warning=%d ok=%d",
                 severity_counts["critical"], severity_counts["warning"],
                 severity_counts["ok"])
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
        "fatigue_severity_counts": {"critical": 0, "warning": 0, "ok": 0},
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
