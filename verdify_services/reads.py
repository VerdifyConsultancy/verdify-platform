"""Shared read helpers (architecture §7).

``collapse()`` is pure and shared by API-05 (Graphs lanes) and API-13 (Equipment): equipment
rows repeat their state on reconnect snapshots and Home Assistant polls, so a run of equal
states is one span.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Span:
    """One run of an unchanged state. ``end`` is None while the run is still open."""

    state: Hashable
    start: datetime
    end: datetime | None


def collapse(rows: Iterable[tuple[datetime, Hashable]], end: datetime | None = None) -> list[Span]:
    """Merge consecutive equal states into runs, oldest first.

    ``rows`` are ``(ts, state)`` pairs; they are ordered by ``ts`` (stable), and of two rows at
    the same instant the later one wins, so no run has zero length. Each run ends where the
    next one starts and the last run ends at ``end`` (None leaves it open). The newest run's
    ``start`` is the state's "since".
    """
    points: list[tuple[datetime, Hashable]] = []
    for ts, state in sorted(rows, key=lambda row: row[0]):
        if points and points[-1][0] == ts:
            points[-1] = (ts, state)
        else:
            points.append((ts, state))
    spans: list[Span] = []
    for ts, state in points:
        if spans and spans[-1].state == state:
            continue
        if spans:
            spans[-1] = Span(spans[-1].state, spans[-1].start, ts)
        spans.append(Span(state, ts, None))
    if spans and end is not None:
        spans[-1] = Span(spans[-1].state, spans[-1].start, end)
    return spans
