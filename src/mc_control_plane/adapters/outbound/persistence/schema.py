"""Small, ordered SQLite schema migrations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        name="initial_control_plane_schema",
        statements=(
            """
            CREATE TABLE server_units (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                desired_state TEXT NOT NULL
                    CHECK (desired_state IN ('running', 'stopped')),
                runtime_spec_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                server_unit_id TEXT NOT NULL
                    REFERENCES server_units(id) ON DELETE RESTRICT,
                runtime_spec_json TEXT NOT NULL,
                source_snapshot_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX uq_runs_active_server_unit
            ON runs(server_unit_id)
            WHERE ended_at IS NULL
            """,
            """
            CREATE TABLE operations (
                id TEXT PRIMARY KEY,
                server_unit_id TEXT NOT NULL
                    REFERENCES server_units(id) ON DELETE RESTRICT,
                run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT,
                kind TEXT NOT NULL
                    CHECK (kind IN ('start', 'stop', 'snapshot', 'maintenance')),
                state TEXT NOT NULL
                    CHECK (state IN (
                        'pending', 'running', 'retry_wait', 'blocked',
                        'succeeded', 'cancelled'
                    )),
                step TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0
                    CHECK (attempt_count >= 0),
                next_attempt_at TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX uq_operations_active_server_unit
            ON operations(server_unit_id)
            WHERE state IN ('pending', 'running', 'retry_wait', 'blocked')
            """,
            """
            CREATE TABLE runtime_instances (
                provider_resource_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                server_unit_id TEXT NOT NULL
                    REFERENCES server_units(id) ON DELETE RESTRICT,
                provider TEXT NOT NULL,
                region TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                provider_status TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                deleted_at TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX uq_runtime_instances_active_run
            ON runtime_instances(run_id)
            WHERE deleted_at IS NULL
            """,
            """
            CREATE TABLE snapshots (
                id TEXT PRIMARY KEY,
                server_unit_id TEXT NOT NULL
                    REFERENCES server_units(id) ON DELETE RESTRICT,
                run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                kind TEXT NOT NULL CHECK (kind IN ('stop', 'periodic', 'manual')),
                created_at TEXT NOT NULL,
                verified_at TEXT
            )
            """,
            "CREATE INDEX ix_operations_due ON operations(state, next_attempt_at)",
            "CREATE INDEX ix_runtime_instances_server_unit ON runtime_instances(server_unit_id)",
            "CREATE INDEX ix_snapshots_server_unit_created ON snapshots(server_unit_id, created_at)",
        ),
    ),
)
