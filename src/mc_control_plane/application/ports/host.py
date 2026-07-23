"""Host bootstrap and observation capabilities required by workflows."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from mc_control_plane.application.host_protocol import (
    HostAgentObservation,
    HostCommand,
    HostCommandKind,
)
from mc_control_plane.domain.models import ResourceIdentity, Run


class HostBootstrapError(Exception):
    """A deterministic Host bootstrap could not be prepared."""


@dataclass(frozen=True, slots=True)
class HostObservation:
    run_id: str
    agent_id: str
    protocol_version: int
    agent_version: str
    status: str
    boot_id: str | None
    capabilities: dict[str, Any] | None
    service_states: dict[str, Any] | None
    observed_at: datetime | None


class HostBootstrapProvider(Protocol):
    def metadata_for(
        self,
        run: Run,
        identity: ResourceIdentity,
        now: datetime,
    ) -> str: ...


class HostObservationProvider(Protocol):
    def get_for_run(self, run_id: str) -> HostObservation | None: ...


class HostCommandGateway(Protocol):
    def get_agent_for_run(self, run_id: str) -> HostAgentObservation | None: ...

    def queue_command(
        self,
        *,
        command_id: str,
        agent_id: str,
        operation_id: str,
        step: str,
        kind: HostCommandKind,
        deadline: datetime,
        now: datetime,
        payload: dict[str, Any] | None = None,
    ) -> HostCommand: ...

    def get_command(self, command_id: str) -> HostCommand | None: ...
