"""Deterministic test adapters."""

from datetime import datetime, timedelta

from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeOwnershipMismatch,
    ComputeProviderError,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
    RuntimeObservation,
)
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
