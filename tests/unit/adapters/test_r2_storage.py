from datetime import UTC, datetime, timedelta

import pytest

from mc_control_plane.adapters.outbound.storage.r2 import (
    CloudflareTemporaryCredentialClient,
    R2ResticLeaseBroker,
    R2ResticSettings,
)
from mc_control_plane.application.data_lease import DataLeaseUnavailable
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
        now + timedelta(minutes=30),
        HostCommandState.PENDING,
        0,
        None,
    )


def test_r2_lease_is_scoped_and_secret_values_are_not_in_durable_command() -> None:
    credentials = FakeCredentials()
    broker = R2ResticLeaseBroker(  # type: ignore[arg-type]
        FakeStore(),
        credentials,
        R2ResticSettings("account", "bucket", "parent", 3600),
    )
    command = _command(HostCommandKind.SNAPSHOT_DATA)

    lease = broker.issue_for(command, datetime(2026, 7, 22, 12, tzinfo=UTC))

    assert credentials.request is not None
    assert credentials.request["permission"] == "object-read-write"
    assert str(credentials.request["prefix"]).startswith("mc-control-plane/server-units/")
    assert lease.repository.endswith(str(credentials.request["prefix"]))
    assert lease.access_key_id not in str(command.wire_value())
    assert set(lease.wire_value()) == {
        "schema_version",
        "repository",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "permission",
        "expires_at",
    }
    assert lease.wire_value()["schema_version"] == 2


def test_restore_lease_is_read_only() -> None:
    credentials = FakeCredentials()
    broker = R2ResticLeaseBroker(  # type: ignore[arg-type]
        FakeStore(),
        credentials,
        R2ResticSettings("account", "bucket", "parent", 3600),
    )

    broker.issue_for(_command(HostCommandKind.RESTORE_DATA), datetime(2026, 7, 22, 12, tzinfo=UTC))

    assert credentials.request is not None
    assert credentials.request["permission"] == "object-read-only"


def test_preflight_mints_and_discards_scoped_write_credential() -> None:
    credentials = FakeCredentials()
    broker = R2ResticLeaseBroker(  # type: ignore[arg-type]
        FakeStore(),
        credentials,
        R2ResticSettings("account", "bucket", "parent", 3600),
    )

    report = broker.preflight()

    assert credentials.request == {
        "bucket": "bucket",
        "parent_access_key_id": "parent",
        "permission": "object-read-write",
        "prefix": "mc-control-plane/preflight/temporary-credentials/",
        "ttl_seconds": 3600,
    }
    assert report.bucket == "bucket"
    assert "temporary-secret" not in str(report)


def test_lease_ttl_must_outlive_command_deadline() -> None:
    broker = R2ResticLeaseBroker(  # type: ignore[arg-type]
        FakeStore(),
        FakeCredentials(),
        R2ResticSettings("account", "bucket", "parent", 900),
    )

    with pytest.raises(DataLeaseUnavailable, match="exceed the command deadline"):
        broker.issue_for(
            _command(HostCommandKind.SNAPSHOT_DATA),
            datetime(2026, 7, 22, 12, tzinfo=UTC),
        )


class RejectedResponse:
    ok = False
    status_code = 403

    def json(self) -> dict[str, object]:
        return {
            "success": False,
            "errors": [
                {
                    "code": 10000,
                    "message": "Authentication error for parent-key in bucket",
                }
            ],
        }


def test_cloudflare_rejection_has_actionable_but_secret_free_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mc_control_plane.adapters.outbound.storage.r2.requests.post",
        lambda *args, **kwargs: RejectedResponse(),
    )
    client = CloudflareTemporaryCredentialClient("account", "api-token")

    with pytest.raises(DataLeaseUnavailable) as captured:
        client.create(
            bucket="bucket",
            parent_access_key_id="parent-key",
            permission="object-read-write",
            prefix="prefix",
            ttl_seconds=3600,
        )

    message = str(captured.value)
    assert "status=403" in message
    assert "code=10000" in message
    assert "Authentication error" in message
    assert "[redacted]" in message
    assert "api-token" not in message
    assert "parent-key" not in message
