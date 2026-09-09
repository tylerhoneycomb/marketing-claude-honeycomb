#!/usr/bin/env python3
"""Compute the daily-check briefing from fetch_daily_data.py output.

Reads the fetch payload from stdin (or --input PATH), reads thresholds from
data/config/benchmarks.json, computes pacing/portfolio/winners/bleeders/
fatigue_flags/learning_phase/stale_creatives, POSTs the summary row to
?action=daily-check-write, and prints a structured JSON to stdout for the
skill (SKILL.md) to compose Slack output from.

Run:
    python3 fetch_daily_data.py > /tmp/daily.json
    python3 analyze_daily.py --input /tmp/daily.json

Or piped:
    python3 fetch_daily_data.py | python3 analyze_daily.py

Flags:
  --input PATH        read fetch payload from PATH instead of stdin
  --no-sheet-write    skip POST to ?action=daily-check-write
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
from lib.exec_api import get_spend_goal  # noqa: E402


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def week_bounds(d: date) -> tuple[date, date]:
    """Monday → Sunday for the ISO week containing d."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def safe_div(num: float, den: float) -> float | None:
    return (num / den) if den else None


# ─── Pacing ───────────────────────────────────────────────────────────────

def compute_pacing(campaigns: list[dict[str, Any]], until: date,
                   target: float, tolerance_pct: float) -> dict[str, Any]:
    """Spent so far this ISO week (Mon–yesterday inclusive) vs needed daily run-rate."""
    monday, _ = week_bounds(until)
    spent_this_week = 0.0
    yesterday_spend = 0.0
    for r in campaigns:
        try:
            d = parse_date(r["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if d < monday or d > until:
            continue
        spent_this_week += float(r.get("spend") or 0.0)
        if d == until:
            yesterday_spend += float(r.get("spend") or 0.0)

    days_remaining = max(0, 7 - (until - monday).days - 1)  # excludes today
    remaining_target = max(0.0, target - spent_this_week)
    remaining_daily = (remaining_target / days_remaining) if days_remaining > 0 else None

    status = "on_pace"
    if remaining_daily is not None and remaining_daily > 0:
        deviation = (yesterday_spend - remaining_daily) / remaining_daily * 100
        if deviation > tolerance_pct:
            status = "overspending"
        elif deviation < -tolerance_pct:
            status = "underspending"
    elif remaining_daily == 0 and yesterday_spend > 0:
        status = "overspending"

    return {
        "status": status,
        "yesterday_spend": round(yesterday_spend, 2),
        "remaining_daily_target": round(remaining_daily, 2) if remaining_daily else None,
        "weekly_target": target,
        "spent_this_week": round(spent_this_week, 2),
        "days_remaining": days_remaining,
        "week_start": monday.isoformat(),
    }


# ─── Aggregations (rollup last-7d daily rows into per-entity totals) ──────

def rollup_by(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """Sum daily metrics keyed by `key` (e.g. 'campaign_id', 'ad_id', 'adset_id')."""
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"spend": 0.0, "impressions": 0, "clicks": 0,
                 "reach": 0, "conversions": 0, "leads": 0,
                 "prequal_decisions": 0, "ic_conversions": 0,
                 "frequency_sum": 0.0, "frequency_n": 0}
    )
    names: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = r.get(key)
        if not k:
            continue
        agg = out[k]
        agg["spend"] += float(r.get("spend") or 0.0)
        agg["impressions"] += int(r.get("impressions") or 0)
        agg["clicks"] += int(r.get("clicks") or 0)
        agg["reach"] += int(r.get("reach") or 0)
        agg["conversions"] += int(r.get("conversions") or 0)
        agg["leads"] += int(r.get("leads", r.get("conversions")) or 0)
        agg["prequal_decisions"] += int(r.get("prequal_decisions") or 0)
        agg["ic_conversions"] += int(r.get("ic_conversions") or 0)
        f = float(r.get("frequency") or 0.0)
        if f > 0:
            agg["frequency_sum"] += f
            agg["frequency_n"] += 1
        if k not in names:
            names[k] = {
                "campaign_id": r.get("campaign_id"),
                "campaign_name": r.get("campaign_name"),
                "adset_id": r.get("adset_id"),
                "adset_name": r.get("adset_name"),
                "ad_id": r.get("ad_id"),
                "ad_name": r.get("ad_name"),
            }

    result: dict[str, dict[str, Any]] = {}
    for k, agg in out.items():
        ctr = safe_div(agg["clicks"], agg["impressions"])
        cpc = safe_div(agg["spend"], agg["clicks"])
        avg_freq = safe_div(agg["frequency_sum"], agg["frequency_n"])
        cpl = safe_div(agg["spend"], agg["leads"])
        cpicp = safe_div(agg["spend"], agg["ic_conversions"])
        result[k] = {
            **names[k],
            "spend": round(agg["spend"], 2),
            "impressions": agg["impressions"],
            "clicks": agg["clicks"],
            "reach": agg["reach"],
            "conversions": agg["conversions"],
            "leads": agg["leads"],
            "prequal_decisions": agg["prequal_decisions"],
            "ic_conversions": agg["ic_conversions"],
            "ctr": round(ctr * 100, 3) if ctr is not None else None,
            "cpc": round(cpc, 3) if cpc is not None else None,
            "frequency": round(avg_freq, 2) if avg_freq is not None else None,
            "cpl": round(cpl, 2) if cpl is not None else None,
            "cpicp": round(cpicp, 2) if cpicp is not None else None,
        }
    return result


# ─── Portfolio (campaigns sorted by CPL asc) ──────────────────────────────

def compute_portfolio(campaigns_rollup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for cid, agg in campaigns_rollup.items():
        rows.append({
            "campaign": agg.get("campaign_name") or cid,
            "spend": agg["spend"],
            "leads": agg["leads"],
            "cpl": agg["cpl"],
            "prequal_decisions": agg["prequal_decisions"],
            "ic_conversions": agg["ic_conversions"],
            "cpicp": agg["cpicp"],
            "ctr": agg["ctr"],
            "frequency": agg["frequency"],
        })
    # Best CPL first; campaigns with no leads sort last. Ordering keyed on
    # CPICP until 2026-09-09, which ranked on a metric that was None for
    # nearly every campaign and therefore produced an arbitrary order.
    rows.sort(key=lambda r: (r["cpl"] is None, r["cpl"] or float("inf")))
    return rows


# ─── Winners / Bleeders (ad-level) ────────────────────────────────────────

def compute_winners(ads_rollup: dict[str, dict[str, Any]],
                    min_leads: int, min_impressions: int) -> list[dict[str, Any]]:
    """Top ads by COST PER LEAD.

    This gated on leads but then ranked by CPC, so the "winners" were the ads
    that bought the cheapest clicks, not the cheapest leads — an ad with a
    great CPC and a poor click-to-lead rate outranked a genuinely efficient
    one. Ranking now uses the metric the gate implies.
    """
    eligible = [
        a for a in ads_rollup.values()
        if a["leads"] >= min_leads
        and a["impressions"] >= min_impressions
        and a.get("cpl") is not None
    ]
    eligible.sort(key=lambda a: a["cpl"])
    return [
        {
            "ad_name": a.get("ad_name"),
            "campaign": a.get("campaign_name"),
            "cpl": a["cpl"],
            "leads": a["leads"],
            "cpc": a["cpc"],
            "ctr": a["ctr"],
        }
        for a in eligible[:3]
    ]


def compute_bleeders(ads_rollup: dict[str, dict[str, Any]],
                     adsets_rollup: dict[str, dict[str, Any]],
                     min_impressions: int,
                     spend_share_min_pct: float,
                     ctr_vs_avg_max_pct: float,
                     cpl_vs_avg_min_mult: float) -> list[dict[str, Any]]:
    """Ads taking a meaningful share of their ad set's spend without earning it.

    An ad qualifies on either of two grounds, both requiring it to hold more
    than `spend_share_min_pct` of ad-set spend:

      - it produced no leads at all while spending, or
      - its CPL is at least `cpl_vs_avg_min_mult` times the ad-set CPL.

    CTR is retained as a fallback for ad sets where nothing has leads yet, so
    the check still says something during a cold start, but outcome beats
    proxy wherever outcome data exists.
    """
    candidates = []
    for ad in ads_rollup.values():
        adset_id = ad.get("adset_id")
        if not adset_id or ad["impressions"] < min_impressions:
            continue
        adset = adsets_rollup.get(adset_id)
        if not adset or not adset["spend"]:
            continue

        spend_share_pct = (ad["spend"] / adset["spend"]) * 100
        if spend_share_pct <= spend_share_min_pct:
            continue

        adset_cpl = adset.get("cpl")
        reason = None
        if ad["leads"] == 0 and ad["spend"] > 0:
            reason = "spend_without_leads"
        elif ad.get("cpl") is not None and adset_cpl:
            if ad["cpl"] >= adset_cpl * cpl_vs_avg_min_mult:
                reason = "cpl_above_adset"
        elif adset_cpl is None and ad["ctr"] is not None and adset["ctr"] is not None:
            # Nothing in this ad set has leads yet — fall back to CTR.
            if ad["ctr"] < adset["ctr"] * (ctr_vs_avg_max_pct / 100):
                reason = "ctr_below_adset_no_lead_data"
        if not reason:
            continue

        candidates.append({
            "ad_name": ad.get("ad_name"),
            "campaign": ad.get("campaign_name"),
            "reason": reason,
            "leads": ad["leads"],
            "cpl": ad.get("cpl"),
            "adset_cpl": adset_cpl,
            "ctr": ad["ctr"],
            "adset_avg_ctr": adset["ctr"],
            "spend_share_pct": round(spend_share_pct, 1),
        })
    # Worst first: no-lead spenders, then the most inflated CPL.
    candidates.sort(key=lambda r: (r["reason"] != "spend_without_leads",
                                   -(r["cpl"] or 0)))
    return candidates[:3]


# ─── Early fatigue flags (preview the fatigue-monitor skill) ──────────────

def compute_fatigue_flags(ad_daily_rows: list[dict[str, Any]],
                          until: date,
                          min_frequency: float,
                          min_impressions: int,
                          ctr_decline_pct: float) -> list[dict[str, Any]]:
    """Flag ads where 7d avg frequency >= min_frequency AND
    3-day CTR is >= ctr_decline_pct below prior 4-day CTR."""
    by_ad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in ad_daily_rows:
        ad_id = r.get("ad_id")
        if ad_id:
            by_ad[ad_id].append(r)

    recent_start = until - timedelta(days=2)  # 3 days: until-2, until-1, until
    flags: list[dict[str, Any]] = []
    for ad_id, rows in by_ad.items():
        rows = sorted(rows, key=lambda r: r["date"])
        total_imps = sum(int(r.get("impressions") or 0) for r in rows)
        if total_imps < min_impressions:
            continue
        freqs = [float(r.get("frequency") or 0) for r in rows if r.get("frequency")]
        avg_freq = sum(freqs) / len(freqs) if freqs else 0.0
        if avg_freq < min_frequency:
            continue

        recent_clicks = recent_imps = prior_clicks = prior_imps = 0
        for r in rows:
            d = parse_date(r["date"])
            imps = int(r.get("impressions") or 0)
            clicks = int(r.get("clicks") or 0)
            if d >= recent_start:
                recent_clicks += clicks
                recent_imps += imps
            else:
                prior_clicks += clicks
                prior_imps += imps
        recent_ctr = (recent_clicks / recent_imps * 100) if recent_imps else None
        prior_ctr = (prior_clicks / prior_imps * 100) if prior_imps else None
        if recent_ctr is None or prior_ctr is None or prior_ctr == 0:
            continue
        decline_pct = (prior_ctr - recent_ctr) / prior_ctr * 100
        if decline_pct < ctr_decline_pct:
            continue

        flags.append({
            "ad_name": rows[-1].get("ad_name"),
            "campaign": rows[-1].get("campaign_name"),
            "frequency": round(avg_freq, 2),
            "ctr_3d": round(recent_ctr, 3),
            "ctr_prior_4d": round(prior_ctr, 3),
            "ctr_decline_pct": round(decline_pct, 1),
        })
    flags.sort(key=lambda r: -r["ctr_decline_pct"])
    return flags


# ─── Learning-phase ad sets ───────────────────────────────────────────────

def compute_learning_phase(adset_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for a in adset_objects:
        info = a.get("learning_stage_info") or {}
        status = (info.get("status") or "").upper()
        if status == "LEARNING":
            out.append({
                "adset_name": a.get("adset_name"),
                "campaign_id": a.get("campaign_id"),
                "status": status,
            })
    return out


# ─── Stale creatives (ads active > N days) ────────────────────────────────

def compute_stale_creatives(ad_objects: list[dict[str, Any]], until: date,
                            warning_days: int) -> list[dict[str, Any]]:
    out = []
    cutoff = until - timedelta(days=warning_days)
    for a in ad_objects:
        ct = a.get("created_time")
        if not ct or (a.get("effective_status") or "").upper() != "ACTIVE":
            continue
        try:
            created = parse_date(ct)
        except (ValueError, TypeError):
            continue
        if created > cutoff:
            continue
        out.append({
            "ad_name": a.get("ad_name"),
            "campaign_id": a.get("campaign_id"),
            "days_active": (until - created).days,
            "created_time": created.isoformat(),
        })
    out.sort(key=lambda r: -r["days_active"])
    return out


# ─── Sheet write ──────────────────────────────────────────────────────────

def write_to_sheet(exec_endpoint: str, until_iso: str, summary: dict[str, Any]
                   ) -> dict[str, Any]:
    payload = {
        "row": {
            "date": until_iso,
            "pacing_status": summary["pacing"]["status"],
            "total_spend": summary["totals"]["spend"],
            "total_leads": summary["totals"]["leads"],
            "portfolio_cpl": summary["totals"]["cpl"],
            "total_icps": summary["totals"]["ic_conversions"],
            "portfolio_cpicp": summary["totals"]["cpicp"],
            "fatigue_flag_count": len(summary["fatigue_flags"]),
        }
    }
    try:
        resp = requests.post(exec_endpoint,
                             params={"action": "daily-check-write"},
                             json=payload, timeout=20)
    except requests.RequestException as exc:
        return {"posted": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"posted": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    try:
        body = resp.json()
    except ValueError:
        return {"posted": False, "error": "non-JSON response from /exec"}
    if isinstance(body, dict) and body.get("error"):
        return {"posted": False, "error": body["error"]}
    return {"posted": True, "written": (body or {}).get("written", 1)}


# ─── Main ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze daily-check fetch payload.")
    parser.add_argument("--input", default=None,
                        help="Read fetch payload from PATH (default: stdin).")
    parser.add_argument("--no-sheet-write", action="store_true",
                        help="Skip POST to ?action=daily-check-write.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.input:
        with open(args.input) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    config = load_config()
    pacing_cfg = config["pacing"]
    daily_cfg = config["daily_check"]
    fatigue_cfg = config["fatigue"]
    exec_endpoint = os.environ.get("EXEC_ENDPOINT") or config["exec_endpoint"]

    until = parse_date(data["until"])

    campaigns_rollup = rollup_by(data["campaigns"], "campaign_id")
    adsets_rollup = rollup_by(data["adsets"], "adset_id")
    ads_rollup = rollup_by(data["ads"], "ad_id")

    # Weekly spend GOAL is fetched live from /exec?action=get_spend_goal
    # so a dashboard update is reflected without a code change. The
    # benchmarks.json value is a fallback only (used when /exec is
    # unreachable). pacing_tolerance_pct stays static config — it's a
    # pacing-status sensitivity band, not the optimizer's dollar
    # tolerance.
    goal = get_spend_goal(
        exec_endpoint,
        fallback_target=pacing_cfg["weekly_spend_target_dollars"],
        fallback_tolerance=pacing_cfg.get("weekly_spend_tolerance_dollars", 500),
    )

    pacing = compute_pacing(
        data["campaigns"], until,
        goal["target_weekly_spend"],
        pacing_cfg["pacing_tolerance_pct"],
    )
    pacing["weekly_target_source"] = goal["source"]
    portfolio = compute_portfolio(campaigns_rollup)
    winners = compute_winners(
        ads_rollup,
        daily_cfg["winner_min_conversions"],
        daily_cfg["min_impressions_for_signal"],
    )
    bleeders = compute_bleeders(
        ads_rollup, adsets_rollup,
        daily_cfg["min_impressions_for_signal"],
        daily_cfg["bleeder_min_spend_share_pct"],
        daily_cfg["bleeder_ctr_vs_adset_avg_pct"],
        config["lead_economics"]["cpl_warning_multiple"],
    )
    fatigue_flags = compute_fatigue_flags(
        data["ads"], until,
        daily_cfg["early_fatigue_min_frequency"],
        daily_cfg["min_impressions_for_signal"],
        daily_cfg["early_fatigue_ctr_decline_pct"],
    )
    learning_phase = compute_learning_phase(data.get("adset_objects", []))
    stale_creatives = compute_stale_creatives(
        data.get("ad_objects", []), until,
        fatigue_cfg["creative_age_warning_days"],
    )

    total_spend = round(sum(c["spend"] for c in campaigns_rollup.values()), 2)
    total_leads = sum(c["leads"] for c in campaigns_rollup.values())
    total_prequal = sum(c["prequal_decisions"] for c in campaigns_rollup.values())
    total_cpl = round(total_spend / total_leads, 2) if total_leads else None
    total_ic = sum(c["ic_conversions"] for c in campaigns_rollup.values())
    total_cpicp = round(total_spend / total_ic, 2) if total_ic else None

    summary: dict[str, Any] = {
        "date": until.isoformat(),
        "since": data.get("since"),
        "until": data.get("until"),
        "pacing": pacing,
        "portfolio": portfolio,
        "winners": winners,
        "bleeders": bleeders,
        "fatigue_flags": fatigue_flags,
        "learning_phase": learning_phase,
        "stale_creatives": stale_creatives,
        "totals": {
            "spend": total_spend,
            "leads": total_leads,
            "cpl": total_cpl,
            "prequal_decisions": total_prequal,
            "ic_conversions": total_ic,
            "cpicp": total_cpicp,
        },
    }

    if args.no_sheet_write:
        summary["sheet_write"] = {"posted": False, "skipped": True}
    else:
        summary["sheet_write"] = write_to_sheet(exec_endpoint, until.isoformat(), summary)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
