#!/usr/bin/env python3
"""
One-off investigation: which Meta creative fields actually populate
the headline/body text for Honeycomb's ads?

Background: the cached creatives.json shows 0 of 348 creatives have
title/body populated, because the standard `body`+`title`+
`object_story_spec.link_data.{name,message}` fields are empty for
dynamic catalog / product-feed ads (the bulk of Honeycomb's mix).

This script samples 20 creative_ids from creatives.json and fetches
each with an EXPANDED field list. It prints which fields populate
and suggests where the actual text lives.

Run locally:
    META_ACCESS_TOKEN=<your-token> python3 scripts/investigate_creative_fields.py

Or dispatch via the existing daily-data workflow with this script
substituted in (the workflow already has META_ACCESS_TOKEN set).

Output is human-readable; pipe to a file if you want to share it back:
    META_ACCESS_TOKEN=$T python3 scripts/investigate_creative_fields.py \
      > /tmp/creative-investigation.txt 2>&1
"""

from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.meta import MetaClient, load_config  # noqa: E402

# Field list extends what's in lib/meta.CREATIVE_FIELDS to cover the
# locations Meta uses for dynamic catalog, asset-feed, and dynamic
# creative ads. This is an investigation script — production code
# should only request the subset that actually populates.
EXPANDED_CREATIVE_FIELDS = [
    "id",
    "name",
    "title",
    "body",
    "link_url",
    "call_to_action_type",
    "image_url",
    "image_hash",
    "thumbnail_url",
    # Page-post pointer — for catalog ads the rendered text lives on the
    # post itself. We can fetch /{post_id}?fields=message,name,description
    # as a separate call if needed.
    "effective_object_story_id",
    # Static link ads stash text in object_story_spec.link_data.
    # Catalog ads may stash a TEMPLATE in object_story_spec.template_data
    # with placeholders like {{product.name}}.
    "object_story_spec",
    # Asset-feed dynamic creative — multi-variant text/image arrays.
    "asset_feed_spec",
    # Catalog/DPA-specific fields.
    "template_url_spec",
    "product_set_id",
]

SAMPLE_SIZE = 20


def is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def field_summary(field: str, value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return repr(value[:120] + ("…" if len(value) > 120 else ""))
    if isinstance(value, (int, float, bool)):
        return repr(value)
    # dict / list — show keys / length only
    if isinstance(value, dict):
        return f"<dict keys={sorted(value.keys())}>"
    if isinstance(value, list):
        return f"<list len={len(value)}>"
    return repr(value)[:120]


def deep_locate_text(creative: dict[str, Any]) -> list[tuple[str, str]]:
    """Walk the creative dict and return (path, value) pairs for any
    string field whose name suggests it holds copy text.
    """
    text_keys = {"name", "title", "body", "message", "description",
                 "headline", "primary_text", "text", "caption",
                 "link_description"}
    found: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                child_path = f"{path}.{k}" if path else k
                if k in text_keys and isinstance(v, str) and v.strip():
                    found.append((child_path, v[:200]))
                walk(v, child_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(creative, "")
    return found


def main() -> int:
    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        sys.stderr.write(
            "ERROR: META_ACCESS_TOKEN not set. Export it before running.\n")
        return 2

    config = load_config()
    account_id = os.environ.get("META_AD_ACCOUNT_ID") or config["account"]["id"]
    api_version = config["account"]["meta_api_version"]

    creatives_path = REPO_ROOT / "data" / "creatives" / "creatives.json"
    if not creatives_path.exists():
        sys.stderr.write(f"ERROR: {creatives_path} missing\n")
        return 2
    cached = json.loads(creatives_path.read_text())["creatives"]

    # Stratify the sample: oldest 5, newest 5, random 10. This catches
    # both legacy ads (might have static copy) and recent ones (more
    # likely to be catalog/dynamic).
    by_first_seen = sorted(cached, key=lambda c: c.get("first_seen_date", ""))
    random.seed(7)
    pool = by_first_seen[:5] + by_first_seen[-5:] + random.sample(cached, 10)
    seen_ids: set[str] = set()
    sample = []
    for c in pool:
        cid = c["creative_id"]
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        sample.append(c)
        if len(sample) >= SAMPLE_SIZE:
            break

    client = MetaClient(account_id, api_version, token, sleep_between_calls=0.4)

    print(f"=== Investigating {len(sample)} creatives ===\n")

    populated_counts: Counter[str] = Counter()
    text_locations: Counter[str] = Counter()

    for idx, c in enumerate(sample, 1):
        creative_id = c["creative_id"]
        name = c.get("name", "")[:60]
        print(f"[{idx}/{len(sample)}] {creative_id} — {name}")
        try:
            url = f"{client.base}/{creative_id}"
            fields = ",".join(EXPANDED_CREATIVE_FIELDS)
            raw = client._request(url, {"fields": fields})
        except Exception as exc:
            print(f"  FETCH FAILED: {exc}\n")
            continue

        for field in EXPANDED_CREATIVE_FIELDS:
            value = raw.get(field)
            if is_populated(value):
                populated_counts[field] += 1
            print(f"  {field:30s} {field_summary(field, value)}")

        text_hits = deep_locate_text(raw)
        if text_hits:
            print("  TEXT FOUND AT:")
            for path, val in text_hits:
                text_locations[path] += 1
                print(f"    {path} -> {val[:120]!r}")
        else:
            print("  TEXT FOUND AT (creative endpoint): (none)")

        # Fallback: if creative didn't yield text, try the underlying
        # Page post via effective_object_story_id. This is where dynamic
        # catalog ads usually keep their rendered headline + body.
        post_id = raw.get("effective_object_story_id")
        if not text_hits and post_id:
            try:
                post = client._request(
                    f"{client.base}/{post_id}",
                    {"fields": "message,name,description,attachments"},
                )
                post_hits = deep_locate_text(post)
                if post_hits:
                    print("  TEXT FOUND VIA PAGE POST:")
                    for path, val in post_hits:
                        text_locations[f"[POST]{path}"] += 1
                        print(f"    {path} -> {val[:120]!r}")
                else:
                    print("  PAGE POST: (no text either)")
            except Exception as exc:
                print(f"  PAGE POST FETCH FAILED: {exc}")
        print()

    n = len(sample)
    print("=== Field population rates ===")
    for field in EXPANDED_CREATIVE_FIELDS:
        count = populated_counts[field]
        pct = (count / n) * 100 if n else 0
        print(f"  {field:30s} {count:3d}/{n} ({pct:5.1f}%)")

    print("\n=== Text-bearing paths (across all sampled creatives) ===")
    if text_locations:
        for path, count in text_locations.most_common():
            pct = (count / n) * 100 if n else 0
            print(f"  {path:60s} {count:3d}/{n} ({pct:5.1f}%)")
    else:
        print("  (none — copy text is not exposed via the standard")
        print("   creative endpoint for these ads. Fallback options:")
        print("   - fetch effective_object_story_id as a Page post")
        print("   - parse rendered HTML from the AdPreview API")
        print("   - ship visual-only)")

    print("\n=== Recommendation ===")
    if populated_counts.get("body", 0) >= n * 0.5:
        print("Stick with current normalize_creative — `body` populates "
              "for the majority. The previous backfill may have missed "
              "the field.")
    elif text_locations:
        most_common_path = text_locations.most_common(1)[0][0]
        print(f"Update normalize_creative to read from {most_common_path}.")
        print("Add the parent field to CREATIVE_FIELDS if not already "
              "there.")
    else:
        print("Standard creative endpoint does not expose copy text "
              "for this ad mix. Next step: try the Page-post endpoint "
              "via effective_object_story_id, or fall back to "
              "visual-only categorization.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
