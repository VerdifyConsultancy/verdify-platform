"""BC-12/ADR0003 §6.2 — fn_grade_credit is a TENT peaking at the target.

Pins the ingestor Python mirror (ingestor/tasks/daily._grade_credit) to the
peak-at-target shape that kills the old flat-1.0-inside-band saturation. The DB
copy (db/migrations/183 fn_grade_credit) is the SAME arithmetic with the SAME
IDEAL_EDGE_CREDIT=0.5 constant; a live DB↔Python parity check is a DB-gated step.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Put ingestor/ on sys.path so `tasks.daily` (and its `import shared`) resolve,
# mirroring tests/test_12_fidelity.py.
_INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

# daily.py reads DB config at import; supply harmless defaults so this pure-math
# unit test imports without a live DB.
for _k, _v in {
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_NAME": "test",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
}.items():
    os.environ.setdefault(_k, _v)

from tasks.daily import _grade_credit  # noqa: E402

# band fixture: stress [60, 90], ideal [70, 80] -> implicit target = midpoint 75
SL, IL, IH, SH = 60.0, 70.0, 80.0, 90.0


def test_grade_credit_peaks_at_target_and_floors_at_ideal_edge():
    assert _grade_credit(75, SL, IL, IH, SH) == 1.0           # peak AT target
    assert _grade_credit(70, SL, IL, IH, SH) == 0.5           # IDEAL_EDGE_CREDIT at the low ideal edge
    assert _grade_credit(80, SL, IL, IH, SH) == 0.5           # ... and the high ideal edge
    assert _grade_credit(60, SL, IL, IH, SH) == 0.0           # stress edge -> 0
    assert _grade_credit(95, SL, IL, IH, SH) == 0.0           # beyond stress -> 0
    assert _grade_credit(None, SL, IL, IH, SH) is None


def test_grade_credit_kills_in_band_saturation():
    # In-band but OFF the target now scores < 1.0 (the old fn returned a flat 1.0).
    off = _grade_credit(78, SL, IL, IH, SH)
    assert 0.0 < off < 1.0
    # Monotonic decline from the target toward each ideal edge.
    assert (
        _grade_credit(76, SL, IL, IH, SH)
        > _grade_credit(78, SL, IL, IH, SH)
        > _grade_credit(80, SL, IL, IH, SH)
    )
    assert (
        _grade_credit(74, SL, IL, IH, SH)
        > _grade_credit(72, SL, IL, IH, SH)
        > _grade_credit(70, SL, IL, IH, SH)
    )


def test_grade_credit_stress_shoulder_is_continuous():
    # Shoulder runs linearly from 0.5 at the ideal edge to 0 at the stress edge.
    assert _grade_credit(65, SL, IL, IH, SH) == 0.25          # halfway up the lower shoulder
    assert _grade_credit(85, SL, IL, IH, SH) == 0.25          # halfway up the upper shoulder
