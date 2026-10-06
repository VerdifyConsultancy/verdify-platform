"""A zero native exit must never turn a truncated stream into parity credit."""

import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "copy_receipt", Path(__file__).resolve().parents[1] / "scripts/cnpg-complete-copy-receipt.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TAG = "S2COPY_BOUNDARY_ececa7060c6f"
HEADERS = ["COPY public.a FROM stdin;", "COPY public.b FROM stdin;"]


def test_complete_multiset_preserves_duplicates_and_empty_relation():
    raw = f"{TAG} 0\nb\na\na\n{TAG} 1\n{TAG} END\n".encode()
    r = m.receipt(io.BytesIO(raw), HEADERS, TAG)
    assert r["table_count"] == 2 and r["row_count"] == 3 and r["tables"][1]["rows"] == 0
    reordered = raw.replace(b"b\na\na\n", b"a\nb\na\n")
    assert m.receipt(io.BytesIO(reordered), HEADERS, TAG)["tables"] == r["tables"]


@pytest.mark.parametrize(
    "raw",
    [
        f"{TAG} 0\na\n",
        f"{TAG} 0\na\n{TAG} END\n",
        f"{TAG} 0\na\n{TAG} 0\n",
        f"{TAG} 1\n",
        f"{TAG} 0\na",
        f"{TAG} 0\n{TAG} 1\n{TAG} END\nextra\n",
    ],
)
def test_partial_or_ambiguous_protocol_is_rejected(raw):
    with pytest.raises(ValueError):
        m.receipt(io.BytesIO(raw.encode()), HEADERS, TAG)
