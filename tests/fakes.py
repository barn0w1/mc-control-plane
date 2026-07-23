"""Deterministic test adapters."""

from datetime import datetime, timedelta
from typing import Any

from mc_control_plane.application.host_protocol import (
    HostAgentObservation,
    HostCommand,
    HostCommandKind,
    HostCommandState,
)
from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeOwnershipMismatch,
    ComputeProviderError,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
    RuntimeObservation,
)
from mc_control_plane.application.ports.host import HostObservation
from mc_control_plane.domain.models import ResourceIdentity, resource_scope_tags


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class SequenceIds:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new(self) -> str:
        return next(self._values)


class FakeHostManager:
    def __init__(self) -> None:
        self.observations: dict[str, HostObservation] = {}
        self.bootstrap_calls: list[str] = []

    def metadata_for(self, run, identity, now) -> str:  # type: ignore[no-untyped-def]
        self.bootstrap_calls.append(run.id)
        return f"#cloud-config\nrun: {identity.run_id}\n"

    def get_for_run(self, run_id: str) -> HostObservation | None:
        return self.observations.get(run_id)


class FakeHostCommandGateway:
    def __init__(self, run_id: str, now: datetime) -> None:
        self.agent = HostAgentObservation(
            agent_id=f"agent-{run_id}",
            run_id=run_id,
            resource_identity=run_id,
            protocol_version=1,
            agent_version="0.3.4",
            status="connected",
            boot_id="boot-1",
            capabilities={},
            service_states={},
            enrolled_at=now,
            observed_at=now,
        )
        self.commands: dict[str, HostCommand] = {}

    def get_agent_for_run(self, run_id: str) -> HostAgentObservation | None:
        return self.agent if run_id == self.agent.run_id else None

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
    ) -> HostCommand:
        command = HostCommand(
            command_id,
            agent_id,
            self.agent.run_id,
            operation_id,
            step,
            kind,
            1,
            payload or {},
            deadline,
            HostCommandState.PENDING,
            0,
            None,
        )
        self.commands[command_id] = command
        return command

    def get_command(self, command_id: str) -> HostCommand | None:
        return self.commands.get(command_id)

    def succeed(self, command_id: str, observation: dict[str, Any]) -> None:
        current = self.commands[command_id]
        self.commands[command_id] = HostCommand(
            current.command_id,
            current.agent_id,
            current.run_id,
            current.operation_id,
            current.step,
            current.kind,
            current.payload_version,
            current.payload,
            current.deadline,
            HostCommandState.SUCCEEDED,
            1,
            {"error_code": None, "message": None, "observation": observation},
        )


class FakeComputeProvider:
    def __init__(self) -> None:
        self.resources: dict[str, RuntimeObservation] = {}
        self.firewall_ids: dict[str, frozenset[str]] = {}
        self.create_count = 0
        self.deleted: list[str] = []
        self.uncertain_next_create = False
        self.find_error: ComputeProviderError | None = None
        self.create_error: ComputeProviderError | None = None
        self.observe_error: ComputeProviderError | None = None

    def find_by_server_unit(self, system_id: str, server_unit_id: str) -> list[RuntimeObservation]:
        if self.find_error is not None:
            raise self.find_error
        required = resource_scope_tags(system_id, server_unit_id)
        return [item for item in self.resources.values() if required.issubset(item.tags)]

    def create_runtime(self, request: RuntimeCreateRequest) -> RuntimeObservation:
        self.create_count += 1
        if self.create_error is not None:
            raise self.create_error
        provider_id = f"linode-{self.create_count}"
        observation = RuntimeObservation(
            provider_resource_id=provider_id,
            provider="akamai",
            region=request.spec.region,
            raw_status="provisioning",
            lifecycle=ComputeLifecycle.PENDING,
            tags=request.identity.tags,
            has_user_data=request.metadata_user_data is not None,
            backups_enabled=False,
            disk_encryption="disabled",
        )
        self.resources[provider_id] = observation
        self.firewall_ids[provider_id] = (
            frozenset({request.spec.firewall_id})
            if request.spec.firewall_id is not None
            else frozenset()
        )
        if self.uncertain_next_create:
            self.uncertain_next_create = False
            raise ComputeActionUncertain("provider timed out after accepting create")
        return observation

    def observe_runtime(self, provider_resource_id: str) -> RuntimeObservation:
        if self.observe_error is not None:
            raise self.observe_error
        if provider_resource_id not in self.resources:
            raise ComputeResourceNotFound(provider_resource_id)
        return self.resources[provider_resource_id]

    def delete_runtime(
        self,
        provider_resource_id: str,
        identity: ResourceIdentity,
    ) -> None:
        if provider_resource_id not in self.resources:
            raise ComputeResourceNotFound(provider_resource_id)
        if not identity.owns(self.resources[provider_resource_id].tags):
            raise ComputeOwnershipMismatch(provider_resource_id)
        del self.resources[provider_resource_id]
        self.firewall_ids.pop(provider_resource_id, None)
        self.deleted.append(provider_resource_id)

    def attached_firewall_ids(self, provider_resource_id: str) -> frozenset[str]:
        return self.firewall_ids[provider_resource_id]

    def add(self, observation: RuntimeObservation) -> None:
        self.resources[observation.provider_resource_id] = observation

    def set_status(
        self,
        provider_resource_id: str,
        raw_status: str,
        lifecycle: ComputeLifecycle,
    ) -> None:
        current = self.resources[provider_resource_id]
        self.resources[provider_resource_id] = RuntimeObservation(
            provider_resource_id=current.provider_resource_id,
            provider=current.provider,
            region=current.region,
            raw_status=raw_status,
            lifecycle=lifecycle,
            tags=current.tags,
            has_user_data=current.has_user_data,
            backups_enabled=current.backups_enabled,
            disk_encryption=current.disk_encryption,
        )
