import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mc_control_plane.adapters.outbound.persistence import (
    HostProtocolStore,
    HostStoreUnavailable,
    SQLiteDatabase,
)
from mc_control_plane.adapters.outbound.persistence.schema import MIGRATIONS
from mc_control_plane.application.host_protocol import (
    HostCommandKind,
    HostCommandState,
    HostEnrollmentError,
)
from tests.fakes import MutableClock


def _enroll(store: HostProtocolStore, clock: MutableClock) -> tuple[str, str]:
    issued = store.issue_enrollment(
        run_id="run-1",
        resource_identity="resource-1",
        expires_at=clock.now() + timedelta(minutes=10),
        now=clock.now(),
    )
    token = "agent-secret"
    store.enroll(
        {
            "protocol_version": 1,
            "agent_id": "agent-1",
            "run_id": "run-1",
            "resource_identity": "resource-1",
            "agent_version": "0.1.0",
            "enrollment_token": issued.token,
            "agent_token": token,
        },
        now=clock.now(),
    )
    return issued.token, token


def _poll(results: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "agent_id": "agent-1",
        "run_id": "run-1",
        "agent_version": "0.1.0",
        "boot_id": "boot-1",
        "capabilities": {"podman": "5.4.2", "quadlet": True},
        "service_states": {"agent": "active", "fixture": "absent"},
        "results": [] if results is None else results,
    }


def test_enrollment_is_one_time_but_same_agent_retry_is_idempotent(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    store = HostProtocolStore(database)
    enrollment_token, agent_token = _enroll(store, clock)

    retried = store.enroll(
        {
            "protocol_version": 1,
            "agent_id": "agent-1",
            "run_id": "run-1",
            "resource_identity": "resource-1",
            "agent_version": "0.1.0",
            "enrollment_token": enrollment_token,
            "agent_token": agent_token,
        },
        now=clock.now(),
    )
    assert retried.agent_id == "agent-1"

    with pytest.raises(HostEnrollmentError, match="already consumed"):
        store.enroll(
            {
                "protocol_version": 1,
                "agent_id": "attacker",
                "run_id": "run-1",
                "resource_identity": "resource-1",
                "agent_version": "0.1.0",
                "enrollment_token": enrollment_token,
                "agent_token": "other-secret",
            },
            now=clock.now(),
        )


def test_expired_enrollment_is_rejected(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    store = HostProtocolStore(database)
    issued = store.issue_enrollment(
        run_id="run-1",
        resource_identity="resource-1",
        expires_at=clock.now() + timedelta(seconds=1),
        now=clock.now(),
    )
    clock.advance(timedelta(seconds=1))

    with pytest.raises(HostEnrollmentError, match="expired"):
        store.enroll(
            {
                "protocol_version": 1,
                "agent_id": "agent-1",
                "run_id": "run-1",
                "resource_identity": "resource-1",
                "agent_version": "0.1.0",
                "enrollment_token": issued.token,
                "agent_token": "agent-secret",
            },
            now=clock.now(),
        )


def test_command_is_redelivered_until_terminal_result_is_recorded(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    store = HostProtocolStore(database)
    _, token = _enroll(store, clock)
    store.queue_command(
        command_id="command-1",
        agent_id="agent-1",
        operation_id="gate2-check",
        step="apply_fixture",
        kind=HostCommandKind.APPLY_FIXTURE,
        deadline=clock.now() + timedelta(minutes=5),
        now=clock.now(),
    )

    candidate = store.poll(token, _poll(), now=clock.now())
    assert candidate is not None
    first = store.mark_delivered(candidate.command_id, candidate.agent_id, now=clock.now())
    candidate = store.poll(token, _poll(), now=clock.now())
    assert candidate is not None
    second = store.mark_delivered(candidate.command_id, candidate.agent_id, now=clock.now())
    assert first is not None and second is not None
    assert first.command_id == second.command_id == "command-1"
    assert second.delivery_count == 2
    assert store.get_agent("agent-1").status == "connected"  # type: ignore[union-attr]

    result = {
        "command_id": "command-1",
        "state": "succeeded",
        "error_code": None,
        "message": None,
        "observation": {"fixture": "installed"},
    }
    assert store.poll(token, _poll([result]), now=clock.now()) is None
    assert store.poll(token, _poll([result]), now=clock.now()) is None
    saved = store.get_command("command-1")
    assert saved is not None
    assert saved.state is HostCommandState.SUCCEEDED
    assert saved.result == {
        "error_code": None,
        "message": None,
        "observation": {"fixture": "installed"},
    }


def test_migration_adds_host_protocol_schema(database: SQLiteDatabase) -> None:
    connection = database.connect()
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"host_enrollments", "host_agents", "host_commands"}.issubset(tables)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "uq_host_enrollments_run" in indexes
    finally:
        connection.close()


def test_poll_retries_a_transient_sqlite_writer_conflict(
    database: SQLiteDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    store = HostProtocolStore(database, busy_retry_delays=(0.01,), sleeper=sleeps.append)
    calls = 0

    def flaky_poll(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_poll_once", flaky_poll)

    assert store.poll("token", {}, now=datetime.now(UTC)) is None
    assert calls == 2
    assert sleeps == [0.01]


def test_poll_bounds_retries_when_sqlite_remains_busy(
    database: SQLiteDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HostProtocolStore(database, busy_retry_delays=(0,), sleeper=lambda _delay: None)

    def busy_poll(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_poll_once", busy_poll)

    with pytest.raises(HostStoreUnavailable, match="remained busy"):
        store.poll("token", {}, now=datetime.now(UTC))
