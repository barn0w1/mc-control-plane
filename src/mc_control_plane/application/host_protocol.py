"""Versioned, closed host-agent protocol values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

HOST_PROTOCOL_VERSION = 1
HOST_AGENT_VERSION = "0.3.3"
HOST_AGENT_ARTIFACT_PATH = f"/artifacts/mccp-host-agent-{HOST_AGENT_VERSION}.whl"


class HostProtocolError(Exception):
    """A request is invalid, unauthenticated, or incompatible."""

    code = "host_protocol_error"


class HostAuthenticationError(HostProtocolError):
    code = "host_authentication_failed"


class HostEnrollmentError(HostProtocolError):
    code = "host_enrollment_failed"


class HostProtocolIncompatible(HostProtocolError):
    code = "host_protocol_incompatible"


class HostCommandKind(StrEnum):
    INSPECT_HOST = "inspect_host"
    APPLY_FIXTURE = "apply_fixture"
    START_FIXTURE = "start_fixture"
    OBSERVE_FIXTURE = "observe_fixture"
    STOP_FIXTURE = "stop_fixture"
    INIT_DATA_REPOSITORY = "init_data_repository"
    WRITE_DATA_FIXTURE = "write_data_fixture"
    RESTORE_DATA = "restore_data"
    SNAPSHOT_DATA = "snapshot_data"
    OBSERVE_DATA = "observe_data"
    APPLY_MINECRAFT = "apply_minecraft"
    START_MINECRAFT = "start_minecraft"
    OBSERVE_MINECRAFT = "observe_minecraft"
    STOP_MINECRAFT = "stop_minecraft"
    SNAPSHOT_MINECRAFT = "snapshot_minecraft"

    @property
    def requires_data_lease(self) -> bool:
        return self in {
            HostCommandKind.INIT_DATA_REPOSITORY,
            HostCommandKind.RESTORE_DATA,
            HostCommandKind.SNAPSHOT_DATA,
            HostCommandKind.SNAPSHOT_MINECRAFT,
        }


class HostCommandState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {HostCommandState.SUCCEEDED, HostCommandState.FAILED}


@dataclass(frozen=True, slots=True)
class IssuedEnrollment:
    enrollment_id: str
    token: str
    run_id: str
    resource_identity: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class HostCommand:
    command_id: str
    agent_id: str
    run_id: str
    operation_id: str
    step: str
    kind: HostCommandKind
    payload_version: int
    payload: dict[str, Any]
    deadline: datetime
    state: HostCommandState
    delivery_count: int
    result: dict[str, Any] | None

    def wire_value(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "run_id": self.run_id,
            "operation_id": self.operation_id,
            "step": self.step,
            "kind": self.kind.value,
            "payload_version": self.payload_version,
            "payload": self.payload,
            "deadline": self.deadline.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class HostAgentObservation:
    agent_id: str
    run_id: str
    resource_identity: str
    protocol_version: int
    agent_version: str
    status: str
    boot_id: str | None
    capabilities: dict[str, Any] | None
    service_states: dict[str, Any] | None
    enrolled_at: datetime
    observed_at: datetime | None
