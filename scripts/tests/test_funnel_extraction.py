"""Regression tests for lead-first funnel extraction.

Fixtures are VERBATIM `actions[]` payloads pulled from the Meta Graph API on
2026-09-09 for the two live Q3 campaigns, trimmed to the entries that matter.
Expected values were read off the same API response, so these tests pin the
extraction to ground truth rather than to our own reimplementation of it.

Run: python3 scripts/tests/test_funnel_extraction.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib.meta import FunnelSpec, extract_conversions, funnel_from_config  # noqa: E402

CONFIG = json.loads((REPO_ROOT / "data" / "config" / "benchmarks.json").read_text())
FUNNEL = funnel_from_config(CONFIG)

# LEADS-Broad-Q3-2026, campaign 120252841681010631, last_30d to 2026-09-08.
BROAD_ACTIONS = [
    {"action_type": "link_click", "value": "1083"},
    {"action_type": "landing_page_view", "value": "982"},
    {"action_type": "onsite_web_lead", "value": "215"},
    {"action_type": "lead", "value": "215"},
    {"action_type": "offsite_conversion.fb_pixel_lead", "value": "215"},
    {"action_type": "offsite_lead_add_20_s_calls", "value": "215"},
    {"action_type": "offsite_conversion.custom.1527298745037132", "value": "29"},
    {"action_type": "offsite_conversion.custom.1153878920152279", "value": "175"},
]
BROAD_EXPECTED = {
    "leads": 215,
    "prequal_decisions": 175,
    "ic_conversions": 0,       # IC absent from this campaign entirely
    "rewards_conversions": 29,
}

# LEADS-IFW-Broad-Q3-2026, campaign 120252962579520631, last_30d to 2026-09-08.
IFW_ACTIONS = [
    {"action_type": "link_click", "value": "1402"},
    {"action_type": "onsite_web_lead", "value": "180"},
    {"action_type": "lead", "value": "180"},
    {"action_type": "offsite_conversion.fb_pixel_lead", "value": "180"},
    {"action_type": "offsite_conversion.custom.1527298745037132", "value": "59"},
    {"action_type": "offsite_conversion.custom.2330338620810873", "value": "1"},
    {"action_type": "offsite_conversion.custom.1153878920152279", "value": "180"},
]
IFW_EXPECTED = {
    "leads": 180,
    "prequal_decisions": 180,
    "ic_conversions": 1,
    "rewards_conversions": 59,
}

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}\n        got  {got}\n        want {want}")


def main() -> int:
    print("live-payload extraction")
    check("LEADS-Broad", extract_conversions(BROAD_ACTIONS, FUNNEL), BROAD_EXPECTED)
    check("LEADS-IFW-Broad", extract_conversions(IFW_ACTIONS, FUNNEL), IFW_EXPECTED)

    print("lead priority is a preference chain, never a sum")
    # 215 appears under three action types; summing would yield 645.
    check("identical types not summed",
          extract_conversions(BROAD_ACTIONS, FUNNEL)["leads"], 215)
    # Order in the payload must not change the answer.
    check("order independent",
          extract_conversions(list(reversed(BROAD_ACTIONS)), FUNNEL)["leads"], 215)
    # When the aggregate `lead` is absent, fall through to the next type.
    check("falls through to fb_pixel_lead",
          extract_conversions(
              [{"action_type": "offsite_conversion.fb_pixel_lead", "value": "42"}],
              FUNNEL)["leads"], 42)
    # A lower-priority type must not override a present higher-priority one.
    check("priority beats position",
          extract_conversions(
              [{"action_type": "onsite_web_lead", "value": "99"},
               {"action_type": "lead", "value": "42"}], FUNNEL)["leads"], 42)

    print("degenerate input")
    zero = {f: 0 for f in FUNNEL.count_fields}
    check("None actions", extract_conversions(None, FUNNEL), zero)
    check("empty actions", extract_conversions([], FUNNEL), zero)
    check("unparseable value",
          extract_conversions([{"action_type": "lead", "value": "abc"}], FUNNEL)["leads"], 0)
    check("missing action_type",
          extract_conversions([{"value": "5"}], FUNNEL), zero)

    print("config wiring")
    check("IC is a subtype, not primary", "ic_conversions" in FUNNEL.count_fields, True)
    check("leads is first field", FUNNEL.count_fields[0], "leads")
    check("subtypes exclude the _doc key",
          all(isinstance(f, str) and f.endswith("_conversions")
              for f, _ in FUNNEL.subtypes), True)

    print("empty-funnel fallback")
    bare = funnel_from_config({})
    check("defaults to LEAD_ACTION_PRIORITY", bare.lead_priority[0], "lead")
    check("no quality conversion configured", bare.quality_action_type, None)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
