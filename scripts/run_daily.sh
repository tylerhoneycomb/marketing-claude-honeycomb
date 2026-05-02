#!/usr/bin/env bash
# Orchestrator for the daily ad-level data pipeline.
#
# Sequence:
#   1. Fetch yesterday's ad-set + ad insights from Meta
#   2. Compute derived fatigue / winner-bleeder signals
#
# Designed to be run by .github/workflows/daily-data.yml or manually.
# Honors SNAPSHOT_DATE env var if set; otherwise defaults to yesterday UTC.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[run_daily] fetching ad-level snapshot…"
python3 scripts/fetch_ad_data.py "$@"

echo "[run_daily] computing derived signals…"
python3 scripts/compute_signals.py

echo "[run_daily] done."
