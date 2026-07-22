"""SQLite persistence adapter with explicit transaction boundaries."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, cast

from mc_control_plane.adapters.outbound.persistence.schema import MIGRATIONS
from mc_control_plane.domain.errors import PersistenceConflict
from mc_control_plane.domain.models import (
    Operation,
    Run,
    RuntimeInstance,
    RuntimeSpec,
    ServerUnit,
    Snapshot,
)
from mc_control_plane.domain.states import (
    DesiredState,
    OperationKind,
    OperationState,
    SnapshotKind,
    StartStep,
)


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _runtime_spec_json(spec: RuntimeSpec) -> str:
    return json.dumps(
        {
            "firewall_id": spec.firewall_id,
            "image": spec.image,
            "instance_type": spec.instance_type,
            "region": spec.region,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _runtime_spec(value: str) -> RuntimeSpec:
    raw = cast(dict[str, Any], json.loads(value))
    return RuntimeSpec(
        region=cast(str, raw["region"]),
        instance_type=cast(str, raw["instance_type"]),
        image=cast(str, raw["image"]),
        firewall_id=cast(str | None, raw.get("firewall_id")),
    )


def _text(row: sqlite3.Row, name: str) -> str:
    return cast(str, row[name])


def _optional_text(row: sqlite3.Row, name: str) -> str | None:
    return cast(str | None, row[name])


def _server_unit(row: sqlite3.Row) -> ServerUnit:
    created_at = _datetime(_text(row, "created_at"))
    updated_at = _datetime(_text(row, "updated_at"))
    assert created_at is not None
    assert updated_at is not None
    return ServerUnit(
        id=_text(row, "id"),
        name=_text(row, "name"),
        desired_state=DesiredState(_text(row, "desired_state")),
        runtime_spec=_runtime_spec(_text(row, "runtime_spec_json")),
        created_at=created_at,
        updated_at=updated_at,
    )


def _run(row: sqlite3.Row) -> Run:
    started_at = _datetime(_text(row, "started_at"))
    assert started_at is not None
    return Run(
        id=_text(row, "id"),
        server_unit_id=_text(row, "server_unit_id"),
        runtime_spec=_runtime_spec(_text(row, "runtime_spec_json")),
        source_snapshot_id=_optional_text(row, "source_snapshot_id"),
        started_at=started_at,
        ended_at=_datetime(_optional_text(row, "ended_at")),
    )


def _operation(row: sqlite3.Row) -> Operation:
    created_at = _datetime(_text(row, "created_at"))
    updated_at = _datetime(_text(row, "updated_at"))
    assert created_at is not None
    assert updated_at is not None
    step_value = _text(row, "step")
    try:
        step: StartStep | str = StartStep(step_value)
    except ValueError:
        step = step_value
    return Operation(
        id=_text(row, "id"),
        server_unit_id=_text(row, "server_unit_id"),
        run_id=_optional_text(row, "run_id"),
        kind=OperationKind(_text(row, "kind")),
        state=OperationState(_text(row, "state")),
        step=step,
        attempt_count=cast(int, row["attempt_count"]),
        next_attempt_at=_datetime(_optional_text(row, "next_attempt_at")),
        last_error_code=_optional_text(row, "last_error_code"),
        last_error_message=_optional_text(row, "last_error_message"),
        created_at=created_at,
        updated_at=updated_at,
    )


def _runtime_instance(row: sqlite3.Row) -> RuntimeInstance:
    observed_at = _datetime(_text(row, "observed_at"))
    created_at = _datetime(_text(row, "created_at"))
    assert observed_at is not None
    assert created_at is not None
    tags = cast(list[str], json.loads(_text(row, "tags_json")))
    return RuntimeInstance(
        provider_resource_id=_text(row, "provider_resource_id"),
        run_id=_text(row, "run_id"),
        server_unit_id=_text(row, "server_unit_id"),
        provider=_text(row, "provider"),
        region=_text(row, "region"),
        tags=frozenset(tags),
        provider_status=_text(row, "provider_status"),
        observed_at=observed_at,
        created_at=created_at,
        deleted_at=_datetime(_optional_text(row, "deleted_at")),
    )


def _snapshot(row: sqlite3.Row) -> Snapshot:
    created_at = _datetime(_text(row, "created_at"))
    assert created_at is not None
    return Snapshot(
        id=_text(row, "id"),
        server_unit_id=_text(row, "server_unit_id"),
        run_id=_optional_text(row, "run_id"),
        kind=SnapshotKind(_text(row, "kind")),
        created_at=created_at,
        verified_at=_datetime(_optional_text(row, "verified_at")),
    )


class SQLiteDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self, *, transactional: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5,
            autocommit=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or foreign_keys[0] != 1:
            connection.close()
            raise RuntimeError("SQLite foreign key enforcement is unavailable")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        if transactional:
            connection.autocommit = False
        return connection

    def connect(self) -> sqlite3.Connection:
        return self._connect(transactional=True)

    def migrate(self) -> None:
        connection = self._connect(transactional=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                cast(int, row[0])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in MIGRATIONS:
                if migration.version in applied:
                    continue
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


class SQLiteServerUnitRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, server_unit: ServerUnit) -> None:
        self._execute(
            """
            INSERT INTO server_units(
                id, name, desired_state, runtime_spec_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                server_unit.id,
                server_unit.name,
                server_unit.desired_state,
                _runtime_spec_json(server_unit.runtime_spec),
                server_unit.created_at.isoformat(),
                server_unit.updated_at.isoformat(),
            ),
        )

    def get(self, server_unit_id: str) -> ServerUnit | None:
        row = self._connection.execute(
            "SELECT * FROM server_units WHERE id = ?", (server_unit_id,)
        ).fetchone()
        return None if row is None else _server_unit(row)

    def save(self, server_unit: ServerUnit) -> None:
        cursor = self._execute(
            """
            UPDATE server_units
            SET name = ?, desired_state = ?, runtime_spec_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                server_unit.name,
                server_unit.desired_state,
                _runtime_spec_json(server_unit.runtime_spec),
                server_unit.updated_at.isoformat(),
                server_unit.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(server_unit.id)

    def _execute(self, sql: str, parameters: tuple[object, ...]) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, parameters)
        except sqlite3.IntegrityError as error:
            raise PersistenceConflict(str(error)) from error


class SQLiteRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, run: Run) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO runs(
                    id, server_unit_id, runtime_spec_json, source_snapshot_id,
                    started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.server_unit_id,
                    _runtime_spec_json(run.runtime_spec),
                    run.source_snapshot_id,
                    run.started_at.isoformat(),
                    _datetime_text(run.ended_at),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise PersistenceConflict(str(error)) from error

    def get(self, run_id: str) -> Run | None:
        row = self._connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else _run(row)

    def get_active(self, server_unit_id: str) -> Run | None:
        row = self._connection.execute(
            "SELECT * FROM runs WHERE server_unit_id = ? AND ended_at IS NULL",
            (server_unit_id,),
        ).fetchone()
        return None if row is None else _run(row)

    def save(self, run: Run) -> None:
        cursor = self._connection.execute(
            """
            UPDATE runs
            SET server_unit_id = ?, runtime_spec_json = ?, source_snapshot_id = ?,
                started_at = ?, ended_at = ?
            WHERE id = ?
            """,
            (
                run.server_unit_id,
                _runtime_spec_json(run.runtime_spec),
                run.source_snapshot_id,
                run.started_at.isoformat(),
                _datetime_text(run.ended_at),
                run.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(run.id)


class SQLiteOperationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, operation: Operation) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO operations(
                    id, server_unit_id, run_id, kind, state, step, attempt_count,
                    next_attempt_at, last_error_code, last_error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._parameters(operation),
            )
        except sqlite3.IntegrityError as error:
            raise PersistenceConflict(str(error)) from error

    def get(self, operation_id: str) -> Operation | None:
        row = self._connection.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        return None if row is None else _operation(row)

    def get_active(self, server_unit_id: str) -> Operation | None:
        row = self._connection.execute(
            """
            SELECT * FROM operations
            WHERE server_unit_id = ?
              AND state IN ('pending', 'running', 'retry_wait', 'blocked')
            """,
            (server_unit_id,),
        ).fetchone()
        return None if row is None else _operation(row)

    def get_latest(self, server_unit_id: str) -> Operation | None:
        row = self._connection.execute(
            """
            SELECT * FROM operations
            WHERE server_unit_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (server_unit_id,),
        ).fetchone()
        return None if row is None else _operation(row)

    def list_due(self, now: datetime, limit: int) -> list[Operation]:
        if limit <= 0:
            raise ValueError("operation query limit must be positive")
        rows = self._connection.execute(
            """
            SELECT * FROM operations
            WHERE state IN ('pending', 'running', 'retry_wait')
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY COALESCE(next_attempt_at, created_at), created_at, id
            LIMIT ?
            """,
            (now.isoformat(), limit),
        ).fetchall()
        return [_operation(row) for row in rows]

    def save(self, operation: Operation) -> None:
        cursor = self._connection.execute(
            """
            UPDATE operations
            SET server_unit_id = ?, run_id = ?, kind = ?, state = ?, step = ?,
                attempt_count = ?, next_attempt_at = ?, last_error_code = ?,
                last_error_message = ?, created_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (*self._parameters(operation)[1:], operation.id),
        )
        if cursor.rowcount != 1:
            raise KeyError(operation.id)

    @staticmethod
    def _parameters(operation: Operation) -> tuple[object, ...]:
        return (
            operation.id,
            operation.server_unit_id,
            operation.run_id,
            operation.kind,
            operation.state,
            operation.step,
            operation.attempt_count,
            _datetime_text(operation.next_attempt_at),
            operation.last_error_code,
            operation.last_error_message,
            operation.created_at.isoformat(),
            operation.updated_at.isoformat(),
        )


class SQLiteRuntimeInstanceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, runtime: RuntimeInstance) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO runtime_instances(
                    provider_resource_id, run_id, server_unit_id, provider, region,
                    tags_json, provider_status, observed_at, created_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._parameters(runtime),
            )
        except sqlite3.IntegrityError as error:
            raise PersistenceConflict(str(error)) from error

    def get_active_for_run(self, run_id: str) -> RuntimeInstance | None:
        row = self._connection.execute(
            "SELECT * FROM runtime_instances WHERE run_id = ? AND deleted_at IS NULL",
            (run_id,),
        ).fetchone()
        return None if row is None else _runtime_instance(row)

    def get_by_provider_id(self, provider_resource_id: str) -> RuntimeInstance | None:
        row = self._connection.execute(
            "SELECT * FROM runtime_instances WHERE provider_resource_id = ?",
            (provider_resource_id,),
        ).fetchone()
        return None if row is None else _runtime_instance(row)

    def save(self, runtime: RuntimeInstance) -> None:
        cursor = self._connection.execute(
            """
            UPDATE runtime_instances
            SET run_id = ?, server_unit_id = ?, provider = ?, region = ?,
                tags_json = ?, provider_status = ?, observed_at = ?,
                created_at = ?, deleted_at = ?
            WHERE provider_resource_id = ?
            """,
            (*self._parameters(runtime)[1:], runtime.provider_resource_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(runtime.provider_resource_id)

    @staticmethod
    def _parameters(runtime: RuntimeInstance) -> tuple[object, ...]:
        return (
            runtime.provider_resource_id,
            runtime.run_id,
            runtime.server_unit_id,
            runtime.provider,
            runtime.region,
            json.dumps(sorted(runtime.tags), separators=(",", ":")),
            runtime.provider_status,
            runtime.observed_at.isoformat(),
            runtime.created_at.isoformat(),
            _datetime_text(runtime.deleted_at),
        )


class SQLiteSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, snapshot: Snapshot) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO snapshots(
                    id, server_unit_id, run_id, kind, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    snapshot.server_unit_id,
                    snapshot.run_id,
                    snapshot.kind,
                    snapshot.created_at.isoformat(),
                    _datetime_text(snapshot.verified_at),
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self.get(snapshot.id)
            if existing == snapshot:
                return
            raise PersistenceConflict(str(error)) from error

    def get(self, snapshot_id: str) -> Snapshot | None:
        row = self._connection.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return None if row is None else _snapshot(row)

    def get_latest(self, server_unit_id: str) -> Snapshot | None:
        row = self._connection.execute(
            """
            SELECT * FROM snapshots
            WHERE server_unit_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (server_unit_id,),
        ).fetchone()
        return None if row is None else _snapshot(row)

    def save(self, snapshot: Snapshot) -> None:
        cursor = self._connection.execute(
            """
            UPDATE snapshots
            SET server_unit_id = ?, run_id = ?, kind = ?, created_at = ?, verified_at = ?
            WHERE id = ?
            """,
            (
                snapshot.server_unit_id,
                snapshot.run_id,
                snapshot.kind,
                snapshot.created_at.isoformat(),
                _datetime_text(snapshot.verified_at),
                snapshot.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(snapshot.id)


class SQLiteUnitOfWork:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._connection: sqlite3.Connection | None = None
        self._committed = False
        self.server_units: SQLiteServerUnitRepository
        self.runs: SQLiteRunRepository
        self.operations: SQLiteOperationRepository
        self.runtime_instances: SQLiteRuntimeInstanceRepository
        self.snapshots: SQLiteSnapshotRepository

    def __enter__(self) -> SQLiteUnitOfWork:
        if self._connection is not None:
            raise RuntimeError("Unit of Work cannot be entered twice")
        self._connection = self._database.connect()
        self.server_units = SQLiteServerUnitRepository(self._connection)
        self.runs = SQLiteRunRepository(self._connection)
        self.operations = SQLiteOperationRepository(self._connection)
        self.runtime_instances = SQLiteRuntimeInstanceRepository(self._connection)
        self.snapshots = SQLiteSnapshotRepository(self._connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._require_connection()
        try:
            if exc_type is not None or not self._committed:
                connection.rollback()
        finally:
            connection.close()
            self._connection = None

    def commit(self) -> None:
        self._require_connection().commit()
        self._committed = True

    def rollback(self) -> None:
        self._require_connection().rollback()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Unit of Work is not active")
        return self._connection


class SQLiteUnitOfWorkFactory:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def __call__(self) -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(self._database)
