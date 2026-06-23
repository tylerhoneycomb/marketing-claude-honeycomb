#!/usr/bin/env python3
"""Deterministic reporter for pipeline-health check output.

`check_health.py` runs the four checks, writes the `pipeline_health` Sheet
rows, and prints structured JSON. This script consumes that JSON and does the
non-reasoning presentation work that used to be handled by an LLM
(`claude-code-action`) in the retired `agent-pipeline-health.yml` workflow:

  1. Print a human-readable terminal summary (all four checks + Sheet line).
  2. On any WARN/FAIL, compose the plain-text Slack alert (FAIL lines first,
     then WARN, `detail` strings verbatim) and POST it to SLACK_WEBHOOK_URL —
     but only if that env var is set and non-empty (silent on full PASS).
  3. Write a one-line status to --status-file for the issue-#48 comment, e.g.
       "PASS 4/0/0"
       "WARN 3/1/0  meta_token: expires in 12 days (regenerate before ...)"
       "FAIL 2/0/2  dashboard_endpoint: timed out after 25s"

None of this requires a model — the output shape is fully specified by
SKILL.md. Reporting is best-effort: a Slack/format hiccup never changes the
checks (already written to the Sheet by check_health.py) and never fails the
host job. Exit code is always 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

SEVERITY_ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2}


def load_payload(path: str | None) -> dict[str, Any]:
    if path and path != "-":
        with open(path) as f:
            return json.load(f)
    return json.load(sys.stdin)


def counts(checks: list[dict[str, Any]]) -> tuple[int, int, int]:
    p = sum(1 for c in checks if c.get("status") == "PASS")
    w = sum(1 for c in checks if c.get("status") == "WARN")
    f = sum(1 for c in checks if c.get("status") == "FAIL")
    return p, w, f


def overall_status(n_pass: int, n_warn: int, n_fail: int) -> str:
    if n_fail:
        return "FAIL"
    if n_warn:
        return "WARN"
    return "PASS"


def print_terminal_summary(payload: dict[str, Any]) -> None:
    """Mirror SKILL.md 'Output — Interactive' shape. Always show all checks."""
    date = payload.get("date", "?")
    checks = payload.get("checks", [])
    print(f"Pipeline Health — {date}\n")
    for c in checks:
        print(f"[{c.get('status')}] {c.get('name')} — {c.get('detail')}")
    sw = payload.get("sheet_write", {})
    if sw.get("skipped"):
        print("\nSheet log: skipped (--no-sheet-write)")
    elif sw.get("posted"):
        print(f"\nSheet log: {sw.get('written', '?')} rows written to pipeline_health")
    else:
        print(f"\nSheet log: NOT WRITTEN — {sw.get('error', 'unknown error')}")


def slack_lines(payload: dict[str, Any]) -> list[str]:
    """Non-PASS checks as 'STATUS: detail' lines, FAIL before WARN. Adds a
    WARN line when the historical Sheet log write failed."""
    checks = sorted(payload.get("checks", []),
                    key=lambda c: SEVERITY_ORDER.get(c.get("status"), 9))
    lines = [f"{c['status']}: {c['detail']}"
             for c in checks if c.get("status") in ("WARN", "FAIL")]
    sw = payload.get("sheet_write", {})
    if not sw.get("posted") and not sw.get("skipped"):
        lines.append(f"WARN: pipeline_health Sheet log not written — "
                     f"{sw.get('error', 'unknown error')}")
    return lines


def post_to_slack(webhook: str, date: str, lines: list[str]) -> None:
    # Lazy import so the reporter (terminal summary + status file) still runs
    # in environments without `requests` and without a webhook configured.
    import requests

    text = "⚠️ Pipeline Health — {}\n\n{}".format(date, "\n".join(lines))
    try:
        resp = requests.post(webhook, json={"text": text}, timeout=15)
        if resp.status_code >= 300:
            sys.stderr.write(f"Slack POST returned HTTP {resp.status_code}\n")
    except Exception as exc:  # never let Slack break the host job
        sys.stderr.write(f"Slack POST failed: {exc}\n")


def status_one_liner(payload: dict[str, Any]) -> str:
    checks = payload.get("checks", [])
    n_pass, n_warn, n_fail = counts(checks)
    overall = overall_status(n_pass, n_warn, n_fail)
    line = f"{overall} {n_pass}/{n_warn}/{n_fail}"

    # Append a concise reason for non-PASS: the non-PASS checks, FAIL first.
    non_pass = sorted((c for c in checks if c.get("status") in ("WARN", "FAIL")),
                      key=lambda c: SEVERITY_ORDER.get(c.get("status"), 9))
    reason_bits = [f"{c['name']}: {c['detail']}" for c in non_pass]
    sw = payload.get("sheet_write", {})
    if not sw.get("posted") and not sw.get("skipped"):
        reason_bits.append("sheet_log_not_written")
    if reason_bits:
        line += "  " + " | ".join(reason_bits)
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Format pipeline-health output.")
    parser.add_argument("--input", default="-",
                        help="Path to check_health.py JSON (default: stdin).")
    parser.add_argument("--status-file", default="/tmp/agent_status.txt",
                        help="Where to write the one-line status summary.")
    args = parser.parse_args(argv)

    try:
        payload = load_payload(args.input)
    except Exception as exc:
        msg = f"FAIL — could not read health JSON: {exc}"
        sys.stderr.write(msg + "\n")
        try:
            with open(args.status_file, "w") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        return 0  # best-effort: don't fail the host job

    print_terminal_summary(payload)

    lines = slack_lines(payload)
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if lines and webhook:
        post_to_slack(webhook, payload.get("date", "?"), lines)
    elif lines:
        print("\n(SLACK_WEBHOOK_URL unset — skipping Slack post)")

    status = status_one_liner(payload)
    try:
        with open(args.status_file, "w") as f:
            f.write(status + "\n")
    except Exception as exc:
        sys.stderr.write(f"could not write status file: {exc}\n")
    print(f"\nstatus: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
