from datetime import UTC, datetime

from mc_control_plane.adapters.outbound.storage.r2 import R2ResticLeaseBroker, R2ResticSettings
from mc_control_plane.application.host_protocol import (
    HostCommand,
    HostCommandKind,
    HostCommandState,
)


class FakeStore:
    def server_unit_for_command(self, command_id: str) -> str:
        assert command_id == "command-1"
        return "survival"


class FakeCredentials:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    def create(self, **values: object) -> dict[str, str]:
        self.request = values
        return {
            "accessKeyId": "temporary-access",
            "secretAccessKey": "temporary-secret",
            "sessionToken": "temporary-session",
        }


def _command(kind: HostCommandKind) -> HostCommand:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    return HostCommand(
        "command-1",
        "agent-1",
        "run-1",
        "operation-1",
        kind.value,
        kind,
        1,
        {"server_unit_id": "survival"},
        now,
        HostCommandState.DELIVERED,
        1,
        None,
    )


def test_r2_lease_is_scoped_and_secret_values_are_not_in_durable_command() -> None:
    credentials = FakeCredentials()
    broker = R2ResticLeaseBroker(  # type: ignore[arg-type]
        FakeStore(),
        credentials,
        R2ResticSettings("account", "bucket", "parent", 900),
        b"k" * 32,
    )
    command = _command(HostCommandKind.SNAPSHOT_DATA)

    lease = broker.issue_for(command, datetime(2026, 7, 22, 12, tzinfo=UTC))

    assert credentials.request is not None
    assert credentials.request["permission"] == "object-read-write"
    assert str(credentials.request["prefix"]).startswith("mc-control-plane/server-units/")
    assert lease.repository.endswith(str(credentials.request["prefix"]))
    assert lease.access_key_id not in str(command.wire_value())
    assert lease.restic_password not in str(command.wire_value())


def test_restore_lease_is_read_only() -> None:
    credentials = FakeCredentials()
    broker = R2ResticLeaseBroker(  # type: ignore[arg-type]
        FakeStore(),
        credentials,
        R2ResticSettings("account", "bucket", "parent", 900),
        b"k" * 32,
    )

    broker.issue_for(_command(HostCommandKind.RESTORE_DATA), datetime(2026, 7, 22, 12, tzinfo=UTC))

    assert credentials.request is not None
    assert credentials.request["permission"] == "object-read-only"
