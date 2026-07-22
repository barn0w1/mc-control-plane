from datetime import UTC, datetime, timedelta

from mc_control_plane.adapters.inbound.host_api import HostApiApplication
from mc_control_plane.adapters.outbound.persistence import (
    HostProtocolStore,
    HostStoreUnavailable,
    SQLiteDatabase,
)
from mc_control_plane.application.data_lease import DataLeaseUnavailable, ResticDataLease
from mc_control_plane.application.host_protocol import (
    HostCommand,
    HostCommandKind,
    HostCommandState,
)
from tests.fakes import MutableClock


def test_api_does_not_expose_enrollment_failure_details(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    app = HostApiApplication(HostProtocolStore(database), now=clock.now)

    response = app.handle(
        "/v1/host/enroll",
        {
            "protocol_version": 1,
            "agent_id": "agent-1",
            "run_id": "run-1",
            "resource_identity": "resource-1",
            "agent_version": "0.1.0",
            "enrollment_token": "unknown-secret",
            "agent_token": "agent-secret",
        },
    )

    assert response.status == 401
    assert response.body == {"error": "host_enrollment_failed"}


def test_api_enrolls_then_requires_bearer_credential_for_poll(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    store = HostProtocolStore(database)
    issued = store.issue_enrollment(
        run_id="run-1",
        resource_identity="resource-1",
        expires_at=clock.now() + timedelta(minutes=10),
        now=clock.now(),
    )
    app = HostApiApplication(store, now=clock.now)
    enrolled = app.handle(
        "/v1/host/enroll",
        {
            "protocol_version": 1,
            "agent_id": "agent-1",
            "run_id": "run-1",
            "resource_identity": "resource-1",
            "agent_version": "0.1.0",
            "enrollment_token": issued.token,
            "agent_token": "agent-secret",
        },
    )
    poll = {
        "protocol_version": 1,
        "agent_id": "agent-1",
        "run_id": "run-1",
        "agent_version": "0.1.0",
        "boot_id": "boot-1",
        "capabilities": {},
        "service_states": {},
        "results": [],
    }

    assert enrolled.status == 200
    assert app.handle("/v1/host/poll", poll).status == 401
    accepted = app.handle("/v1/host/poll", poll, "Bearer agent-secret")
    assert accepted.status == 200
    assert accepted.body["command"] is None


class DataCommandStore:
    def __init__(self) -> None:
        self.delivery_count = 0

    def poll(self, token, body, now):  # type: ignore[no-untyped-def]
        assert token == "agent-secret"
        return HostCommand(
            "command-1",
            "agent-1",
            "run-1",
            "operation-1",
            "snapshot_data",
            HostCommandKind.SNAPSHOT_DATA,
            1,
            {"server_unit_id": "survival"},
            datetime(2026, 7, 22, 13, tzinfo=UTC),
            HostCommandState.PENDING,
            0,
            None,
        )

    def mark_delivered(self, command_id, agent_id, now):  # type: ignore[no-untyped-def]
        assert command_id == "command-1"
        assert agent_id == "agent-1"
        self.delivery_count += 1
        command = self.poll("agent-secret", {}, now)
        return HostCommand(
            command.command_id,
            command.agent_id,
            command.run_id,
            command.operation_id,
            command.step,
            command.kind,
            command.payload_version,
            command.payload,
            command.deadline,
            HostCommandState.DELIVERED,
            self.delivery_count,
            command.result,
        )


class DataLeases:
    def issue_for(self, command, now):  # type: ignore[no-untyped-def]
        return ResticDataLease(
            "s3:https://account.r2.cloudflarestorage.com/bucket/prefix",
            "temporary-access",
            "temporary-secret",
            "temporary-session",
            "object-read-write",
            datetime(2026, 7, 22, 12, 15, tzinfo=UTC),
        )


def test_api_attaches_data_lease_without_mutating_durable_command() -> None:
    store = DataCommandStore()
    app = HostApiApplication(  # type: ignore[arg-type]
        store,
        data_leases=DataLeases(),
        now=lambda: datetime(2026, 7, 22, 12, tzinfo=UTC),
    )

    response = app.handle("/v1/host/poll", {}, "Bearer agent-secret")

    assert response.status == 200
    assert response.body["command"]["payload"] == {"server_unit_id": "survival"}
    assert "temporary-secret" not in str(response.body["command"])
    assert response.body["data_lease"]["secret_access_key"] == "temporary-secret"
    assert response.body["data_lease"]["schema_version"] == 2
    assert "restic_password" not in response.body["data_lease"]
    assert store.delivery_count == 1


class UnavailableDataLeases:
    def issue_for(self, command, now):  # type: ignore[no-untyped-def]
        raise DataLeaseUnavailable("Cloudflare rejected request: status=403 code=10000")


def test_api_keeps_command_pending_when_data_lease_cannot_be_issued() -> None:
    store = DataCommandStore()
    errors: list[str] = []
    app = HostApiApplication(  # type: ignore[arg-type]
        store,
        data_leases=UnavailableDataLeases(),
        now=lambda: datetime(2026, 7, 22, 12, tzinfo=UTC),
        report_error=errors.append,
    )

    response = app.handle("/v1/host/poll", {}, "Bearer agent-secret")

    assert response.status == 503
    assert response.body == {"error": "data_lease_unavailable"}
    assert store.delivery_count == 0
    assert errors == [
        "temporary data lease error: Cloudflare rejected request: status=403 code=10000"
    ]


class BusyStore:
    def poll(self, token, body, now):  # type: ignore[no-untyped-def]
        raise HostStoreUnavailable("SQLite Host store remained busy")


def test_api_reports_sqlite_contention_as_temporary_503() -> None:
    errors: list[str] = []
    app = HostApiApplication(  # type: ignore[arg-type]
        BusyStore(),
        report_error=errors.append,
    )

    response = app.handle("/v1/host/poll", {}, "Bearer agent-secret")

    assert response.status == 503
    assert response.body == {"error": "host_store_unavailable"}
    assert errors == ["temporary Host store error: SQLite Host store remained busy"]
