#!/usr/bin/env python3
"""Classify ads as fatigued / early_fatigue / saturated / underperforming /
healthy by comparing current 7-day metrics to baselines.

Leads and CPL are the headline numbers on every row: each classification
carries what the ad's leads cost in the current window vs its baseline, and
CPL inflation (fatigue.cpl_inflation_*_pct) is a classification input
alongside CTR decline and frequency. CTR / CPC stay as diagnostics.

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
from lib.exec_api import exec_key  # noqa: E402
from lib.meta import load_config  # noqa: E402

CURRENT_WINDOW_DAYS = 7  # last 7 days of the 14-day fetch

# Slack section order. Within a section: budget conflicts first, then
# spend-with-zero-leads, then CPL descending, then spend descending.
SEVERITY_ORDER = ("fatigued", "early_fatigue", "saturated", "underperforming", "healthy")


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def safe_div(num: float, den: float) -> float | None:
    return (num / den) if den else None


def row_leads(r: dict[str, Any]) -> int:
    """Lead count for one insight row. `leads` is canonical; rows normalized
    before the lead pivot only carry the deprecated `conversions` alias."""
    if r.get("leads") is not None:
        return int(r["leads"] or 0)
    return int(r.get("conversions") or 0)


def aggregate(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    impressions = clicks = days = 0
    leads = prequal = ic = 0
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
        leads += row_leads(r)
        prequal += int(r.get("prequal_decisions") or 0)
        ic += int(r.get("ic_conversions") or 0)
        f = float(r.get("frequency") or 0.0)
        if f > 0:
            freq_sum += f
            freq_n += 1
        days += 1
    ctr = safe_div(clicks, impressions)
    cpc = safe_div(spend, clicks)
    cpm = safe_div(spend, impressions)
    cpl = safe_div(spend, leads)  # None when the window bought no leads
    avg_freq = safe_div(freq_sum, freq_n)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": round(spend, 2),
        "leads": leads,
        "prequal_decisions": prequal,
        "ic_conversions": ic,
        "cpl": round(cpl, 2) if cpl is not None else None,
        "ctr": round(ctr * 100, 4) if ctr is not None else None,
        "cpc": round(cpc, 4) if cpc is not None else None,
        "cpm": round(cpm * 1000, 4) if cpm is not None else None,
        "frequency": round(avg_freq, 3) if avg_freq is not None else None,
        "days": days,
    }


def fmt_money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return (current - baseline) / baseline * 100.0


def classify(metrics: dict[str, Any], baseline: dict[str, Any],
             freq_critical: float, freq_warning: float,
             ctr_fatigued_decline_pct: float, ctr_early_decline_pct: float,
             cpl_inflation_warning_pct: float,
             cpl_inflation_critical_pct: float) -> dict[str, Any]:
    """Apply the 5-class matrix. `metrics` = current 7d, `baseline` = peak."""
    ctr_current = metrics["ctr"]
    ctr_baseline = baseline.get("ctr_baseline")
    cpc_current = metrics["cpc"]
    cpc_baseline = baseline.get("cpc_baseline")
    cpl_current = metrics["cpl"]
    cpl_baseline = baseline.get("cpl_baseline")
    frequency = metrics["frequency"] or 0.0

    ctr_change = pct_change(ctr_current, ctr_baseline)
    ctr_decline = -ctr_change if ctr_change is not None else None  # positive = decline
    cpc_change = pct_change(cpc_current, cpc_baseline)  # positive = inflation
    # CPL is only comparable when both windows bought at least one lead —
    # cpl is None otherwise, so the CPL rules stay silent rather than
    # firing off a zero-lead denominator (same guard as compute_signals.py).
    cpl_change = pct_change(cpl_current, cpl_baseline)  # positive = inflation

    ctr_fatigued = ctr_decline is not None and ctr_decline > ctr_fatigued_decline_pct
    ctr_early = (ctr_decline is not None
                 and ctr_early_decline_pct <= ctr_decline <= ctr_fatigued_decline_pct)
    cpl_critical = cpl_change is not None and cpl_change >= cpl_inflation_critical_pct
    cpl_warning = cpl_change is not None and cpl_change >= cpl_inflation_warning_pct

    # Which rules fired, so the brief can say WHY without re-deriving it.
    signals: list[str] = []
    if frequency >= freq_critical:
        signals.append("frequency_critical")
    elif frequency >= freq_warning:
        signals.append("frequency_warning")
    if ctr_fatigued:
        signals.append("ctr_fatigued")
    elif ctr_early:
        signals.append("ctr_early")
    if cpl_critical:
        signals.append("cpl_critical")
    elif cpl_warning:
        signals.append("cpl_warning")

    # Order matters: saturated > fatigued > early_fatigue > underperforming > healthy.
    # CPL inflation is the outcome-level signal: an ad can hold its CTR while
    # the leads it buys get steadily more expensive.
    if frequency >= freq_critical:
        classification = "saturated"
    elif (ctr_fatigued and frequency >= freq_warning) or cpl_critical:
        classification = "fatigued"
    elif (ctr_early or cpl_warning) and frequency >= freq_warning:
        classification = "early_fatigue"
    elif ctr_fatigued and frequency < freq_warning:
        classification = "underperforming"
    else:
        classification = "healthy"

    return {
        "classification": classification,
        "signals": signals,
        "leads_baseline": baseline.get("leads_baseline"),
        "cpl_baseline": cpl_baseline,
        "cpl_current": cpl_current,
        "cpl_change_pct": round(cpl_change, 2) if cpl_change is not None else None,
        "ctr_baseline": ctr_baseline,
        "ctr_current": ctr_current,
        "ctr_decline_pct": round(ctr_decline, 2) if ctr_decline is not None else None,
        "frequency": frequency,
        "cpc_baseline": cpc_baseline,
        "cpc_current": cpc_current,
        "cpc_change_pct": round(cpc_change, 2) if cpc_change is not None else None,
    }


def budget_conflict_line(p: dict[str, Any], campaign_id: str | None,
                         current: dict[str, Any],
                         verdict: dict[str, Any]) -> str:
    """Lead-first warning for a fatiguing ad whose campaign has a pending
    budget INCREASE. Never echoes `signal_reasons` — stale optimizer rows
    carry pre-pivot IC framing."""
    campaign = p.get("campaign_name") or campaign_id
    try:
        change_pct = float(p.get("change_pct") or 0)
    except (TypeError, ValueError):
        change_pct = 0.0
    try:
        change_per_day = float(p.get("change_cents") or 0) / 100.0
    except (TypeError, ValueError):
        change_per_day = 0.0
    source = p.get("source") or "optimizer"
    sign = "-" if change_per_day < 0 else "+"
    head = (f"Pending budget INCREASE on {campaign} "
            f"({change_pct:+.1f}%, {sign}${abs(change_per_day):,.2f}/day, {source})")

    leads = current["leads"]
    if leads:
        context = f"this ad bought {leads} leads at CPL {fmt_money(current['cpl'])}"
        if verdict["cpl_change_pct"] is not None:
            context += (f" (baseline {fmt_money(verdict['cpl_baseline'])}, "
                        f"{verdict['cpl_change_pct']:+.0f}%)")
    else:
        context = f"this ad spent {fmt_money(current['spend'])} with 0 leads"
    return (f"{head} — {context} in the last {CURRENT_WINDOW_DAYS} days; "
            f"consider pausing before approval")


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
                             json={"rows": rows, "key": exec_key()},
                             timeout=30)
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
    # Surfaced in stats so the brief's "vs $N target" comes from config.
    target_cpl = config.get("lead_economics", {}).get("target_cpl_dollars")

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
    # Keep the NEWEST pending increase per campaign. budget-queue-read walks
    # the append-only sheet top-down (oldest first), and optimizer rows
    # queued before the 2026-09-09 pause never expire, so "first match"
    # would pin a stale proposal over a live strategic one.
    pending_increase_by_campaign: dict[str, dict[str, Any]] = {}
    for p in pending_proposals:
        cid = p.get("campaign_id")
        if not cid or p.get("direction") != "increase":
            continue
        stamp = str(p.get("created_at") or p.get("analysis_date") or "")
        prior = pending_increase_by_campaign.get(cid)
        prior_stamp = str(prior.get("created_at") or prior.get("analysis_date") or "") if prior else ""
        if prior is None or stamp >= prior_stamp:
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
            cpl_inflation_warning_pct=fatigue_cfg["cpl_inflation_warning_pct"],
            cpl_inflation_critical_pct=fatigue_cfg["cpl_inflation_critical_pct"],
        )

        # Budget conflict only matters for fatigued / early_fatigue.
        conflict = None
        ad_campaign_id = ad_obj.get("campaign_id") or (
            rows[0].get("campaign_id") if rows else None)
        if (verdict["classification"] in ("fatigued", "early_fatigue")
                and ad_campaign_id in pending_increase_by_campaign):
            conflict = budget_conflict_line(
                pending_increase_by_campaign[ad_campaign_id],
                ad_campaign_id, current, verdict)

        creative = creatives_by_ad.get(ad_id, {})

        classifications.append({
            "ad_id": ad_id,
            "ad_name": ad_obj.get("ad_name") or (rows[0].get("ad_name") if rows else None),
            "campaign_id": ad_campaign_id,
            "campaign": (rows[0].get("campaign_name") if rows else None),
            "classification": verdict["classification"],
            "signals": verdict["signals"],
            # Lead economics for the current 7d window — the headline line.
            "spend_current": current["spend"],
            "leads_current": current["leads"],
            "cpl_current": verdict["cpl_current"],
            "leads_baseline": verdict["leads_baseline"],
            "cpl_baseline": verdict["cpl_baseline"],
            "cpl_change_pct": verdict["cpl_change_pct"],
            "zero_lead_spend": current["spend"] > 0 and current["leads"] == 0,
            # Secondary only: reported, never a sort key or threshold.
            "prequal_current": current["prequal_decisions"],
            "ic_current": current["ic_conversions"],
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

    # Slack order: severity section, conflicts first, spend-with-zero-leads
    # next, then most expensive leads first. Replaces Meta return order.
    def sort_key(c: dict[str, Any]) -> tuple:
        cpl = c["cpl_current"]
        return (
            SEVERITY_ORDER.index(c["classification"]),
            0 if c["budget_conflict"] else 1,
            0 if c["zero_lead_spend"] else 1,
            -cpl if cpl is not None else float("inf"),
            -(c["spend_current"] or 0.0),
        )
    classifications.sort(key=sort_key)

    # Stats
    counts: dict[str, int] = defaultdict(int)
    for c in classifications:
        counts[c["classification"]] += 1
    spend_7d = round(sum(c["spend_current"] for c in classifications), 2)
    leads_7d = sum(c["leads_current"] for c in classifications)
    cpl_7d = safe_div(spend_7d, leads_7d)

    # Sheet write — payload uses the `rows` envelope expected by
    # handleFatigueWrite_, which reads keys by NAME and ignores unknown ones.
    # Legacy keys stay as-is; the lead keys are additive.
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
            "spend_current": c["spend_current"],
            "leads_current": c["leads_current"],
            "cpl_current": c["cpl_current"],
            "leads_baseline": c["leads_baseline"],
            "cpl_baseline": c["cpl_baseline"],
            "cpl_change_pct": c["cpl_change_pct"],
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
            "non_healthy": sum(1 for c in classifications
                               if c["classification"] != "healthy"),
            "by_classification": dict(counts),
            # 7d lead economics across evaluated ads — the brief's headline.
            "spend_7d": spend_7d,
            "leads_7d": leads_7d,
            "cpl_7d": round(cpl_7d, 2) if cpl_7d is not None else None,
            "target_cpl_dollars": target_cpl,
            "prequal_7d": sum(c["prequal_current"] for c in classifications),
            "ic_7d": sum(c["ic_current"] for c in classifications),
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
