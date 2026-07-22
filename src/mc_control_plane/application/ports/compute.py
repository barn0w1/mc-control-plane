"""Compute provider capability required by the application."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec


class ComputeLifecycle(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    DELETING = "deleting"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RuntimeCreateRequest:
    identity: ResourceIdentity
    spec: RuntimeSpec


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    provider_resource_id: str
    provider: str
    region: str
    raw_status: str
    lifecycle: ComputeLifecycle
    tags: frozenset[str]


class ComputeActionUncertain(Exception):
    """The caller cannot know whether a mutating provider action succeeded."""


class ComputeProvider(Protocol):
    def find_by_server_unit(
        self, system_id: str, server_unit_id: str
    ) -> Sequence[RuntimeObservation]: ...

    def create_runtime(self, request: RuntimeCreateRequest) -> RuntimeObservation: ...

    def observe_runtime(self, provider_resource_id: str) -> RuntimeObservation: ...

    def delete_runtime(self, provider_resource_id: str) -> None: ...
