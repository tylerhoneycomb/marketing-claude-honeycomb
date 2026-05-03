#!/usr/bin/env python3
"""Classify ads as fatigued / early_fatigue / saturated / underperforming /
healthy by comparing current 7-day metrics to baselines.

Reads two inputs:
  --fetch       PATH to fetch_fatigue_data.py output
  --baselines   PATH to compute_baselines.py output
                (or both via stdin: pass concatenated as a single JSON
                {"fetch": ..., "baselines": ...})

Outputs JSON to stdout (a list of classifications for the skill to compose
Slack from), POSTs ALL classifications to ?action=fatigue-write for the
historical record, and pulls pending budget proposals via
?action=budget-queue-read to flag conflicts.
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

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import load_config  # noqa: E402

CURRENT_WINDOW_DAYS = 7  # last 7 days of the 14-day fetch


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def safe_div(num: float, den: float) -> float | None:
    return (num / den) if den else None


def aggregate(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    impressions = clicks = days = 0
    spend = 0.0
    freq_sum = 0.0
    freq_n = 0
    for r in rows:
        try:
            d = parse_date(r["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if d < start or d > end:
            continue
        impressions += int(r.get("impressions") or 0)
        clicks += int(r.get("clicks") or 0)
        spend += float(r.get("spend") or 0.0)
        f = float(r.get("frequency") or 0.0)
        if f > 0:
            freq_sum += f
            freq_n += 1
        days += 1
    ctr = safe_div(clicks, impressions)
    cpc = safe_div(spend, clicks)
    cpm = safe_div(spend, impressions)
    avg_freq = safe_div(freq_sum, freq_n)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": round(spend, 2),
        "ctr": round(ctr * 100, 4) if ctr is not None else None,
        "cpc": round(cpc, 4) if cpc is not None else None,
        "cpm": round(cpm * 1000, 4) if cpm is not None else None,
        "frequency": round(avg_freq, 3) if avg_freq is not None else None,
        "days": days,
    }


def pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return (current - baseline) / baseline * 100.0


def classify(metrics: dict[str, Any], baseline: dict[str, Any],
             freq_critical: float, freq_warning: float,
             ctr_fatigued_decline_pct: float, ctr_early_decline_pct: float,
             cpc_inflation_warning_pct: float) -> dict[str, Any]:
    """Apply the 5-class matrix. `metrics` = current 7d, `baseline` = peak."""
    ctr_current = metrics["ctr"]
    ctr_baseline = baseline.get("ctr_baseline")
    cpc_current = metrics["cpc"]
    cpc_baseline = baseline.get("cpc_baseline")
    frequency = metrics["frequency"] or 0.0

    ctr_change = pct_change(ctr_current, ctr_baseline)
    ctr_decline = -ctr_change if ctr_change is not None else None  # positive = decline
    cpc_change = pct_change(cpc_current, cpc_baseline)  # positive = inflation

    # Order matters: saturated > fatigued > early_fatigue > underperforming > healthy.
    if frequency >= freq_critical:
        classification = "saturated"
    elif (ctr_decline is not None and ctr_decline > ctr_fatigued_decline_pct
          and frequency >= freq_warning):
        classification = "fatigued"
    elif (ctr_decline is not None
          and ctr_early_decline_pct <= ctr_decline <= ctr_fatigued_decline_pct
          and frequency >= freq_warning):
        classification = "early_fatigue"
    elif (ctr_decline is not None and ctr_decline > ctr_fatigued_decline_pct
          and frequency < freq_warning):
        classification = "underperforming"
    else:
        classification = "healthy"

    return {
        "classification": classification,
        "ctr_baseline": ctr_baseline,
        "ctr_current": ctr_current,
        "ctr_decline_pct": round(ctr_decline, 2) if ctr_decline is not None else None,
        "frequency": frequency,
        "cpc_baseline": cpc_baseline,
        "cpc_current": cpc_current,
        "cpc_change_pct": round(cpc_change, 2) if cpc_change is not None else None,
    }


def fetch_pending_budget_proposals(exec_endpoint: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(exec_endpoint,
                            params={"action": "budget-queue-read"},
                            timeout=20)
    except requests.RequestException as exc:
        logging.warning("budget-queue-read failed: %s", exc)
        return []
    if resp.status_code != 200:
        logging.warning("budget-queue-read HTTP %d: %s",
                        resp.status_code, resp.text[:200])
        return []
    try:
        body = resp.json()
    except ValueError:
        return []
    return body.get("pending", []) if isinstance(body, dict) else []


def post_to_sheet(exec_endpoint: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        resp = requests.post(exec_endpoint,
                             params={"action": "fatigue-write"},
                             json={"rows": rows}, timeout=30)
    except requests.RequestException as exc:
        return {"posted": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"posted": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    try:
        body = resp.json()
    except ValueError:
        return {"posted": False, "error": "non-JSON response"}
    if isinstance(body, dict) and body.get("error"):
        return {"posted": False, "error": body["error"]}
    return {"posted": True, "written": (body or {}).get("written", len(rows))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify ad fatigue.")
    parser.add_argument("--fetch", required=True,
                        help="Path to fetch_fatigue_data.py output JSON.")
    parser.add_argument("--baselines", required=True,
                        help="Path to compute_baselines.py output JSON.")
    parser.add_argument("--no-sheet-write", action="store_true",
                        help="Skip POST to ?action=fatigue-write.")
    parser.add_argument("--no-budget-check", action="store_true",
                        help="Skip GET ?action=budget-queue-read.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    with open(args.fetch) as f:
        fetch_data = json.load(f)
    with open(args.baselines) as f:
        baselines_data = json.load(f)

    config = load_config()
    fatigue_cfg = config["fatigue"]
    campaign_type = config.get("campaign_defaults", {}).get("type", "prospecting")
    freq_critical = (fatigue_cfg["frequency_retargeting_critical"]
                     if campaign_type == "retargeting"
                     else fatigue_cfg["frequency_critical"])
    freq_warning = fatigue_cfg["frequency_warning"]
    min_impressions = fatigue_cfg["min_impressions"]
    min_days_active = fatigue_cfg["min_days_active"]
    exec_endpoint = os.environ.get("EXEC_ENDPOINT") or config["exec_endpoint"]

    until = parse_date(fetch_data["until"])
    current_start = until - timedelta(days=CURRENT_WINDOW_DAYS - 1)

    rows_by_ad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fetch_data["ads"]:
        if r.get("ad_id"):
            rows_by_ad[r["ad_id"]].append(r)

    ad_objects_by_id = {a["ad_id"]: a for a in fetch_data.get("ad_objects", [])
                         if a.get("ad_id")}
    creatives_by_ad = {c["ad_id"]: c for c in fetch_data.get("creatives", [])
                        if c.get("ad_id")}
    baselines = baselines_data.get("baselines", {})

    pending_proposals: list[dict[str, Any]] = []
    if not args.no_budget_check:
        pending_proposals = fetch_pending_budget_proposals(exec_endpoint)
    pending_increase_by_campaign: dict[str, dict[str, Any]] = {}
    for p in pending_proposals:
        cid = p.get("campaign_id")
        if not cid:
            continue
        if p.get("direction") == "increase":
            # Keep the most recent pending increase per campaign.
            if cid not in pending_increase_by_campaign:
                pending_increase_by_campaign[cid] = p

    classifications: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)
    today_iso = until.isoformat()

    for ad_id, ad_obj in ad_objects_by_id.items():
        eff = (ad_obj.get("effective_status") or "").upper()
        if eff != "ACTIVE":
            skipped["not_active"] += 1
            continue

        baseline = baselines.get(ad_id)
        if not baseline or baseline.get("ctr_baseline") is None:
            skipped["no_baseline"] += 1
            continue

        rows = rows_by_ad.get(ad_id, [])
        # Path C ads have no created_time, so days_active is None. Fall
        # back to the count of distinct dates with non-zero impressions in
        # the fetch window — a reasonable lower bound on active days.
        days_active = baseline.get("days_active")
        if days_active is None:
            active_dates = {r.get("date") for r in rows
                            if int(r.get("impressions") or 0) > 0}
            days_active = len(active_dates)
        if days_active < min_days_active:
            skipped["below_min_days_active"] += 1
            continue

        # Total impressions across the full 14-day fetch (not just current 7d).
        total_imps = sum(int(r.get("impressions") or 0) for r in rows)
        if total_imps < min_impressions:
            skipped["below_min_impressions"] += 1
            continue

        current = aggregate(rows, current_start, until)
        verdict = classify(
            current, baseline,
            freq_critical=freq_critical,
            freq_warning=freq_warning,
            ctr_fatigued_decline_pct=fatigue_cfg["ctr_fatigued_decline_pct"],
            ctr_early_decline_pct=fatigue_cfg["ctr_early_decline_pct"],
            cpc_inflation_warning_pct=fatigue_cfg["cpc_inflation_warning_pct"],
        )

        # Budget conflict only matters for fatigued / early_fatigue.
        conflict = None
        ad_campaign_id = ad_obj.get("campaign_id") or (
            rows[0].get("campaign_id") if rows else None)
        if (verdict["classification"] in ("fatigued", "early_fatigue")
                and ad_campaign_id in pending_increase_by_campaign):
            p = pending_increase_by_campaign[ad_campaign_id]
            conflict = (f"Pending budget INCREASE on {p.get('campaign_name') or ad_campaign_id} "
                        f"({p.get('change_pct'):+.1f}%) — "
                        f"consider pausing this ad before approval")

        creative = creatives_by_ad.get(ad_id, {})

        classifications.append({
            "ad_id": ad_id,
            "ad_name": ad_obj.get("ad_name") or (rows[0].get("ad_name") if rows else None),
            "campaign_id": ad_campaign_id,
            "campaign": (rows[0].get("campaign_name") if rows else None),
            "classification": verdict["classification"],
            "ctr_baseline": verdict["ctr_baseline"],
            "ctr_current": verdict["ctr_current"],
            "ctr_decline_pct": verdict["ctr_decline_pct"],
            "frequency": verdict["frequency"],
            "cpc_baseline": verdict["cpc_baseline"],
            "cpc_current": verdict["cpc_current"],
            "cpc_change_pct": verdict["cpc_change_pct"],
            "days_active": days_active,
            "baseline_type": baseline.get("baseline_type"),
            "baseline_since": baseline.get("baseline_since"),
            "baseline_until": baseline.get("baseline_until"),
            "headline": creative.get("title") or creative.get("body"),
            "thumbnail_url": creative.get("thumbnail_url"),
            "budget_conflict": conflict,
        })

    # Stats
    counts: dict[str, int] = defaultdict(int)
    for c in classifications:
        counts[c["classification"]] += 1

    # Sheet write — payload uses the `rows` envelope expected by handleFatigueWrite_.
    sheet_payload = [
        {
            "date": today_iso,
            "ad_id": c["ad_id"],
            "ad_name": c["ad_name"],
            "campaign": c["campaign"],
            "classification": c["classification"],
            "ctr_baseline": c["ctr_baseline"],
            "ctr_current": c["ctr_current"],
            "ctr_decline_pct": c["ctr_decline_pct"],
            "frequency": c["frequency"],
            "cpc_baseline": c["cpc_baseline"],
            "cpc_current": c["cpc_current"],
            "days_active": c["days_active"],
            "baseline_type": c["baseline_type"],
            "budget_conflict": c["budget_conflict"],
        }
        for c in classifications
    ]
    if args.no_sheet_write:
        sheet_write = {"posted": False, "skipped": True}
    elif sheet_payload:
        sheet_write = post_to_sheet(exec_endpoint, sheet_payload)
    else:
        sheet_write = {"posted": False, "skipped": True,
                        "note": "no classifications to write"}

    payload = {
        "date": today_iso,
        "since": fetch_data.get("since"),
        "until": fetch_data.get("until"),
        "stats": {
            "ads_evaluated": len(classifications),
            "by_classification": dict(counts),
            "skipped": dict(skipped),
            "pending_budget_proposals": len(pending_proposals),
            "pending_increases_with_conflict": sum(1 for c in classifications
                                                   if c["budget_conflict"]),
            "campaign_type": campaign_type,
        },
        "classifications": classifications,
        "sheet_write": sheet_write,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
