#!/usr/bin/env python3
"""Pipeline health checks for the Honeycomb ads system.

Runs five checks, posts the results to the `pipeline_health` Sheet tab, and
prints structured JSON to stdout. The skill (SKILL.md) reads the JSON and
composes the Slack message — but the Sheet write is the script's job, so it
happens deterministically every run.

Checks:
  1. data_freshness     — most recent date in rolling_data vs expected
  2. meta_token         — debug_token: validity + expiry
  3. funnel_conversions — quality + subtype custom conversions exist, are
                          unarchived, and report last_fired_time
  4. dashboard_endpoint — /exec?action=rollup returns valid JSON
  5. snapshot_volume    — newest ad-level snapshot has insight rows, and
                          reports its lead total + spend (the primary tier)

The primary tier (`leads`) is a standard pixel action, not a custom
conversion, so it never appears in `customconversions` — its health signal
is the lead count in snapshot_volume, not funnel_conversions.

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

# This script is deliberately standalone — it is the checker that runs when
# other things are broken, so it imports nothing from scripts/lib. That is
# why the shared secret is read here rather than via
# scripts/lib/exec_api.exec_key(), which is the canonical definition for
# every other caller. Keep the env var name in step with it.
EXEC_SECRET_ENV_VAR = "EXEC_SHARED_SECRET"


def exec_key() -> str:
    """Shared secret for side-effecting /exec actions (e.g. health-write)."""
    return os.environ.get(EXEC_SECRET_ENV_VAR, "")


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


def check_funnel_conversions(token: str, account_id: str, api_version: str,
                             config: dict[str, Any]) -> dict[str, Any]:
    """Verify every configured custom conversion still exists and is live.

    Checks the quality tier and each subtype. A missing or archived QUALITY
    conversion is a FAIL (it feeds reported metrics); a missing or archived
    SUBTYPE is at most a WARN, since subtypes are reported-only and may
    legitimately retire. Also surfaces `last_fired_time` so a conversion
    that silently stopped firing is visible instead of passing on mere
    existence — the failure mode that let IC drop to n=1 through August
    2026 without any alert.

    The detail string always leads with the quality tier and trails the
    subtypes under an explicit "(reported only)" label, so a subtype problem
    never reads as a performance alert for that subtype. The primary tier
    (leads) is a pixel action, not a custom conversion — see
    check_snapshot_volume for its signal.
    """
    name = "funnel_conversions"
    conv = config.get("conversions") or {}
    expected: list[tuple[str, str, bool]] = []  # (id, label, is_required)

    quality = conv.get("quality") or {}
    if quality.get("custom_conversion_id"):
        expected.append((str(quality["custom_conversion_id"]),
                         quality.get("metric") or "quality", True))
    for key, spec in (conv.get("subtypes") or {}).items():
        if isinstance(spec, dict) and spec.get("custom_conversion_id"):
            expected.append((str(spec["custom_conversion_id"]), key, False))

    if not expected:
        return {"name": name, "status": "WARN",
                "detail": "no custom conversions configured in benchmarks.json"}

    url = f"https://graph.facebook.com/{api_version}/{account_id}/customconversions"
    try:
        resp = requests.get(url, params={
            "fields": "id,name,is_archived,last_fired_time",
            "limit": 100,
            "access_token": token,
        }, timeout=15)
    except requests.RequestException as exc:
        return {"name": name, "status": "FAIL",
                "detail": f"could not reach Meta: {exc}"}

    if resp.status_code != 200:
        return {"name": name, "status": "FAIL",
                "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    by_id = {str(c.get("id")): c for c in resp.json().get("data", [])}
    quality_parts: list[str] = []            # always first in the detail
    subtype_problems: list[str] = []
    subtype_notes: list[tuple[str, str]] = []  # (last_fired, label)
    status = "PASS"

    for cid, label, required in expected:
        found = by_id.get(cid)
        if not found:
            problem = "not found in account"
        elif found.get("is_archived"):
            problem = "is ARCHIVED"
        else:
            problem = ""

        if required:
            if problem:
                quality_parts.append(f"{label}: {cid} {problem}")
                status = "FAIL"
            else:
                last_fired = (found.get("last_fired_time") or "never")[:10]
                quality_parts.append(f"{label} last fired {last_fired}")
            continue

        # Subtypes are reported-only: a problem is a tracking-config issue,
        # never more than WARN, and never allowed to lead the line.
        if problem:
            subtype_problems.append(f"subtype {label} (reported only) {problem} ({cid})")
            if status == "PASS":
                status = "WARN"
        else:
            last_fired = (found.get("last_fired_time") or "never")[:10]
            subtype_notes.append((last_fired, label))

    parts = list(quality_parts) + subtype_problems
    if subtype_notes:
        # Most recently fired first, so the subtype the audience actually
        # converts to leads the note rather than alphabetical order.
        ordered = sorted(subtype_notes, key=lambda n: (n[0] != "never", n[0]), reverse=True)
        parts.append("subtypes (reported only): "
                     + ", ".join(f"{label}={fired}" for fired, label in ordered))
    return {"name": name, "status": status, "detail": " | ".join(parts)}


def check_snapshot_volume(config: dict[str, Any]) -> dict[str, Any]:
    """Guard the empty-snapshot failure mode and report the primary tier.

    On 2026-08-15 the pipeline committed `ad_insights.json` as `[]` while
    `ads.json` held hundreds of objects, and every check still passed. This
    fails when the newest snapshot has ad objects but no insight rows, and
    warns when insight rows fall under the configured floor.

    It is also the only health signal for leads: the primary tier is a pixel
    action that `customconversions` cannot report on, so the lead total and
    spend from the snapshot rows are carried in every detail string. If
    `pipeline_health.zero_lead_spend_floor_usd` is configured, spend at or
    above it with zero leads is a WARN — the lead-side mirror of the
    empty-snapshot case. Without that key the lead total is reported only.
    """
    name = "snapshot_volume"
    snapshots = REPO_ROOT / "data" / "snapshots"
    if not snapshots.is_dir():
        return {"name": name, "status": "WARN", "detail": "no data/snapshots directory"}

    dated = sorted(d for d in snapshots.iterdir()
                   if d.is_dir() and len(d.name) == 10 and d.name[4] == "-")
    if not dated:
        return {"name": name, "status": "WARN", "detail": "no snapshots on disk"}

    latest = dated[-1]
    health_cfg = config.get("pipeline_health") or {}
    floor = int(health_cfg.get("min_expected_insight_rows", 1))
    zero_lead_floor = health_cfg.get("zero_lead_spend_floor_usd")
    # Field name comes from the funnel config; `conversions` is the
    # pre-pivot alias still present on snapshots written before 2026-09-09
    # (scripts/lib/meta.py keeps it as a deprecated alias — stay in step).
    lead_field = ((config.get("conversions") or {}).get("primary") or {}).get("field") or "leads"

    def _load(filename: str) -> list[Any] | None:
        path = latest / filename
        if not path.exists():
            return None
        try:
            rows = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return rows if isinstance(rows, list) else None

    insight_rows = _load("ad_insights.json")
    ad_rows = _load("ads.json")

    if insight_rows is None:
        return {"name": name, "status": "FAIL",
                "detail": f"{latest.name}: ad_insights.json missing or unreadable"}
    insights = len(insight_rows)
    ads = len(ad_rows) if ad_rows is not None else None

    leads = 0
    spend = 0.0
    for row in insight_rows:
        if not isinstance(row, dict):
            continue
        try:
            leads += int(row.get(lead_field, row.get("conversions", 0)) or 0)
            spend += float(row.get("spend", 0) or 0)
        except (TypeError, ValueError):
            continue
    lead_note = f"{leads} leads on ${spend:,.2f} spend"

    if ads and insights == 0:
        return {"name": name, "status": "FAIL",
                "detail": f"{latest.name}: 0 insight rows against {ads} ad objects "
                          f"— pull returned no delivery data"}
    if insights < floor:
        return {"name": name, "status": "WARN",
                "detail": f"{latest.name}: {insights} insight row(s), below floor of {floor}, "
                          f"{lead_note}"}
    if zero_lead_floor is not None and leads == 0 and spend >= float(zero_lead_floor):
        return {"name": name, "status": "WARN",
                "detail": f"{latest.name}: {insights} insight row(s), {lead_note} "
                          f"— lead pipeline may have stopped firing"}
    return {"name": name, "status": "PASS",
            "detail": f"{latest.name}: {insights} insight row(s), {ads} ad object(s), "
                      f"{lead_note}"}


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
            json={"rows": rows, "key": exec_key()},
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
        check_funnel_conversions(token, account_id, api_version, config),
        check_dashboard_endpoint(exec_endpoint,
                                 health_cfg["endpoint_timeout_seconds"]),
        check_snapshot_volume(config),
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
