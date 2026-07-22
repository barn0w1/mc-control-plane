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
    Migration(
        version=2,
        name="host_protocol",
        statements=(
            """
            CREATE TABLE host_enrollments (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                resource_identity TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                agent_id TEXT,
                agent_token_hash TEXT,
                created_at TEXT NOT NULL,
                CHECK (
                    (consumed_at IS NULL AND agent_id IS NULL AND agent_token_hash IS NULL)
                    OR
                    (consumed_at IS NOT NULL AND agent_id IS NOT NULL
                     AND agent_token_hash IS NOT NULL)
                )
            )
            """,
            """
            CREATE TABLE host_agents (
                agent_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                resource_identity TEXT NOT NULL UNIQUE,
                token_hash TEXT NOT NULL UNIQUE,
                protocol_version INTEGER NOT NULL,
                agent_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('enrolled', 'connected', 'incompatible', 'revoked')
                ),
                boot_id TEXT,
                capabilities_json TEXT,
                service_states_json TEXT,
                enrolled_at TEXT NOT NULL,
                observed_at TEXT,
                revoked_at TEXT
            )
            """,
            """
            CREATE TABLE host_commands (
                command_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES host_agents(agent_id) ON DELETE RESTRICT,
                run_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                step TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN (
                    'inspect_host', 'apply_fixture', 'start_fixture',
                    'observe_fixture', 'stop_fixture'
                )),
                payload_version INTEGER NOT NULL CHECK (payload_version = 1),
                payload_json TEXT NOT NULL,
                deadline TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('pending', 'delivered', 'succeeded', 'failed')
                ),
                delivery_count INTEGER NOT NULL DEFAULT 0 CHECK (delivery_count >= 0),
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX ix_host_commands_delivery
            ON host_commands(agent_id, state, created_at)
            """,
            """
            CREATE INDEX ix_host_enrollments_expiry
            ON host_enrollments(expires_at, consumed_at)
            """,
        ),
    ),
)
