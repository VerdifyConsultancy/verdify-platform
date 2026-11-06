#!/usr/bin/env python3
"""Fit cortex_planner.tokens' linear estimate against the served Qwen tokenizer (offline).

    python3 scripts/calibrate-cortex-tokens.py --tokenizer PATH/tokenizer.json TEXT_FILE...

Needs the `tokenizers` package and the served model's tokenizer.json (sha256 in
cortex_planner/tokens.py). Splits each file into 20-line chunks, fits non-negative least squares
on the estimator's features, and prints the coefficients and the error of the fitted estimate on
chunks and on whole files. Paste the printed COEFFICIENTS, SAFETY and CALIBRATION into tokens.py.
Bounded: a few MB of text, seconds of CPU.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cortex_tokens", ROOT / "cortex_planner/tokens.py")
tokens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tokens)
NAMES = list(tokens.COEFFICIENTS)


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            raise ValueError("singular")
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def nnls(rows, target):
    # Active-set least squares: solve, zero any negative coefficient, re-solve without it.
    active = list(range(len(NAMES)))
    while True:
        a = [[sum(r[i] * r[j] for r in rows) for j in active] for i in active]
        b = [sum(r[i] * y for r, y in zip(rows, target, strict=True)) for i in active]
        sol = _solve(a, b)
        if all(v >= 0 for v in sol):
            out = [0.0] * len(NAMES)
            for i, v in zip(active, sol, strict=True):
                out[i] = v
            return out
        active = [i for i, v in zip(active, sol, strict=True) if v >= 0]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    from tokenizers import Tokenizer

    sha = hashlib.sha256(Path(args.tokenizer).read_bytes()).hexdigest()
    if sha != tokens.QWEN_TOKENIZER_SHA256:
        print(f"warning: tokenizer sha {sha} differs from {tokens.QWEN_TOKENIZER_SHA256}", file=sys.stderr)
    tok = Tokenizer.from_file(args.tokenizer)
    chunks, wholes = [], []
    for name in args.files:
        text = Path(name).read_text()
        wholes.append(text)
        lines = text.split("\n")
        chunks.extend("\n".join(lines[i : i + 20]) for i in range(0, len(lines), 20))
    chunks = [c for c in chunks if c.strip()]
    feats = [[tokens.features(c)[n] for n in NAMES] for c in chunks]
    truth = [len(tok.encode(c).ids) for c in chunks]
    coef = nnls(feats, truth)

    def est(text):
        f = tokens.features(text)
        return sum(c * f[n] for c, n in zip(coef, NAMES, strict=True))

    ratios = [est(c) / t for c, t in zip(chunks, truth, strict=True) if t >= 50]
    ratios.sort()
    # Scale so the 90th-percentile chunk is not undercounted.
    safety = 1.0 / ratios[int(0.1 * len(ratios))]
    whole = [(len(tok.encode(t).ids), safety * est(t)) for t in wholes]
    mape = sum(abs(e - t) / t for t, e in whole) / len(whole)
    report = {
        "coefficients": {n: round(c, 4) for n, c in zip(NAMES, coef, strict=True)},
        "safety": round(safety, 4),
        "chunks": len(chunks),
        "files": len(wholes),
        "file_true_tokens": [t for t, _ in whole],
        "file_estimated_tokens": [round(e) for _, e in whole],
        "file_mape": round(mape, 4),
        "chunk_ratio_p10_p50_p90": [round(ratios[int(q * (len(ratios) - 1))], 3) for q in (0.1, 0.5, 0.9)],
        "chars_over_4_ratio": round(sum(len(t) / 4 for t in wholes) / sum(t for t, _ in whole), 3),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
