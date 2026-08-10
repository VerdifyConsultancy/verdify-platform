"""Bounded Home Assistant history recovery tests for #575."""

from __future__ import annotations

import asyncio
import http.client
import importlib.util
import sys
import urllib.error
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = REPO_ROOT / "ingestor"
if str(INGESTOR_PATH) not in sys.path:
    sys.path.insert(0, str(INGESTOR_PATH))


def _load_backfill_module():
    path = REPO_ROOT / "deploy/k8s/components/ha-gap-backfill/backfill-ha-gaps.py"
    name = "verdify_ha_gap_backfill_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_backfill_module()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _request_query(path: str) -> tuple[tuple[str, ...], datetime, datetime, bool]:
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    entities = tuple(query["filter_entity_id"][0].split(","))
    start = backfill.parse_time(urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1]))
    end = backfill.parse_time(query["end_time"][0])
    return entities, start, end, query.get("skip_initial_state") == ["true"]


def _request_shape(path: str) -> tuple[tuple[str, ...], datetime, datetime]:
    entities, padded_start, padded_end, _skip_initial = _request_query(path)
    return (
        entities,
        padded_start + timedelta(microseconds=1),
        padded_end - timedelta(microseconds=1),
    )


def _payload(entities: tuple[str, ...], timestamp: datetime, state: str = "1"):
    return [
        [
            {
                "entity_id": entity_id,
                "state": state,
                "last_updated": backfill.iso_z(timestamp),
            }
        ]
        for entity_id in entities
    ]


def _fetcher(request_json, **policy_overrides):
    clock = _Clock()
    policy = backfill.HistoryFetchPolicy(
        request_timeout_seconds=10,
        retry_backoff_seconds=1,
        budget_seconds=100,
        **policy_overrides,
    )
    fetcher = backfill.HistoryFetcher(
        "http://ha.invalid",
        "test-token-never-log",
        policy,
        request_json=request_json,
        clock=clock,
        sleep=clock.sleep,
    )
    return fetcher, clock


def test_timeout_retries_once_then_splits_entities_without_loss():
    calls = []

    def request_json(_url, _token, path, _timeout):
        entities, start, _end = _request_shape(path)
        calls.append(entities)
        if len(entities) > 2:
            raise TimeoutError("simulated recorder timeout")
        return _payload(entities, start)

    fetcher, _clock = _fetcher(request_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    histories = fetcher.fetch(
        [f"sensor.e{i}" for i in range(4)],
        start,
        start + timedelta(minutes=10),
        batch_size=25,
    )

    assert set(histories) == {f"sensor.e{i}" for i in range(4)}
    assert [len(shape) for shape in calls] == [4, 4, 2, 2]
    assert fetcher.stats.retries == 1
    assert fetcher.stats.splits == 1


def test_single_entity_timeout_adaptively_splits_time():
    spans = []

    def request_json(_url, _token, path, _timeout):
        entities, start, end = _request_shape(path)
        span = (end - start).total_seconds() / 60
        spans.append(span)
        if span > 10:
            raise TimeoutError("simulated long-range timeout")
        return _payload(entities, start)

    fetcher, _clock = _fetcher(request_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    histories = fetcher.fetch(
        ["sensor.one"],
        start,
        start + timedelta(minutes=20),
        batch_size=25,
        prepartition_time=False,
    )

    assert "sensor.one" in histories
    assert spans == [20, 20, 10, 10]
    assert fetcher.stats.splits == 1


def test_retryable_503_succeeds_without_split_and_401_fails_fast():
    attempts = 0

    def transient(_url, _token, path, _timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError(path, 503, "unavailable", {}, None)
        entities, start, _end = _request_shape(path)
        return _payload(entities, start)

    fetcher, _clock = _fetcher(transient)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    assert fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)
    assert attempts == 2
    assert fetcher.stats.retries == 1
    assert fetcher.stats.splits == 0

    fatal_attempts = 0

    def unauthorized(_url, _token, path, _timeout):
        nonlocal fatal_attempts
        fatal_attempts += 1
        raise urllib.error.HTTPError(path, 401, "unauthorized", {}, None)

    fatal_fetcher, _clock = _fetcher(unauthorized)
    with pytest.raises(backfill.HistoryFetchExhausted, match="http_401"):
        fatal_fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)
    assert fatal_attempts == 1


def test_request_and_wall_budgets_fail_closed():
    def timeout(*_args):
        raise TimeoutError("always")

    fetcher, _clock = _fetcher(timeout, max_requests=1)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    with pytest.raises(backfill.HistoryFetchExhausted, match="request budget"):
        fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)

    slow_clock = _Clock()

    def slow_timeout(*_args):
        slow_clock.now += 11
        raise TimeoutError("slow")

    policy = backfill.HistoryFetchPolicy(
        request_timeout_seconds=10,
        retry_backoff_seconds=1,
        budget_seconds=10,
    )
    slow_fetcher = backfill.HistoryFetcher(
        "http://ha.invalid",
        "token",
        policy,
        request_json=slow_timeout,
        clock=slow_clock,
        sleep=slow_clock.sleep,
    )
    with pytest.raises(backfill.HistoryFetchExhausted, match="budget"):
        slow_fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)


def test_slow_success_expires_after_transport_before_payload_is_accepted():
    clock = _Clock()
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)

    def slow_success(_url, _token, path, _timeout):
        clock.now += 11
        entities, request_start, _end = _request_shape(path)
        return _payload(entities, request_start)

    fetcher = backfill.HistoryFetcher(
        "http://ha.invalid",
        "token",
        backfill.HistoryFetchPolicy(request_timeout_seconds=10, budget_seconds=10),
        request_json=slow_success,
        clock=clock,
        sleep=clock.sleep,
    )

    with pytest.raises(backfill.HistoryFetchExhausted, match="after transport"):
        fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)
    assert fetcher.stats.points == 0


def test_persistent_json_decode_retries_once_without_recursive_split():
    calls = 0

    def malformed_json(*_args):
        nonlocal calls
        calls += 1
        raise backfill.json.JSONDecodeError("bad JSON", "{", 0)

    fetcher, _clock = _fetcher(malformed_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    with pytest.raises(backfill.HistoryFetchExhausted, match="json_decode"):
        fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=60), 25)

    assert calls == 2
    assert fetcher.stats.retries == 1
    assert fetcher.stats.splits == 0


def test_incomplete_body_can_split_but_disconnect_only_retries():
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    spans = []

    def incomplete(_url, _token, path, _timeout):
        entities, request_start, request_end = _request_shape(path)
        span = (request_end - request_start).total_seconds() / 60
        spans.append(span)
        if span > 5:
            raise http.client.IncompleteRead(b"partial", 100)
        return _payload(entities, request_start)

    fetcher, _clock = _fetcher(incomplete)
    assert fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=10), 25)
    assert spans == [10, 10, 5, 5]
    assert fetcher.stats.splits == 1

    disconnected_calls = 0

    def disconnected(*_args):
        nonlocal disconnected_calls
        disconnected_calls += 1
        raise http.client.RemoteDisconnected("peer closed")

    disconnected_fetcher, _clock = _fetcher(disconnected)
    with pytest.raises(backfill.HistoryFetchExhausted, match="RemoteDisconnected"):
        disconnected_fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=10), 25)
    assert disconnected_calls == 2
    assert disconnected_fetcher.stats.splits == 0


def test_retry_after_is_honored_but_capped_by_policy_boundary():
    calls = 0

    def throttled(_url, _token, path, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(path, 503, "unavailable", {"Retry-After": "99"}, None)
        entities, request_start, _end = _request_shape(path)
        return _payload(entities, request_start)

    fetcher, clock = _fetcher(throttled)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    assert fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)
    assert clock.now == 15


def test_prepartition_limits_normal_requests_to_sixty_minutes():
    ranges = []

    def request_json(_url, _token, path, _timeout):
        _entities, start, end = _request_shape(path)
        ranges.append((start, end))
        return []

    fetcher, _clock = _fetcher(request_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=181), 25)

    assert len(ranges) == 4
    assert all((end - begin) <= timedelta(minutes=60) for begin, end in ranges)


def test_time_split_never_creates_a_leaf_below_the_configured_minimum():
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    six_minute_calls = 0

    def six_minute_timeout(*_args):
        nonlocal six_minute_calls
        six_minute_calls += 1
        raise TimeoutError("too broad")

    fetcher, _clock = _fetcher(six_minute_timeout)
    with pytest.raises(backfill.HistoryFetchExhausted, match="irreducible"):
        fetcher.fetch(
            ["sensor.one"],
            start,
            start + timedelta(minutes=6),
            25,
            prepartition_time=False,
        )
    assert six_minute_calls == 2
    assert fetcher.stats.splits == 0

    spans = []

    def ten_minute_timeout(_url, _token, path, _timeout):
        entities, request_start, request_end = _request_shape(path)
        span = (request_end - request_start).total_seconds() / 60
        spans.append(span)
        if span > 5:
            raise TimeoutError("too broad")
        return _payload(entities, request_start)

    splittable, _clock = _fetcher(ten_minute_timeout)
    assert splittable.fetch(
        ["sensor.one"],
        start,
        start + timedelta(minutes=10),
        25,
        prepartition_time=False,
    )
    assert spans == [10, 10, 5, 5]


def test_history_query_uses_home_assistant_false_literal_and_padded_bounds():
    seen_query = None

    def request_json(_url, _token, path, _timeout):
        nonlocal seen_query
        seen_query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        return []

    fetcher, _clock = _fetcher(request_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25)

    assert seen_query["significant_changes_only"] == ["0"]


def test_shared_time_boundary_deduplicates_equal_state_and_rejects_conflict():
    boundary = datetime(2026, 8, 10, 15, tzinfo=UTC)

    def same_state(_url, _token, path, _timeout):
        entities, _start, _end = _request_shape(path)
        return _payload(entities, boundary, "on")

    fetcher, _clock = _fetcher(same_state)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    history = fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=120), 25)["sensor.one"]
    assert history.points == [(boundary, "on")]

    def conflicting_state(_url, _token, path, _timeout):
        entities, request_start, _end = _request_shape(path)
        state = "on" if request_start < boundary else "off"
        return _payload(entities, boundary, state)

    conflict_fetcher, _clock = _fetcher(conflicting_state)
    with pytest.raises(backfill.HistoryFetchExhausted, match="payload_integrity"):
        conflict_fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=120), 25)


def test_recorder_strict_bounds_preserve_real_transition_at_chunk_boundary():
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    boundary = start + timedelta(minutes=60)
    events = [
        (start - timedelta(minutes=10), "old"),
        (start, "old"),
        (boundary, "new"),
    ]
    skip_flags = []

    def recorder_semantics(_url, _token, path, _timeout):
        entities, query_start, query_end, skip_initial = _request_query(path)
        skip_flags.append(skip_initial)
        rows = []
        if not skip_initial:
            prior = [item for item in events if item[0] <= query_start]
            if prior:
                rows.append((query_start, prior[-1][1]))
        rows.extend(item for item in events if query_start < item[0] < query_end)
        series = []
        for index, (timestamp, state) in enumerate(rows):
            row = {"state": state, "last_updated": backfill.history_iso_z(timestamp)}
            if index == 0:
                row["entity_id"] = entities[0]
            series.append(row)
        return [series] if series else []

    fetcher, _clock = _fetcher(recorder_semantics)
    history = fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=120), 25)["sensor.one"]

    assert (boundary, "new") in history.points
    assert (boundary, "old") not in history.points
    assert skip_flags == [False, True]


def test_minimal_response_rows_inherit_series_entity_id():
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)

    def request_json(_url, _token, _path, _timeout):
        return [
            [
                {"entity_id": "sensor.one", "state": "1", "last_updated": backfill.iso_z(timestamp)},
                {"state": "2", "last_updated": backfill.iso_z(timestamp + timedelta(minutes=1))},
            ]
        ]

    fetcher, _clock = _fetcher(request_json)
    history = fetcher.fetch(["sensor.one"], timestamp, timestamp + timedelta(minutes=1), 25)["sensor.one"]
    assert history.points == [(timestamp, "1"), (timestamp + timedelta(minutes=1), "2")]


@pytest.mark.parametrize(
    "bad_row",
    [
        {"state": "2", "last_updated": "not-a-time"},
        {"state": "2", "last_updated": 123},
        {"state": "2"},
        {"state": None, "last_updated": "2026-08-10T14:01:00Z"},
        {"state": {"not": "scalar"}, "last_updated": "2026-08-10T14:01:00Z"},
    ],
)
def test_any_malformed_nonempty_history_row_rejects_the_whole_payload(bad_row):
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)
    payload = [
        [
            {"entity_id": "sensor.one", "state": "1", "last_updated": backfill.iso_z(timestamp)},
            bad_row,
        ]
    ]

    with pytest.raises(backfill.HistoryPayloadError):
        backfill._parse_history_payload(payload, {"sensor.one"})


def test_mixed_valid_and_invalid_payload_fails_once_without_split():
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)
    calls = 0

    def request_json(*_args):
        nonlocal calls
        calls += 1
        return [
            [
                {"entity_id": "sensor.one", "state": "1", "last_updated": backfill.iso_z(timestamp)},
                {"state": "2", "last_updated": "not-a-time"},
            ]
        ]

    fetcher, _clock = _fetcher(request_json)
    with pytest.raises(backfill.HistoryFetchExhausted, match="payload_integrity"):
        fetcher.fetch(["sensor.one"], timestamp, timestamp + timedelta(minutes=1), 25)
    assert calls == 1
    assert fetcher.stats.retries == 0
    assert fetcher.stats.splits == 0


def test_decoded_point_budget_fails_before_returning_history():
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)

    def request_json(*_args):
        return [
            [
                {
                    "entity_id": "sensor.one",
                    "state": str(index),
                    "last_updated": backfill.iso_z(timestamp + timedelta(seconds=index)),
                }
                for index in range(3)
            ]
        ]

    fetcher, _clock = _fetcher(request_json, max_points=2)
    with pytest.raises(backfill.HistoryFetchExhausted, match="point budget"):
        fetcher.fetch(["sensor.one"], timestamp, timestamp + timedelta(minutes=1), 25)
    assert fetcher.stats.points == 0


def test_encoded_response_body_is_bounded_before_json_decode(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(limit):
            assert limit == 11
            return b"x" * 11

    monkeypatch.setattr(backfill.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(backfill.HistoryResponseTooLarge, match="byte limit") as raised:
        backfill.ha_get_json("http://ha.invalid", "token", "/api/history", max_response_bytes=10)
    traceback = raised.value.__traceback__
    while traceback is not None:
        assert "body" not in traceback.tb_frame.f_locals
        traceback = traceback.tb_next


def test_adaptive_split_runs_after_large_response_exception_is_cleared(monkeypatch):
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)

    def request_json(_url, _token, path, _timeout):
        entities, request_start, request_end = _request_shape(path)
        if request_end - request_start > timedelta(minutes=5):
            raise backfill.HistoryResponseTooLarge("bounded test body")
        return _payload(entities, request_start)

    fetcher, _clock = _fetcher(request_json)
    original_split = fetcher._split_request
    active_exceptions = []

    def split_after_exception_scope(request, reason, destination):
        active_exceptions.append(sys.exception())
        return original_split(request, reason, destination)

    monkeypatch.setattr(fetcher, "_split_request", split_after_exception_scope)
    assert fetcher.fetch(
        ["sensor.one"],
        start,
        start + timedelta(minutes=10),
        25,
        prepartition_time=False,
    )
    assert active_exceptions == [None]


def test_budget_is_rechecked_after_result_freeze(monkeypatch):
    clock = _Clock()
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)

    def request_json(_url, _token, path, _timeout):
        entities, request_start, _end = _request_shape(path)
        return _payload(entities, request_start)

    original_freeze = backfill._freeze_history_points

    def slow_freeze(points):
        result = original_freeze(points)
        clock.now += 11
        return result

    monkeypatch.setattr(backfill, "_freeze_history_points", slow_freeze)
    fetcher = backfill.HistoryFetcher(
        "http://ha.invalid",
        "token",
        backfill.HistoryFetchPolicy(request_timeout_seconds=10, budget_seconds=10),
        request_json=request_json,
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(backfill.HistoryFetchExhausted, match="after result freeze"):
        fetcher.fetch(["sensor.one"], timestamp, timestamp + timedelta(minutes=1), 25)


def test_fetch_failure_occurs_before_window_transaction():
    class FailingFetcher:
        def fetch(self, *_args, **_kwargs):
            raise backfill.HistoryFetchExhausted("test failure")

    class NoTransactionConnection:
        def transaction(self):
            raise AssertionError("DB transaction entered before history completed")

    mappings = backfill.MappingSet([], [], [], [], {}, {})
    window = backfill.Window(
        datetime(2026, 8, 10, 14, tzinfo=UTC),
        datetime(2026, 8, 10, 15, tzinfo=UTC),
        ("climate",),
    )
    args = SimpleNamespace(history_carry_minutes=20, batch_size=25)

    with pytest.raises(backfill.HistoryFetchExhausted):
        asyncio.run(
            backfill.backfill_window(
                NoTransactionConnection(),
                window,
                mappings,
                FailingFetcher(),
                args,
                {"climate": set(), "diagnostics": set()},
            )
        )


def test_expired_fetch_budget_is_checked_immediately_before_transaction():
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)

    class ExpiredFetcher:
        def fetch(self, *_args, **_kwargs):
            return {backfill.REPRESENTATIVE_HA_ENTITIES[0]: backfill.HAHistory([(timestamp, "1")])}

        def ensure_within_budget(self, stage):
            assert stage == "before window transaction"
            raise backfill.HistoryFetchExhausted("expired before transaction")

    class NoTransactionConnection:
        def transaction(self):
            raise AssertionError("DB transaction entered after history budget expired")

    window = backfill.Window(timestamp, timestamp + timedelta(minutes=1), ("climate",))
    args = SimpleNamespace(history_carry_minutes=20, batch_size=25, apply=True)
    with pytest.raises(backfill.HistoryFetchExhausted, match="before transaction"):
        asyncio.run(
            backfill.backfill_window(
                NoTransactionConnection(),
                window,
                backfill.MappingSet([], [], [], [], {}, {}),
                ExpiredFetcher(),
                args,
                {"climate": set(), "diagnostics": set()},
            )
        )


def test_late_writer_failure_rolls_back_the_whole_window(monkeypatch):
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)

    class Fetcher:
        def fetch(self, *_args, **_kwargs):
            return {backfill.REPRESENTATIVE_HA_ENTITIES[0]: backfill.HAHistory([(timestamp, "1")])}

    class Transaction:
        entered = False
        rolled_back = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, exc_type, _exc, _tb):
            self.rolled_back = exc_type is not None
            return False

    transaction = Transaction()
    connection = SimpleNamespace(transaction=lambda: transaction)

    async def one(*_args, **_kwargs):
        return 1

    async def fail(*_args, **_kwargs):
        raise RuntimeError("late writer failure")

    for name in (
        "backfill_climate",
        "backfill_diagnostics",
        "backfill_setpoints",
        "backfill_energy",
        "backfill_equipment",
    ):
        monkeypatch.setattr(backfill, name, one)
    monkeypatch.setattr(backfill, "backfill_system_state", fail)

    mappings = backfill.MappingSet([], [], [], [], {}, {})
    window = backfill.Window(timestamp, timestamp + timedelta(minutes=1), ("climate",))
    args = SimpleNamespace(history_carry_minutes=20, batch_size=25)
    with pytest.raises(RuntimeError, match="late writer failure"):
        asyncio.run(
            backfill.backfill_window(
                connection,
                window,
                mappings,
                Fetcher(),
                args,
                {"climate": set(), "diagnostics": set()},
            )
        )
    assert transaction.entered is True
    assert transaction.rolled_back is True


def test_dry_run_completes_transaction_without_claiming_a_commit(monkeypatch, caplog):
    timestamp = datetime(2026, 8, 10, 14, tzinfo=UTC)

    class Fetcher:
        def fetch(self, *_args, **_kwargs):
            return {backfill.REPRESENTATIVE_HA_ENTITIES[0]: backfill.HAHistory([(timestamp, "1")])}

        def ensure_within_budget(self, _stage):
            return None

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    async def one(*_args, **_kwargs):
        return 1

    for name in (
        "backfill_climate",
        "backfill_diagnostics",
        "backfill_setpoints",
        "backfill_energy",
        "backfill_equipment",
        "backfill_system_state",
    ):
        monkeypatch.setattr(backfill, name, one)

    window = backfill.Window(timestamp, timestamp + timedelta(minutes=1), ("climate",))
    args = SimpleNamespace(history_carry_minutes=20, batch_size=25, apply=False)
    with caplog.at_level("INFO"):
        stats = asyncio.run(
            backfill.backfill_window(
                SimpleNamespace(transaction=lambda: Transaction()),
                window,
                backfill.MappingSet([], [], [], [], {}, {}),
                Fetcher(),
                args,
                {"climate": set(), "diagnostics": set()},
            )
        )

    assert stats.windows_committed == 0
    assert stats.windows_backfilled == 0
    assert stats.windows_with_candidates == 1
    assert "mode=DRY-RUN" in caplog.text
    assert "candidate_rows=" in caplog.text
    assert "window_write_commit" not in caplog.text


def test_history_logs_never_include_token_or_entity_list(caplog):
    def request_json(_url, _token, path, _timeout):
        entities, start, _end = _request_shape(path)
        return _payload(entities, start)

    fetcher, _clock = _fetcher(request_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    with caplog.at_level("INFO"):
        fetcher.fetch(["sensor.private_name"], start, start + timedelta(minutes=1), 25)
    rendered = caplog.text
    assert "test-token-never-log" not in rendered
    assert "sensor.private_name" not in rendered
    assert "entity_hash=" in rendered


def test_history_failure_logs_and_exception_redact_entity_and_token(caplog):
    def unsafe_transport_error(_url, token, path, _timeout):
        entity = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["filter_entity_id"][0]
        raise RuntimeError(f"transport leaked {token} for {entity}")

    fetcher, _clock = _fetcher(unsafe_transport_error)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    with caplog.at_level("INFO"), pytest.raises(backfill.HistoryFetchExhausted) as raised:
        fetcher.fetch(["sensor.private_name"], start, start + timedelta(minutes=1), 25)

    rendered = caplog.text + str(raised.value)
    assert "sensor.private_name" not in rendered
    assert "test-token-never-log" not in rendered


def test_fetch_completion_logs_window_deltas_and_run_totals(caplog):
    def request_json(_url, _token, path, _timeout):
        entities, start, _end = _request_shape(path)
        return _payload(entities, start)

    fetcher, _clock = _fetcher(request_json)
    start = datetime(2026, 8, 10, 14, tzinfo=UTC)
    with caplog.at_level("INFO"):
        fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25, window_key="first")
        fetcher.fetch(["sensor.one"], start, start + timedelta(minutes=1), 25, window_key="second")

    second = next(message for message in caplog.messages if "history_fetch_complete window=second" in message)
    assert "window_attempts=1 window_retries=0 window_splits=0" in second
    assert "run_attempts=2 run_retries=0 run_splits=0 run_decoded_points=2" in second


@pytest.mark.parametrize("field", ["request_timeout_seconds", "budget_seconds", "request_max_minutes"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_history_policy_rejects_nonfinite_bounds(field, value):
    with pytest.raises(ValueError, match="invalid history fetch policy"):
        backfill.HistoryFetchPolicy(**{field: value})


@pytest.mark.parametrize("field", ["transport_retries", "max_requests", "max_split_depth", "max_points"])
@pytest.mark.parametrize("value", [True, 1.5])
def test_history_policy_requires_exact_integer_limits(field, value):
    with pytest.raises(ValueError, match="invalid history fetch policy"):
        backfill.HistoryFetchPolicy(**{field: value})
