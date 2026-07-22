from datetime import UTC, datetime, timedelta

from mc_control_plane.adapters.inbound.host_api import HostApiApplication
from mc_control_plane.adapters.outbound.persistence import HostProtocolStore, SQLiteDatabase
from mc_control_plane.application.data_lease import ResticDataLease
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
            HostCommandState.DELIVERED,
            1,
            None,
        )


class DataLeases:
    def issue_for(self, command, now):  # type: ignore[no-untyped-def]
        return ResticDataLease(
            "s3:https://account.r2.cloudflarestorage.com/bucket/prefix",
            "temporary-access",
            "temporary-secret",
            "temporary-session",
            "repository-password",
            "object-read-write",
            datetime(2026, 7, 22, 12, 15, tzinfo=UTC),
        )


def test_api_attaches_data_lease_without_mutating_durable_command() -> None:
    app = HostApiApplication(  # type: ignore[arg-type]
        DataCommandStore(),
        data_leases=DataLeases(),
        now=lambda: datetime(2026, 7, 22, 12, tzinfo=UTC),
    )

    response = app.handle("/v1/host/poll", {}, "Bearer agent-secret")

    assert response.status == 200
    assert response.body["command"]["payload"] == {"server_unit_id": "survival"}
    assert "temporary-secret" not in str(response.body["command"])
    assert response.body["data_lease"]["secret_access_key"] == "temporary-secret"
