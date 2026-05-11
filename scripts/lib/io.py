"""Atomic file-write helpers shared across the data pipeline.

Used by snapshot, derived-signals, creative cache, and portfolio-
scaling writers. Mirrors the pattern in
`scripts/fetch_ad_data.py:write_json` (PR #72) and
`skills/creative-intelligence/scripts/categorize_creative.py:save_cache_atomic`.

A crash mid-write leaves the orphan `<name>.json.tmp` on disk
(gitignored by `.gitignore:*.json.tmp`) but never a half-written
target. Consumers reading the target file see either the previous
complete state or the new complete state, never a JSONDecodeError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, indent: int = 2,
                      sort_keys: bool = False,
                      default: Any = None) -> None:
    """Write JSON to `path` atomically via tmp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=indent, sort_keys=sort_keys, default=default)
        f.write("\n")
    tmp.replace(path)
