"""Deterministic structural features for ad-copy variants.

Pure Python, no external deps. The Creative Intelligence pipeline
attaches one of these dicts to each variant (body, title, description)
so the analysis layer can correlate length, syntax, and word choice
with performance without relying on LLM judgment for what a human
strategist would compute by inspection.

The features are intentionally simple and explainable — anything an
analyst would extract by reading the copy. Things that need a model
to interpret (tone, evocativeness, brand alignment) belong in the
LLM categorization layer, not here.
"""

from __future__ import annotations

import re
from typing import Any

# Imperative verbs that commonly open Meta ad copy. Used to flag
# call-to-action openings without paying for an LLM call.
IMPERATIVE_OPENERS = {
    "add", "apply", "ask", "break", "buy", "build", "check", "claim",
    "create", "discover", "ditch", "drop", "earn", "expand", "explore",
    "fight", "find", "fund", "get", "give", "grow", "hire", "imagine",
    "join", "keep", "launch", "learn", "let", "make", "open", "pre-qualify",
    "prequalify", "prove", "raise", "save", "scale", "see", "shop",
    "skip", "start", "staff", "stop", "take", "tap", "try", "turn",
    "unlock", "upgrade", "use", "watch",
}


def _strip_punct(token: str) -> str:
    return re.sub(r"^[^\w$]+|[^\w%]+$", "", token)


def compute_features(text: str | None) -> dict[str, Any] | None:
    """Compute structural features for a single variant text.

    Returns None for empty/missing input. Otherwise returns a dict
    with stable keys — never raises on malformed input."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None

    words = text.split()
    word_count = len(words)
    if word_count == 0:
        return None

    char_count = len(text)
    # Sentence split: terminator followed by whitespace OR end of string.
    # Falls back to 1 for single-clause copy with no terminator.
    sentence_parts = [s for s in re.split(r"[.!?]+(?:\s+|$)", text)
                      if s.strip()]
    sentence_count = max(1, len(sentence_parts))

    opening = _strip_punct(words[0]).lower()
    opens_with_imperative = opening in IMPERATIVE_OPENERS

    # Proper-noun heuristic. Two signals:
    #   1. A possessive ("Sarah's", "Honeycomb's") — strong signal
    #      for owner names regardless of position in the sentence.
    #   2. A capitalized word that's NOT the first word of its
    #      sentence AND the text isn't title-case overall. Title
    #      case is detected as ≥60% of non-initial words starting
    #      with a capital, which typical Meta titles ("Your Loyal
    #      Diners Can Invest in You") cleanly trigger.
    has_possessive = bool(re.search(r"\b[A-Z][a-z'’]+'s\b", text))

    non_initial_words = words[1:] if len(words) > 1 else []
    capitalized_ratio = (
        sum(1 for w in non_initial_words
            if w and w[0:1].isupper()) / len(non_initial_words)
        if non_initial_words else 0.0)
    is_title_case = capitalized_ratio >= 0.6

    has_mid_sentence_capital = False
    if not is_title_case:
        for sentence in re.split(r"[.!?]+(?:\s+|$)", text):
            s_words = sentence.strip().split()
            for w in s_words[1:]:  # skip sentence-initial
                stripped = _strip_punct(w)
                if not stripped or not stripped[0:1].isupper():
                    continue
                if stripped in {"I", "I'm", "I've", "I'd", "I'll"}:
                    continue
                has_mid_sentence_capital = True
                break
            if has_mid_sentence_capital:
                break
    has_proper_noun = has_possessive or has_mid_sentence_capital

    word_lengths = [len(_strip_punct(w)) for w in words]
    word_lengths = [n for n in word_lengths if n > 0]
    avg_word_length = (sum(word_lengths) / len(word_lengths)
                       if word_lengths else 0.0)

    return {
        "char_count": char_count,
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_word_length": round(avg_word_length, 2),
        "avg_words_per_sentence": round(word_count / sentence_count, 2),
        "opening_word": opening,
        "opens_with_imperative": opens_with_imperative,
        "has_question_mark": "?" in text,
        "has_exclamation": "!" in text,
        "has_em_dash": "—" in text or "--" in text,
        "has_arrow": any(s in text for s in ("→", "->", "➔")),
        "has_number": bool(re.search(r"\d", text)),
        "has_dollar_amount": bool(re.search(r"\$\s*\d", text)),
        "has_percentage": bool(re.search(r"\d\s*%", text)),
        "has_proper_noun": has_proper_noun,
        "has_second_person": bool(
            re.search(r"\b(you|your|yours|you'?re|you'?ve|you'?ll)\b",
                      text, re.IGNORECASE)),
        "has_first_person_plural": bool(
            re.search(r"\b(we|our|ours|us|we'?re|we'?ve|we'?ll)\b",
                      text, re.IGNORECASE)),
        "has_negation": bool(
            re.search(r"\b(no|not|never|don'?t|won'?t|can'?t|aren'?t|"
                      r"isn'?t|doesn'?t|wasn'?t)\b",
                      text, re.IGNORECASE)),
    }


if __name__ == "__main__":
    # Quick smoke check against real Honeycomb ad copy from the
    # round-1 investigation. Run with: python3 scripts/lib/text_features.py
    import json
    samples = [
        "Banks decline restaurants. Your loyal regulars see a place "
        "worth protecting. Let them invest in it. See if you prequalify →",
        "Your Loyal Diners Can Invest in You",
        "Prequalify instantly. Zero commitment.",
        "Sarah's been brewing for 12 years. $250K raised. 3 ICPs at $42 CPICP.",
        "Ready to grow your brewery?",
        "MCAs drain your margins with daily withdrawals and a renewal "
        "cycle that never ends. 500+ small businesses have raised "
        "community capital instead.",
        "",
        None,
    ]
    for s in samples:
        result = compute_features(s)
        print(f"INPUT:    {s!r}")
        print(f"FEATURES: {json.dumps(result, indent=2)}")
        print()
