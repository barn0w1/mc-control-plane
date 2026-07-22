from dataclasses import replace

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
