import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mc_control_plane.adapters.outbound.persistence import (
    SQLiteDatabase,
    SQLiteUnitOfWorkFactory,
)
from mc_control_plane.adapters.outbound.persistence.schema import MIGRATIONS
from mc_control_plane.domain.errors import PersistenceConflict
from mc_control_plane.domain.models import Operation, Run, ServerUnit
from mc_control_plane.domain.states import OperationKind, OperationState, StartStep
from tests.fakes import MutableClock


def test_migration_is_idempotent_and_enables_required_pragmas(database: SQLiteDatabase) -> None:
    database.migrate()

    connection = database.connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )
    finally:
        connection.close()


def test_migration_clears_legacy_snapshot_verification_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path, autocommit=True)
    try:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for migration in MIGRATIONS[:4]:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
        created_at = "2026-07-22T15:35:28+00:00"
        connection.execute(
            """
            INSERT INTO server_units(
                id, name, desired_state, runtime_spec_json, created_at, updated_at
            ) VALUES ('survival', 'Survival', 'stopped', '{}', ?, ?)
            """,
            (created_at, created_at),
        )
        connection.execute(
            """
            INSERT INTO snapshots(id, server_unit_id, kind, created_at, verified_at)
            VALUES ('snapshot-1', 'survival', 'manual', ?, ?)
            """,
            (created_at, created_at),
        )
    finally:
        connection.close()

    database = SQLiteDatabase(path)
    database.migrate()

    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT verified_at FROM snapshots WHERE id = 'snapshot-1'"
        ).fetchone()
        assert row is not None
        assert row[0] is None
    finally:
        connection.close()


def test_database_rejects_second_active_run(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.runs.add(
            Run(
                id="run-1",
                server_unit_id=server_unit.id,
                runtime_spec=server_unit.runtime_spec,
                source_snapshot_id=None,
                started_at=clock.now(),
            )
        )
        work.commit()

    with pytest.raises(PersistenceConflict), unit_of_work() as work:
        work.runs.add(
            Run(
                id="run-2",
                server_unit_id=server_unit.id,
                runtime_spec=server_unit.runtime_spec,
                source_snapshot_id=None,
                started_at=clock.now(),
            )
        )


def test_database_allows_new_run_after_previous_run_ends(
    database: SQLiteDatabase,
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    first = Run(
        id="run-1",
        server_unit_id=server_unit.id,
        runtime_spec=server_unit.runtime_spec,
        source_snapshot_id=None,
        started_at=clock.now(),
    )
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.runs.add(first)
        work.commit()

    connection = database.connect()
    try:
        connection.execute(
            "UPDATE runs SET ended_at = ? WHERE id = ?",
            (clock.now().isoformat(), first.id),
        )
        connection.commit()
    finally:
        connection.close()

    with unit_of_work() as work:
        work.runs.add(replace(first, id="run-2"))
        work.commit()


def test_database_rejects_second_unfinished_operation(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    run = Run(
        id="run-1",
        server_unit_id=server_unit.id,
        runtime_spec=server_unit.runtime_spec,
        source_snapshot_id=None,
        started_at=clock.now(),
    )
    operation = Operation(
        id="operation-1",
        server_unit_id=server_unit.id,
        run_id=run.id,
        kind=OperationKind.START,
        state=OperationState.PENDING,
        step=StartStep.DISCOVER_RUNTIME,
        attempt_count=0,
        next_attempt_at=None,
        last_error_code=None,
        last_error_message=None,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.runs.add(run)
        work.operations.add(operation)
        work.commit()

    with pytest.raises(PersistenceConflict), unit_of_work() as work:
        work.operations.add(replace(operation, id="operation-2"))
