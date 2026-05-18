"""Canonical Python accessor for the Apps Script `/exec` web API.

The Apps Script Web App is the runtime source of truth for the weekly
spend goal + tolerance: the dashboard approval flow writes them to
Script Properties (`DASHBOARD_TARGET_WEEKLY_SPEND` /
`DASHBOARD_WEEKLY_SPEND_TOLERANCE`) and `/exec?action=get_spend_goal`
reads them back. Every Python skill that needs the live goal should go
through `get_spend_goal()` here rather than reading the static
`data/config/benchmarks.json` (which the dashboard never updates).
"""

from __future__ import annotations

import logging
from typing import Any

import requests


def fetch_json(url: str, params: dict[str, Any] | None = None,
               retries: int = 3, timeout: int = 30) -> Any:
    """GET `url` and parse JSON, retrying transient failures.

    Raises RuntimeError when all attempts are exhausted.
    """
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


def get_spend_goal(exec_url: str, *, fallback_target: float,
                   fallback_tolerance: float,
                   retries: int = 3, timeout: int = 30) -> dict[str, Any]:
    """Return the live weekly spend goal + tolerance from `/exec`.

    Calls `?action=get_spend_goal`, which reflects the dashboard-managed
    Script Properties (or the Apps Script hardcoded defaults when no
    override is set). On any failure — unreachable endpoint, non-JSON
    body, missing fields — logs a warning and returns the supplied
    fallbacks tagged `source="fallback_unreachable"` so callers never
    crash and the staleness is visible downstream.

    Returns: {"target_weekly_spend": float,
              "weekly_spend_tolerance": float,
              "source": str}
    `source` is the upstream value (`script_property_override` |
    `hardcoded_default`) on success, or `fallback_unreachable`.
    """
    try:
        body = fetch_json(exec_url, {"action": "get_spend_goal"},
                          retries=retries, timeout=timeout)
    except RuntimeError as exc:
        logging.warning("get_spend_goal: /exec unreachable (%s) — "
                        "using fallbacks target=%s tolerance=%s",
                        exc, fallback_target, fallback_tolerance)
        return {
            "target_weekly_spend": float(fallback_target),
            "weekly_spend_tolerance": float(fallback_tolerance),
            "source": "fallback_unreachable",
        }

    if not isinstance(body, dict):
        logging.warning("get_spend_goal: unexpected response %r — "
                        "using fallbacks", body)
        return {
            "target_weekly_spend": float(fallback_target),
            "weekly_spend_tolerance": float(fallback_tolerance),
            "source": "fallback_unreachable",
        }

    target = body.get("target_weekly_spend")
    tolerance = body.get("weekly_spend_tolerance")
    if target is None or tolerance is None:
        logging.warning("get_spend_goal: response missing fields "
                        "(target=%r tolerance=%r) — using fallbacks",
                        target, tolerance)
        return {
            "target_weekly_spend": float(fallback_target),
            "weekly_spend_tolerance": float(fallback_tolerance),
            "source": "fallback_unreachable",
        }

    return {
        "target_weekly_spend": float(target),
        "weekly_spend_tolerance": float(tolerance),
        "source": str(body.get("source") or "unknown"),
    }
