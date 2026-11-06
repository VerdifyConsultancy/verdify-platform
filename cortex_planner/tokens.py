"""Qwen token counting for pack budgets.

The served model's tokenizer (Qwen3.8-27B, `tokenizer.json`) gives every digit its own token, so
chars/4 undercounts numeric tables about 4x (#1366). When the `tokenizers` package and the
tokenizer file named by VERDIFY_QWEN_TOKENIZER_JSON are both present, counts are exact. The
ingestor image carries neither, so in the pod counts come from a linear estimate calibrated
offline against that tokenizer (sha256 below) on rendered packs and the 2026-11-05 Iris prompts.
scripts/calibrate-cortex-tokens.py produced COEFFICIENTS, SAFETY and CALIBRATION; the estimate
overcounts whole packs by about 4 %, so budgets err on the safe side.
"""

from __future__ import annotations

import functools
import os
import re

QWEN_TOKENIZER_SHA256 = "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"

# tokens ≈ Σ coefficient × feature, fitted by least squares (scripts/calibrate-cortex-tokens.py).
COEFFICIENTS = {
    "digits": 1.1179,
    "words": 1.0724,
    "long_word_chars": 0.1341,
    "punct": 0.6253,
    "newlines": 1.657,
}
# Scaled so the 10th-percentile 20-line chunk is not undercounted.
SAFETY = 1.0448
CALIBRATION = {
    "date": "2026-11-05",
    "tokenizer_sha256": QWEN_TOKENIZER_SHA256,
    "corpus": "5 fixture packs (SUNRISE, SUNSET, MIDNIGHT, TRANSITION, WEEKLY) + Iris SUNRISE system and user prompts",
    "chunks": 181,
    "whole_file_mape": 0.0441,  # every file overestimated, by 4.0-5.3 %
    "chunk_ratio_p10_p50_p90": (0.957, 1.001, 1.061),
    "chars_over_4_ratio": 0.581,  # chars/4 would have undercounted by 42 %
}

_WORD = re.compile(r"[A-Za-z]+")
_PUNCT = re.compile(r"[^\sA-Za-z0-9]")


def features(text: str) -> dict[str, float]:
    words = _WORD.findall(text)
    return {
        "digits": float(sum(ch.isdigit() for ch in text)),
        "words": float(len(words)),
        # Characters beyond the 6th in each word: long identifiers split into several tokens.
        "long_word_chars": float(sum(max(0, len(w) - 6) for w in words)),
        "punct": float(len(_PUNCT.findall(text))),
        "newlines": float(text.count("\n")),
    }


def estimate(text: str) -> int:
    if not text:
        return 0
    feats = features(text)
    return max(1, round(SAFETY * sum(COEFFICIENTS[name] * value for name, value in feats.items())))


@functools.lru_cache(maxsize=1)
def _tokenizer():
    path = os.environ.get("VERDIFY_QWEN_TOKENIZER_JSON", "")
    if not path or not os.path.exists(path):
        return None
    try:
        from tokenizers import Tokenizer  # optional; not in the ingestor image
    except ImportError:
        return None
    return Tokenizer.from_file(path)


def method() -> str:
    return f"qwen-tokenizer@{QWEN_TOKENIZER_SHA256[:12]}" if _tokenizer() is not None else "estimate-v1"


def count(text: str) -> int:
    tok = _tokenizer()
    if tok is not None:
        return len(tok.encode(text).ids) if text else 0
    return estimate(text)
