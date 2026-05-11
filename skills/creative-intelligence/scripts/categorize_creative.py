#!/usr/bin/env python3
"""Categorize ad-copy variants and images for the Creative Intelligence skill.

Reads the creative cache (data/creatives/creatives.json) and the
local image directory (data/creatives/images/). Calls the Anthropic
API once per unique variant text and once per unique image. Caches
results in data/creatives/categorizations.json keyed by variant_id
(text) or image_hash (visual).

Hash-deduped: the boilerplate "MCAs drain your margins…" body
appearing in 50+ ads gets categorized once. Across the corpus
expect roughly 200-400 unique text variants and 50-150 unique
images, so the first run is ~$5 on Sonnet 4.5; subsequent runs
hit cache except for new variants.

Atomic incremental writes: every successful categorization persists
to disk immediately so a partial failure (network blip, mid-run
abort) doesn't lose work. Each per-call failure is logged but
doesn't abort the run.

CLI:
    python3 skills/creative-intelligence/scripts/categorize_creative.py
        [--max-new N]              # cap categorizations this run
        [--text-only | --images-only]
        [--force]                  # ignore cache, re-categorize everything
        [--workers 4]              # ThreadPoolExecutor concurrency
        [--model claude-sonnet-4-5]
        [--dry-run]                # print work queue, don't call API

Environment:
    ANTHROPIC_API_KEY    required (unless --dry-run)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib.text_features import variant_id  # noqa: E402

CREATIVES_PATH = REPO_ROOT / "data" / "creatives" / "creatives.json"
IMAGES_DIR = REPO_ROOT / "data" / "creatives" / "images"
CATEGORIES_PATH = REPO_ROOT / "data" / "creatives" / "categorizations.json"
REFS_DIR = Path(__file__).resolve().parents[1] / "references"

DEFAULT_MODEL = "claude-sonnet-4-5"

COPY_ANGLES = ["owner_story", "benefit_led", "urgency", "social_proof",
               "question", "product_feature", "community_local"]
VISUAL_STYLES = ["real_person", "product_shot", "lifestyle",
                 "storefront", "graphic", "text_heavy"]

CATEGORIZE_TOOL = {
    "name": "categorize",
    "description": ("Record the categorization decision for this ad "
                    "variant or image."),
    "input_schema": {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": ("The selected category. Must be one of "
                                "the listed copy_angle or visual_style "
                                "values."),
            },
            "rationale": {
                "type": "string",
                "description": ("2-3 sentences explaining the choice, "
                                "citing specific words or visual "
                                "elements."),
            },
        },
        "required": ["tag", "rationale"],
    },
}

CACHE_LOCK = Lock()


def load_definitions() -> tuple[str, str]:
    return (
        (REFS_DIR / "copy_angle_definitions.md").read_text(),
        (REFS_DIR / "visual_style_definitions.md").read_text(),
    )


def load_cache() -> dict[str, Any]:
    if not CATEGORIES_PATH.exists():
        return {"updated_at": None, "categorizations": {}}
    try:
        payload = json.loads(CATEGORIES_PATH.read_text())
        if "categorizations" not in payload:
            payload["categorizations"] = {}
        return payload
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("categorizations cache unreadable (%s) — empty", exc)
        return {"updated_at": None, "categorizations": {}}


def save_cache_atomic(cache: dict[str, Any]) -> None:
    """Atomic write: tmp file + rename. Holds CACHE_LOCK at call time."""
    CATEGORIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache["updated_at"] = datetime.now(timezone.utc).isoformat()
    cache["count"] = len(cache.get("categorizations", {}))
    tmp = CATEGORIES_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(CATEGORIES_PATH)


def store_result(cache: dict[str, Any], key: str,
                 entry: dict[str, Any]) -> None:
    """Thread-safe append-and-persist. The lock serializes both the
    cache mutation and the disk write so a concurrent worker can't
    overwrite a partially-written file."""
    with CACHE_LOCK:
        cache["categorizations"][key] = entry
        save_cache_atomic(cache)


def collect_text_variants(
        creatives: dict[str, dict[str, Any]],
        ) -> list[tuple[str, str, str]]:
    """Return [(variant_id, dimension, text), ...] for every unique
    text variant across the cache. variant_id is the same hash the
    dataset builder uses, so the categorizer's output keys match the
    dataset's variant_ids exactly."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for c in creatives.values():
        for dimension, key in (("body", "bodies"),
                               ("title", "titles"),
                               ("description", "descriptions")):
            for text in (c.get(key) or []):
                if not text or not text.strip():
                    continue
                vid = variant_id(text)
                if vid in seen:
                    continue
                seen.add(vid)
                out.append((vid, dimension, text))
    return out


def collect_image_hashes(
        creatives: dict[str, dict[str, Any]]) -> list[str]:
    """Sorted unique image_hashes across the cache."""
    seen: set[str] = set()
    for c in creatives.values():
        for h in (c.get("image_hashes") or []):
            if h:
                seen.add(h)
    return sorted(seen)


def _parse_retry_after(exc: Any) -> float | None:
    """Extract Retry-After (seconds) from an Anthropic SDK exception.
    Header may be either delta-seconds or an HTTP-date. Returns None
    when no header is present or it's unparseable."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers:
        return None
    ra = headers.get("retry-after") or headers.get("Retry-After")
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(ra)
        if target is None:
            return None
        delta = (target - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


def _call_with_retry(client: Any, request_kwargs: dict[str, Any],
                     max_attempts: int = 5,
                     max_backoff: float = 60.0) -> tuple[Any, str | None]:
    """Anthropic SDK call with retry-aware backoff. Returns
    (response, error_str). error_str is None on success.

    Distinguishes:
      - Retryable transient errors (429, 5xx, connection, timeout) →
        exponential backoff, honoring server-provided Retry-After
        on 429 when present.
      - Non-retryable client errors (400, 401, 403, 422) → returned
        immediately without retry.
      - Unknown exceptions → returned without retry (fail loud).

    Previous behavior: 2 attempts × fixed 2-second sleep with no
    error-type discrimination. Brittle when load grows past ~600
    variants per run, where 429 rate limits become routine.
    """
    import anthropic  # lazy to keep --dry-run usable without the SDK
    last_err: str | None = None
    for attempt in range(max_attempts):
        try:
            resp = client.messages.create(**request_kwargs)
            return resp, None
        except anthropic.RateLimitError as exc:
            ra = _parse_retry_after(exc)
            sleep_s = ra if ra is not None else 2 ** attempt
            sleep_s = min(sleep_s, max_backoff)
            last_err = (f"429 RateLimit (attempt {attempt + 1}/{max_attempts}, "
                        f"sleep {sleep_s:.1f}s, retry_after={'server' if ra is not None else 'backoff'})")
        except anthropic.APIConnectionError as exc:
            sleep_s = min(2 ** attempt, max_backoff)
            last_err = f"APIConnectionError: {exc} (sleep {sleep_s:.1f}s)"
        except anthropic.APITimeoutError as exc:
            sleep_s = min(2 ** attempt, max_backoff)
            last_err = f"APITimeoutError: {exc} (sleep {sleep_s:.1f}s)"
        except anthropic.InternalServerError as exc:
            sleep_s = min(2 ** attempt, max_backoff)
            last_err = f"InternalServerError: {exc} (sleep {sleep_s:.1f}s)"
        except (anthropic.BadRequestError, anthropic.AuthenticationError,
                anthropic.PermissionDeniedError,
                anthropic.UnprocessableEntityError) as exc:
            return None, f"non-retryable {type(exc).__name__}: {exc}"
        except anthropic.APIError as exc:
            sleep_s = min(2 ** attempt, max_backoff)
            last_err = f"{type(exc).__name__}: {exc} (sleep {sleep_s:.1f}s)"
        except Exception as exc:  # noqa: BLE001 — surface unknowns
            return None, f"unexpected {type(exc).__name__}: {exc}"
        if attempt < max_attempts - 1:
            logging.info("Anthropic retry %d/%d: %s",
                         attempt + 1, max_attempts, last_err)
            time.sleep(sleep_s)
    return None, last_err or "exhausted retries"


def _extract_tool_use(resp: Any) -> dict[str, Any] | None:
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "tool_use":
            return getattr(block, "input", None) or {}
    return None


def categorize_text(client: Any, model: str, defs: str,
                    dimension: str, text: str) -> dict[str, Any] | None:
    """One Anthropic call. Returns {copy_angle, rationale} or None on
    invalid output. Logs warnings but doesn't raise — failures get
    skipped on this run and retried on the next (no cache entry
    written, so the work queue picks them up again)."""
    sys_msg = (
        "You categorize Honeycomb Credit ad copy variants for the "
        "Creative Intelligence skill. Pick exactly one copy_angle "
        "from the definitions below.\n\n"
        f"Valid copy_angle values: {', '.join(COPY_ANGLES)}\n\n"
        f"{defs}\n\n"
        "Call the `categorize` tool with your answer. The `tag` "
        "field must be exactly one of the listed copy_angle values."
    )
    user_msg = f"Categorize this {dimension} variant:\n\n{text}"

    # Prompt caching on the system message: it's identical across every
    # variant in this run (definitions + enum list + format
    # instructions), so flagging it as ephemeral cache hits cuts
    # effective token cost ~10x after the first call. Fixes the rate-
    # limit failures seen on 2026-05-05 (95/526 calls hit 429 against
    # the 30k tokens/min limit because each call sent the full 5000-
    # token system message uncached).
    resp, err = _call_with_retry(client, {
        "model": model,
        "max_tokens": 512,
        "system": [{
            "type": "text",
            "text": sys_msg,
            "cache_control": {"type": "ephemeral"},
        }],
        "tools": [CATEGORIZE_TOOL],
        "tool_choice": {"type": "tool", "name": "categorize"},
        "messages": [{"role": "user", "content": user_msg}],
    })
    if err is not None:
        logging.warning("text categorization failed (dim=%s, text=%r): %s",
                        dimension, text[:60], err)
        return None

    inp = _extract_tool_use(resp)
    if not inp:
        logging.warning("text categorization failed (dim=%s, text=%r): "
                        "no tool_use block", dimension, text[:60])
        return None
    tag = (inp.get("tag") or "").strip().lower()
    if tag not in COPY_ANGLES:
        logging.warning("text categorization failed (dim=%s, text=%r): "
                        "invalid copy_angle tag %r", dimension, text[:60], tag)
        return None
    return {"copy_angle": tag, "rationale": inp.get("rationale", "")}


def categorize_image(client: Any, model: str, defs: str,
                     image_hash: str,
                     image_path: Path) -> dict[str, Any] | None:
    if not image_path.exists() or image_path.stat().st_size == 0:
        logging.warning("image not on disk for hash %s "
                        "(expected %s)", image_hash, image_path)
        return None

    sys_msg = (
        "You categorize Honeycomb Credit ad images for the Creative "
        "Intelligence skill. Pick exactly one visual_style from the "
        "definitions below.\n\n"
        f"Valid visual_style values: {', '.join(VISUAL_STYLES)}\n\n"
        f"{defs}\n\n"
        "Call the `categorize` tool with your answer. The `tag` "
        "field must be exactly one of the listed visual_style values."
    )
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    # Same prompt-caching pattern as categorize_text — the visual_style
    # system message is identical across every image, so caching it
    # cuts effective rate-limit cost.
    resp, err = _call_with_retry(client, {
        "model": model,
        "max_tokens": 512,
        "system": [{
            "type": "text",
            "text": sys_msg,
            "cache_control": {"type": "ephemeral"},
        }],
        "tools": [CATEGORIZE_TOOL],
        "tool_choice": {"type": "tool", "name": "categorize"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_b64,
            }},
            {"type": "text",
             "text": "Categorize this ad image's visual style."},
        ]}],
    })
    if err is not None:
        logging.warning("image categorization failed for %s: %s",
                        image_hash, err)
        return None

    inp = _extract_tool_use(resp)
    if not inp:
        logging.warning("image categorization failed for %s: "
                        "no tool_use block", image_hash)
        return None
    tag = (inp.get("tag") or "").strip().lower()
    if tag not in VISUAL_STYLES:
        logging.warning("image categorization failed for %s: "
                        "invalid visual_style tag %r", image_hash, tag)
        return None
    return {"visual_style": tag, "rationale": inp.get("rationale", "")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Categorize creative variants and images.")
    parser.add_argument("--max-new", type=int, default=999,
                        help="Cap total categorizations per run")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--images-only", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Ignore cache, re-categorize everything")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print work queue without calling the API")
    args = parser.parse_args(argv)

    if args.text_only and args.images_only:
        sys.stderr.write("ERROR: --text-only and --images-only are "
                         "mutually exclusive\n")
        return 2

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s")

    if not CREATIVES_PATH.exists():
        sys.stderr.write(f"ERROR: {CREATIVES_PATH} missing — run the "
                         f"snapshot pipeline first\n")
        return 2

    creatives_payload = json.loads(CREATIVES_PATH.read_text())
    creatives = {c["creative_id"]: c
                 for c in creatives_payload.get("creatives", [])
                 if c.get("creative_id")}

    copy_defs, visual_defs = load_definitions()
    cache = load_cache()
    cats = cache["categorizations"]

    # Build work queues
    text_work: list[tuple[str, str, str]] = []
    if not args.images_only:
        for vid, dim, text in collect_text_variants(creatives):
            existing = cats.get(vid)
            if (not args.force and existing
                    and existing.get("kind") == "copy"):
                continue
            text_work.append((vid, dim, text))

    image_work: list[tuple[str, Path]] = []
    if not args.text_only:
        for h in collect_image_hashes(creatives):
            existing = cats.get(h)
            if (not args.force and existing
                    and existing.get("kind") == "visual"):
                continue
            img_path = IMAGES_DIR / f"{h}.jpg"
            if not img_path.exists() or img_path.stat().st_size == 0:
                continue
            image_work.append((h, img_path))

    # Apply --max-new budget across both queues, text first.
    text_budget = min(len(text_work), args.max_new)
    image_budget = min(len(image_work), max(0, args.max_new - text_budget))
    text_work = text_work[:text_budget]
    image_work = image_work[:image_budget]

    print(f"work queue: {len(text_work)} text variant(s) + "
          f"{len(image_work)} image(s) "
          f"(model={args.model}, workers={args.workers}, "
          f"max-new={args.max_new}, "
          f"cache size={len(cats)})")

    if args.dry_run:
        for vid, dim, text in text_work[:5]:
            print(f"  TEXT [{dim:5s}] {vid} {text[:80]!r}")
        if len(text_work) > 5:
            print(f"  ... +{len(text_work) - 5} more text variants")
        for h, _ in image_work[:5]:
            print(f"  IMAGE {h}")
        if len(image_work) > 5:
            print(f"  ... +{len(image_work) - 5} more images")
        print("(dry-run: no API calls made)")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.stderr.write("ERROR: ANTHROPIC_API_KEY not set\n")
        return 2

    if not text_work and not image_work:
        print("Nothing to categorize. Cache is up to date.")
        return 0

    # Lazy import — keeps --dry-run usable without the SDK installed.
    import anthropic  # noqa: PLC0415
    # Explicit base_url defeats any ANTHROPIC_BASE_URL env-var override.
    # The first production run of this skill (2026-05-05) failed with
    # 526/526 APIConnectionError when the script ran inside
    # claude-code-action's Bash subprocess; the suspected cause was an
    # inherited base-URL or proxy env that broke direct SDK
    # connections. Keeping this explicit even though the workflow has
    # since been restructured to run scripts as ordinary steps.
    client = anthropic.Anthropic(
        api_key=api_key,
        base_url="https://api.anthropic.com",
    )

    counts = {"text_ok": 0, "text_fail": 0,
              "image_ok": 0, "image_fail": 0}
    counts_lock = Lock()

    def text_task(vid: str, dim: str, text: str) -> None:
        result = categorize_text(client, args.model, copy_defs, dim, text)
        with counts_lock:
            counts["text_ok" if result else "text_fail"] += 1
        if result:
            store_result(cache, vid, {
                "kind": "copy",
                "variant_id": vid,
                "dimension": dim,
                "text": text,
                "copy_angle": result["copy_angle"],
                "rationale": result["rationale"],
                "categorized_at": datetime.now(timezone.utc).isoformat(),
                "model": args.model,
            })

    def image_task(h: str, path: Path) -> None:
        result = categorize_image(client, args.model, visual_defs,
                                   h, path)
        with counts_lock:
            counts["image_ok" if result else "image_fail"] += 1
        if result:
            store_result(cache, h, {
                "kind": "visual",
                "image_hash": h,
                "image_path": str(path.relative_to(REPO_ROOT)),
                "visual_style": result["visual_style"],
                "rationale": result["rationale"],
                "categorized_at": datetime.now(timezone.utc).isoformat(),
                "model": args.model,
            })

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = []
        for vid, dim, text in text_work:
            futures.append(ex.submit(text_task, vid, dim, text))
        for h, path in image_work:
            futures.append(ex.submit(image_task, h, path))
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                logging.error("worker exception: %s", exc)

    print(f"text:  {counts['text_ok']} ok / {counts['text_fail']} fail")
    print(f"image: {counts['image_ok']} ok / {counts['image_fail']} fail")
    print(f"cache size: {len(cats)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
