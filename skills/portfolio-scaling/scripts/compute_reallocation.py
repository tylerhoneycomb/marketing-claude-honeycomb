#!/usr/bin/env python3
"""compute_reallocation.py — turn scaling_profiles.json into a budget proposal.

Reads:
  - data/derived/scaling_profiles.json (from compute_scaling_profiles.py)
  - data/config/benchmarks.json
  - Optional: data/creatives/categorizations.json + /exec creative_intelligence_log
              (for audience-action creative prescriptions; gracefully skipped
              if missing)

Pool mechanics:
  - Saturating + over-invested verticals contribute decreases sized by
    elasticity severity, capped by each campaign's remaining headroom and
    the $26/day effective floor (the $25 hard floor + 4% buffer protects
    one worst-case optimizer reduction cycle from breaching it).
  - Scalable verticals (primary) and stable verticals (secondary, 0.5x
    weight) absorb the pool, weighted by inverse CPICP.
  - The pool is bounded by [target - tolerance, target + tolerance] in
    weekly portfolio spend, biased toward target itself.
  - Net-positive pools above target raise knockdown_risk so the brief
    can flag it. We do NOT pre-deduct the expected 1% knockdown — that
    fights itself; the optimizer will act on the next cycle and the
    headroom math counts it via the normal path.

Writes: data/derived/reallocation.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import load_config  # noqa: E402

DERIVED_DIR = REPO_ROOT / "data" / "derived"
DEFAULT_PROFILES = DERIVED_DIR / "scaling_profiles.json"
DEFAULT_OUTPUT = DERIVED_DIR / "reallocation.json"
CREATIVE_CACHE_PATH = REPO_ROOT / "data" / "creatives" / "categorizations.json"

# $25 hard floor + 4% buffer. The buffer ensures one worst-case optimizer
# reduction cycle (4% reduction) won't push a campaign below the $25 floor.
CAMPAIGN_DAILY_MIN_CENTS = 2500


def floor_cents(buffer_pct: float) -> int:
    return int(round(CAMPAIGN_DAILY_MIN_CENTS * (1 + buffer_pct)))


def severity(r: float | None, threshold: float, modifier: float = 1.0) -> float:
    """Map elasticity r ∈ [threshold, 1.0] → severity ∈ [0, 1].
    `modifier` boosts (or dampens) severity for over-invested verticals.
    """
    if r is None:
        return 0.0
    abs_r = abs(r)
    if abs_r <= threshold:
        return 0.0
    raw = (abs_r - threshold) / max(1e-9, 1.0 - threshold)
    return min(1.0, max(0.0, raw * modifier))


def safe_inv(x: float | None) -> float:
    if x is None or x <= 0:
        return 0.0
    return 1.0 / x


def next_tuesday_midnight_utc(today: date) -> datetime:
    """Lockout expires at next Tuesday 00:00 UTC so the optimizer's
    Tuesday-morning cycle sees lockout already expired (per directive D)."""
    # weekday(): Mon=0 ... Tue=1 ... Sun=6
    days_ahead = (1 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return datetime.combine(today + timedelta(days=days_ahead),
                            datetime.min.time(), tzinfo=timezone.utc)


def fetch_json(url: str, params: dict[str, Any] | None = None,
               retries: int = 2, timeout: int = 30) -> Any:
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


def post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> Any:
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ─── Decrease pool ────────────────────────────────────────────────────

def compute_decreases(profiles: dict[str, Any],
                      benchmarks: dict[str, Any]) -> list[dict[str, Any]]:
    """For each over-invested + saturating vertical, propose decreases
    sized by severity * remaining_headroom, with floor protection.
    """
    sat_threshold = benchmarks["elasticity_saturating_threshold"]
    floor_buffer = benchmarks["campaign_floor_buffer_pct"]
    out: list[dict[str, Any]] = []
    floor = floor_cents(floor_buffer)

    for vertical, m in profiles["verticals"].items():
        cls = m.get("classification")
        if cls not in ("saturating", "over-invested"):
            continue
        modifier = 1.5 if cls == "over-invested" else 1.0
        sev = severity(m.get("elasticity_r"), sat_threshold, modifier)
        if sev <= 0:
            continue

        for cid in m.get("campaign_ids", []):
            cinfo = profiles["campaigns"].get(cid)
            if not cinfo:
                continue
            current_cents = cinfo["daily_budget_cents"]
            remaining = cinfo["weekly_remaining_pct"]
            if remaining <= 0:
                continue
            desired_pct = sev * remaining
            desired_cents = int(round(current_cents * desired_pct))
            # Floor protection.
            max_cuttable = max(0, current_cents - floor)
            actual_cents = min(desired_cents, max_cuttable)
            if actual_cents <= 0:
                continue
            actual_pct = actual_cents / current_cents
            out.append({
                "vertical": vertical,
                "campaign_id": cid,
                "campaign_name": cinfo["campaign_name"],
                "current_daily_cents": current_cents,
                "change_cents": -actual_cents,
                "change_pct": -round(actual_pct, 4),
                "post_change_cents": current_cents - actual_cents,
                "classification": cls,
                "elasticity_r": m.get("elasticity_r"),
                "remaining_headroom_pct": round(remaining, 4),
                "reason": (
                    f"{cls} (r={m.get('elasticity_r')}); "
                    f"severity {sev:.2f} * remaining headroom "
                    f"{remaining:.2%} → -{actual_pct:.2%}"
                ),
            })
    return out


# ─── Increase pool ────────────────────────────────────────────────────

def compute_increases(profiles: dict[str, Any],
                      benchmarks: dict[str, Any],
                      pool_cents: int) -> list[dict[str, Any]]:
    """Allocate pool_cents across scalable + stable verticals weighted
    by inverse CPICP. Stable gets 0.5x weight (secondary priority).
    """
    if pool_cents <= 0:
        return []

    weights: dict[str, float] = {}
    for vertical, m in profiles["verticals"].items():
        cls = m.get("classification")
        cpicp = m.get("cpicp")
        if cls == "scalable" and cpicp:
            weights[vertical] = safe_inv(cpicp)
        elif cls == "stable" and cpicp:
            weights[vertical] = safe_inv(cpicp) * 0.5
    total_w = sum(weights.values())
    if total_w <= 0:
        return []

    out: list[dict[str, Any]] = []
    for vertical, w in weights.items():
        m = profiles["verticals"][vertical]
        vertical_pool = int(round(pool_cents * (w / total_w)))
        if vertical_pool <= 0:
            continue

        # Distribute vertical_pool across the vertical's campaigns,
        # proportionally by current daily budget, capped per-campaign by
        # weekly_remaining_pct.
        cids = m.get("campaign_ids", [])
        total_budget = sum(
            profiles["campaigns"].get(cid, {}).get("daily_budget_cents", 0)
            for cid in cids
        )
        if total_budget <= 0:
            continue

        for cid in cids:
            cinfo = profiles["campaigns"].get(cid)
            if not cinfo:
                continue
            current = cinfo["daily_budget_cents"]
            remaining = cinfo["weekly_remaining_pct"]
            if remaining <= 0:
                continue
            share = current / total_budget
            desired_cents = int(round(vertical_pool * share))
            cap_cents = int(round(current * remaining))
            actual_cents = min(desired_cents, cap_cents)
            if actual_cents <= 0:
                continue
            actual_pct = actual_cents / current
            out.append({
                "vertical": vertical,
                "campaign_id": cid,
                "campaign_name": cinfo["campaign_name"],
                "current_daily_cents": current,
                "change_cents": actual_cents,
                "change_pct": round(actual_pct, 4),
                "post_change_cents": current + actual_cents,
                "classification": m.get("classification"),
                "cpicp": m.get("cpicp"),
                "remaining_headroom_pct": round(remaining, 4),
                "allocation_weight_reason": (
                    f"inverse-CPICP weight {w:.4f} of {total_w:.4f}"
                    + (" (secondary, 0.5x for stable)"
                       if m.get("classification") == "stable" else "")
                ),
            })
    return out


def absorption_capacity(profiles: dict[str, Any]) -> int:
    """Maximum cents-per-day that scalable + stable verticals could absorb
    without exceeding any campaign's weekly_remaining_pct.
    """
    cap = 0
    for vertical, m in profiles["verticals"].items():
        if m.get("classification") not in ("scalable", "stable"):
            continue
        for cid in m.get("campaign_ids", []):
            cinfo = profiles["campaigns"].get(cid)
            if not cinfo:
                continue
            cap += int(round(cinfo["daily_budget_cents"]
                             * cinfo["weekly_remaining_pct"]))
    return cap


# ─── Tolerance band enforcement ───────────────────────────────────────

def enforce_tolerance(decreases: list[dict[str, Any]],
                      increases: list[dict[str, Any]],
                      profiles: dict[str, Any]) -> tuple[
                          list[dict[str, Any]], list[dict[str, Any]],
                          int, int, int, bool]:
    """Bound post-change portfolio total to [target - tolerance,
    target + tolerance]. Returns (decreases, increases, freed_cents,
    allocated_cents, post_change_total_cents, knockdown_risk).

    Priority:
      1. Stay within tolerance band — hard constraint.
      2. Spend at least target (the baseline). Drop below only if
         scalable verticals have no remaining capacity.
      3. Prefer net-positive over net-negative: tolerance is slack to
         use if scalable verticals have headroom.

    Convergence: single-pass proportional scaling. The math is exact
    pre-rounding (scaled allocated/freed exactly cancels the
    excess/deficit), but `int(round(...))` of per-item change_cents may
    leave the post-change portfolio total ±N cents outside the band,
    where N ≤ number of items being scaled. At Honeycomb's scale
    (typically 10-15 affected campaigns, $5-10/day individual changes),
    that's at most $0.07/week of slop on a $10,000 target — below the
    tolerance Tyler cares about. Don't iterate; the rounding noise
    won't compound meaningfully on a second pass.
    """
    portfolio = profiles["portfolio"]
    current = portfolio["current_total_daily_cents"]
    target_daily = portfolio["target_weekly_spend"] * 100 / 7
    tol_daily = portfolio["weekly_spend_tolerance"] * 100 / 7
    min_allowed = target_daily - tol_daily
    max_allowed = target_daily + tol_daily

    freed = sum(-d["change_cents"] for d in decreases)
    allocated = sum(i["change_cents"] for i in increases)
    proposed = current - freed + allocated

    # Above ceiling: scale increases down proportionally.
    if proposed > max_allowed:
        excess = proposed - max_allowed
        keep_factor = max(0.0, (allocated - excess) / max(1, allocated))
        increases = [
            _scale_change(i, keep_factor) for i in increases
        ]
        increases = [i for i in increases if i["change_cents"] > 0]
        allocated = sum(i["change_cents"] for i in increases)
        proposed = current - freed + allocated

    # Below floor: scale decreases down proportionally.
    if proposed < min_allowed:
        deficit = min_allowed - proposed
        keep_factor = max(0.0, (freed - deficit) / max(1, freed))
        decreases = [
            _scale_change(d, keep_factor) for d in decreases
        ]
        decreases = [d for d in decreases if d["change_cents"] < 0]
        freed = sum(-d["change_cents"] for d in decreases)
        proposed = current - freed + allocated

    knockdown_risk = proposed > target_daily
    return decreases, increases, int(freed), int(allocated), int(proposed), knockdown_risk


def _scale_change(item: dict[str, Any], factor: float) -> dict[str, Any]:
    """Multiply an item's change_cents by `factor`. Recomputes change_pct
    and post_change_cents. Used to thin the pool when a band is breached.
    """
    new_cents = int(round(item["change_cents"] * factor))
    current = item["current_daily_cents"]
    new_pct = (new_cents / current) if current else 0.0
    out = dict(item)
    out["change_cents"] = new_cents
    out["change_pct"] = round(new_pct, 4)
    out["post_change_cents"] = current + new_cents
    return out


# ─── Audience actions for new-audience-needed verticals ───────────────

def load_creative_cache_safely() -> dict[str, Any] | None:
    """Optional dependency — return None on any failure path."""
    try:
        if not CREATIVE_CACHE_PATH.exists():
            logging.info("Creative cache missing at %s; audience actions "
                         "will omit creative_prescription",
                         CREATIVE_CACHE_PATH)
            return None
        with CREATIVE_CACHE_PATH.open() as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        logging.warning("Creative cache load failed: %s", exc)
        return None


def fetch_creative_intelligence_log(exec_url: str) -> list[dict[str, Any]] | None:
    """Optional dependency — graceful skip on any failure (handler may not
    exist yet; Session 1 of the portfolio-scaling rollout does not ship a
    creative-intelligence-read /exec action)."""
    try:
        body = fetch_json(exec_url,
                          {"action": "creative-intelligence-read"},
                          retries=1)
    except (RuntimeError, requests.RequestException) as exc:
        logging.info("creative-intelligence-read fetch skipped: %s", exc)
        return None
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        if body.get("error"):
            logging.info("creative-intelligence-read error: %s", body["error"])
            return None
        rows = body.get("rows")
        return rows if isinstance(rows, list) else None
    return None


def compose_audience_actions(profiles: dict[str, Any],
                             ci_log: list[dict[str, Any]] | None,
                             creative_cache: dict[str, Any] | None,
                             ) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for vertical, m in profiles["verticals"].items():
        if not m.get("new_audience_needed"):
            continue
        prescription = None
        creative_source = None
        if ci_log and creative_cache:
            for row in reversed(ci_log):
                if str(row.get("vertical", "")).lower() == vertical:
                    body = row.get("top_body_text") or ""
                    style = row.get("top_visual_style") or ""
                    if body or style:
                        parts = []
                        if body:
                            parts.append(f'Top body: "{body[:120]}"')
                        if style:
                            parts.append(f"Visual style: {style}")
                        prescription = ". ".join(parts)
                        creative_source = "creative_intelligence_cache"
                        break
        diagnosis_parts = [
            f"frequency={m.get('avg_frequency')} ({m.get('frequency_trend')})",
            f"CPM trend={m.get('cpm_trend')}",
            f"r={m.get('elasticity_r')}",
        ]
        if m.get("high_spend_cpl_degradation_pct") is not None:
            diagnosis_parts.append(
                f"CPL degradation high vs low spend "
                f"weeks: {m['high_spend_cpl_degradation_pct']:.0%}"
            )
        out.append({
            "vertical": vertical,
            "diagnosis": "; ".join(diagnosis_parts),
            "action": (f"Duplicate {vertical} ad set with broader "
                       f"targeting. Keep original at current budget."),
            "creative_prescription": prescription,
            "creative_source": creative_source,
        })
    return out


# ─── Orchestration ────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--exec-url", default=os.environ.get("EXEC_ENDPOINT"))
    p.add_argument("--write-log", action="store_true",
                   help="POST scaling_log rows to /exec?action=scaling-write")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config()
    benchmarks = config["scaling"]
    exec_url = args.exec_url or config["exec_endpoint"]

    profiles_path = Path(args.profiles)
    if not profiles_path.exists():
        logging.error("scaling_profiles.json not found at %s — "
                      "run compute_scaling_profiles.py first", profiles_path)
        return 2
    profiles = json.loads(profiles_path.read_text())

    today = datetime.fromisoformat(profiles["today"]).date() \
        if isinstance(profiles.get("today"), str) else \
        datetime.now(timezone.utc).date()

    # ─── Decreases first, then increases sized to the freed pool ──────
    decreases = compute_decreases(profiles, benchmarks)
    initial_freed = sum(-d["change_cents"] for d in decreases)

    # Initial allocation pool = freed + tolerance ceiling room. Lets
    # net-positive flow if scalable verticals have headroom AND we're
    # under the target+tolerance ceiling.
    portfolio = profiles["portfolio"]
    current = portfolio["current_total_daily_cents"]
    target_daily = portfolio["target_weekly_spend"] * 100 / 7
    tol_daily = portfolio["weekly_spend_tolerance"] * 100 / 7
    headroom_above_current = (target_daily + tol_daily) - current
    # If we're already at or above target, increases come purely from
    # the freed pool. If we're below target, we can also use the gap.
    initial_pool = int(round(initial_freed + max(0, headroom_above_current)))

    increases = compute_increases(profiles, benchmarks, initial_pool)

    # Enforce the tolerance band; this may scale either side down.
    decreases, increases, freed, allocated, proposed, knockdown_risk = \
        enforce_tolerance(decreases, increases, profiles)

    net_change = allocated - freed
    net_type = (
        "zero_sum" if net_change == 0 else
        ("net_positive" if net_change > 0 else "net_negative")
    )

    # ─── Audience actions ─────────────────────────────────────────────
    creative_cache = load_creative_cache_safely()
    ci_log = (fetch_creative_intelligence_log(exec_url)
              if creative_cache else None)
    audience_actions = compose_audience_actions(profiles, ci_log, creative_cache)

    affected_campaigns = sorted({
        item["campaign_id"]
        for item in (decreases + increases)
    })
    lockout_until = next_tuesday_midnight_utc(today)

    output = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "today": today.isoformat(),
        "lockout_until": lockout_until.isoformat(),
        "affected_campaign_ids": affected_campaigns,
        "pool": {
            "freed_daily_cents": freed,
            "allocated_daily_cents": allocated,
            "net_change_daily_cents": net_change,
            "net_change_type": net_type,
            "portfolio_current_daily_cents": current,
            "portfolio_post_change_daily_cents": proposed,
            "portfolio_post_change_weekly_dollars": round(proposed / 100 * 7, 2),
            "target_weekly_dollars": portfolio["target_weekly_spend"],
            "tolerance_weekly_dollars": portfolio["weekly_spend_tolerance"],
            "knockdown_risk": knockdown_risk,
        },
        "decreases": decreases,
        "increases": increases,
        "audience_actions": audience_actions,
    }

    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, indent=2, default=str))
    logging.info("Wrote %s — freed=%d allocated=%d net=%d (%s) "
                 "knockdown_risk=%s audience_actions=%d",
                 args.output, freed, allocated, net_change, net_type,
                 knockdown_risk, len(audience_actions))

    # ─── Optional: write summary rows to scaling_log via /exec ────────
    if args.write_log:
        rows = compose_scaling_log_rows(profiles, output, today)
        if rows:
            try:
                resp = post_json(
                    exec_url + "?action=scaling-write",
                    {"rows": rows},
                )
                logging.info("scaling-write response: %s", resp)
            except (RuntimeError, requests.RequestException) as exc:
                logging.warning("scaling-write failed: %s", exc)

    # Stdout summary for the agent prompt.
    summary = {
        "decreases": len(decreases),
        "increases": len(increases),
        "freed_dollars_daily": round(freed / 100, 2),
        "allocated_dollars_daily": round(allocated / 100, 2),
        "net_change_dollars_daily": round(net_change / 100, 2),
        "net_change_type": net_type,
        "knockdown_risk": knockdown_risk,
        "lockout_until": lockout_until.isoformat(),
        "audience_actions": [a["vertical"] for a in audience_actions],
        "affected_campaign_ids": affected_campaigns,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


def compose_scaling_log_rows(profiles: dict[str, Any],
                             reallocation: dict[str, Any],
                             today: date) -> list[dict[str, Any]]:
    """One row per vertical: classification + key metrics + whether the
    vertical contributed to or received from the reallocation pool.
    """
    contributed = {d["vertical"] for d in reallocation["decreases"]}
    received = {i["vertical"] for i in reallocation["increases"]}
    rows: list[dict[str, Any]] = []
    for vertical, m in profiles["verticals"].items():
        rows.append({
            "date": today.isoformat(),
            "vertical": vertical,
            "classification": m.get("classification"),
            "confidence": m.get("confidence"),
            "elasticity_r": m.get("elasticity_r"),
            "ic_rate": m.get("ic_rate"),
            "cpicp": m.get("cpicp"),
            "spend_share_pct": m.get("spend_share_pct"),
            "avg_frequency": m.get("avg_frequency"),
            "frequency_trend": m.get("frequency_trend"),
            "cpm_trend": m.get("cpm_trend"),
            "new_audience_needed": m.get("new_audience_needed"),
            "weeks_with_conversions": m.get("weeks_with_conversions"),
            "contributed_to_pool": vertical in contributed,
            "received_from_pool": vertical in received,
        })
    return rows


if __name__ == "__main__":
    sys.exit(main())
