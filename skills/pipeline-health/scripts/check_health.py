#!/usr/bin/env python3
"""Pipeline health checks for the Honeycomb ads system.

Runs four checks, posts the results to the `pipeline_health` Sheet tab, and
prints structured JSON to stdout. The skill (SKILL.md) reads the JSON and
composes the Slack message — but the Sheet write is the script's job, so it
happens deterministically every run.

Checks:
  1. data_freshness    — most recent date in rolling_data vs expected
  2. meta_token        — debug_token: validity + expiry
  3. ic_conversion_event — IC custom conversion exists in the account
  4. dashboard_endpoint — /exec?action=rollup returns valid JSON

Environment:
  META_ACCESS_TOKEN  required
  EXEC_ENDPOINT      optional override; defaults to benchmarks.json `exec_endpoint`

Flags:
  --no-sheet-write   Skip the POST to /exec?action=health-write (useful for
                     dry-runs and local development).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "data" / "config" / "benchmarks.json"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def expected_data_date(now: datetime, tz: ZoneInfo, daily_pull_hour: int = 7) -> date:
    """The date `rolling_data` should have as of `now` in `tz`.

    The campaign-level pipeline pulls yesterday's data each day at 7 AM ET.
    Before that pull, the freshest expected row is two days ago.
    """
    local = now.astimezone(tz)
    if local.hour < daily_pull_hour:
        return (local - timedelta(days=2)).date()
    return (local - timedelta(days=1)).date()


def weekday_gap(latest: date, expected: date) -> int:
    """Number of weekdays missed strictly after `latest` through `expected`.

    - latest >= expected → 0 (we have the expected day or later)
    - latest = expected - 1 weekday → 1 weekday missed
    - latest = expected - 2 weekdays → 2 missed
    Weekend days (Sat/Sun) don't count toward the gap.
    """
    if latest >= expected:
        return 0
    count = 0
    cursor = latest + timedelta(days=1)
    while cursor <= expected:
        if cursor.weekday() < 5:  # Mon-Fri
            count += 1
        cursor += timedelta(days=1)
    return count


def parse_iso_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def check_data_freshness(exec_endpoint: str, tz: ZoneInfo, max_gap: int) -> dict[str, Any]:
    name = "data_freshness"
    expected = expected_data_date(datetime.now(tz), tz)
    try:
        resp = requests.get(exec_endpoint, params={"action": "rolling-latest-date"},
                            timeout=15)
    except requests.RequestException as exc:
        return {"name": name, "status": "FAIL",
                "detail": f"could not reach exec endpoint: {exc}"}

    if resp.status_code != 200:
        return {"name": name, "status": "FAIL",
                "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    try:
        body = resp.json()
    except ValueError:
        return {"name": name, "status": "FAIL",
                "detail": f"non-JSON response: {resp.text[:200]}"}

    latest_str = (body or {}).get("latest_date")
    if not latest_str:
        return {"name": name, "status": "FAIL",
                "detail": f"endpoint returned no latest_date: {body}"}

    try:
        latest = parse_iso_date(latest_str)
    except ValueError:
        return {"name": name, "status": "FAIL",
                "detail": f"unparseable latest_date: {latest_str}"}

    gap = weekday_gap(latest, expected)
    detail = f"latest data: {latest.isoformat()}, expected: {expected.isoformat()}"

    if gap == 0:
        return {"name": name, "status": "PASS", "detail": detail}
    if gap <= max_gap:
        return {"name": name, "status": "WARN",
                "detail": f"{detail} ({gap} weekday behind)"}
    return {"name": name, "status": "FAIL",
            "detail": f"{detail} ({gap} weekdays behind)"}


def check_meta_token(token: str, api_version: str, tz: ZoneInfo,
                     warning_days: int) -> dict[str, Any]:
    name = "meta_token"
    url = f"https://graph.facebook.com/{api_version}/debug_token"
    try:
        resp = requests.get(url, params={"input_token": token,
                                          "access_token": token}, timeout=15)
    except requests.RequestException as exc:
        return {"name": name, "status": "FAIL",
                "detail": f"could not reach Meta: {exc}"}

    if resp.status_code != 200:
        return {"name": name, "status": "FAIL",
                "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    body = resp.json().get("data", {})
    if not body.get("is_valid"):
        return {"name": name, "status": "FAIL",
                "detail": f"token is not valid: {body.get('error', {}).get('message', 'no error message')}"}

    expires_at = body.get("expires_at")
    if not expires_at:  # 0 or missing means never expires (system user token)
        return {"name": name, "status": "PASS",
                "detail": "valid, no expiry (system user token)"}

    expires_dt = datetime.fromtimestamp(expires_at, tz=tz)
    days_left = (expires_dt.date() - datetime.now(tz).date()).days
    if days_left <= 0:
        return {"name": name, "status": "FAIL",
                "detail": f"token expired on {expires_dt.date().isoformat()}"}
    if days_left <= warning_days:
        return {"name": name, "status": "WARN",
                "detail": f"expires in {days_left} days "
                          f"(regenerate before {expires_dt.date().isoformat()})"}
    return {"name": name, "status": "PASS",
            "detail": f"valid, expires in {days_left} days"}


def check_ic_conversion_event(token: str, account_id: str, api_version: str,
                              expected_id: str) -> dict[str, Any]:
    name = "ic_conversion_event"
    url = f"https://graph.facebook.com/{api_version}/{account_id}/customconversions"
    try:
        resp = requests.get(url, params={"fields": "id,name",
                                          "access_token": token}, timeout=15)
    except requests.RequestException as exc:
        return {"name": name, "status": "FAIL",
                "detail": f"could not reach Meta: {exc}"}

    if resp.status_code != 200:
        return {"name": name, "status": "FAIL",
                "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    items = resp.json().get("data", [])
    for c in items:
        if str(c.get("id")) == str(expected_id):
            return {"name": name, "status": "PASS",
                    "detail": f"custom conversion {expected_id} ('{c.get('name')}') exists"}

    return {"name": name, "status": "FAIL",
            "detail": f"custom conversion {expected_id} not found "
                      f"in account {account_id} ({len(items)} conversions checked)"}


def check_dashboard_endpoint(exec_endpoint: str, timeout_s: int) -> dict[str, Any]:
    name = "dashboard_endpoint"
    started = datetime.now()
    try:
        # `rollup` returns weekly_rollup rows — the action the dashboard
        # itself hits most heavily, so this is a representative health
        # signal. (Earlier this was `leaderboard`, but that action doesn't
        # actually exist in handleDashboardApi_; the dashboard builds its
        # leaderboard view client-side from rollup data.)
        resp = requests.get(exec_endpoint, params={"action": "rollup"},
                            timeout=timeout_s)
    except requests.Timeout:
        return {"name": name, "status": "FAIL",
                "detail": f"timed out after {timeout_s}s"}
    except requests.RequestException as exc:
        return {"name": name, "status": "FAIL",
                "detail": f"request error: {exc}"}

    elapsed = (datetime.now() - started).total_seconds()

    if resp.status_code != 200:
        return {"name": name, "status": "FAIL",
                "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    try:
        body = resp.json()
    except ValueError:
        snippet = resp.text[:120].replace("\n", " ")
        return {"name": name, "status": "FAIL",
                "detail": f"non-JSON response (got HTML?): {snippet}"}

    if isinstance(body, dict) and body.get("error"):
        return {"name": name, "status": "FAIL",
                "detail": f"endpoint returned error: {body['error']}"}

    return {"name": name, "status": "PASS",
            "detail": f"valid JSON in {elapsed:.1f}s"}


def write_to_sheet(exec_endpoint: str, today_local: str,
                   checks: list[dict[str, Any]]) -> dict[str, Any]:
    """POST one row per check to ?action=health-write. Best-effort — a
    network error here does not invalidate the JSON we already computed."""
    rows = [{
        "date": today_local,
        "check": c["name"],
        "status": c["status"],
        "detail": c["detail"],
    } for c in checks]
    try:
        resp = requests.post(
            exec_endpoint,
            params={"action": "health-write"},
            json={"rows": rows},
            timeout=20,
        )
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
    return {"posted": True, "written": (body or {}).get("written", len(rows))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pipeline-health checks.")
    parser.add_argument("--no-sheet-write", action="store_true",
                        help="Skip POST to ?action=health-write (dry-run mode).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    account_id = os.environ.get("META_AD_ACCOUNT_ID") or config["account"]["id"]
    api_version = config["account"]["meta_api_version"]
    tz = ZoneInfo(config["account"]["timezone"])
    exec_endpoint = os.environ.get("EXEC_ENDPOINT") or config["exec_endpoint"]
    ic_id = config["ic_tracking"]["custom_conversion_id"]
    health_cfg = config["pipeline_health"]

    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        sys.stderr.write("ERROR: META_ACCESS_TOKEN is not set\n")
        return 2

    today_local = datetime.now(tz).date().isoformat()
    checks = [
        check_data_freshness(exec_endpoint, tz,
                             health_cfg["data_freshness_max_gap_weekdays"]),
        check_meta_token(token, api_version, tz,
                         health_cfg["token_warning_days"]),
        check_ic_conversion_event(token, account_id, api_version, ic_id),
        check_dashboard_endpoint(exec_endpoint,
                                 health_cfg["endpoint_timeout_seconds"]),
    ]

    payload: dict[str, Any] = {"date": today_local, "checks": checks}
    if args.no_sheet_write:
        payload["sheet_write"] = {"posted": False, "skipped": True}
    else:
        payload["sheet_write"] = write_to_sheet(exec_endpoint, today_local, checks)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
