"""Compute provider capability required by the application."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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
    metadata_user_data: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.metadata_user_data is not None and not self.metadata_user_data.strip():
            raise ValueError("metadata user data must not be empty")
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("resource expiration must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    provider_resource_id: str
    provider: str
    region: str
    raw_status: str
    lifecycle: ComputeLifecycle
    tags: frozenset[str]
    has_user_data: bool | None = None
    backups_enabled: bool | None = None
    disk_encryption: str | None = None


class ComputeActionUncertain(Exception):
    """The caller cannot know whether a mutating provider action succeeded."""


class ComputeProviderError(Exception):
    """Base class for normalized compute-provider failures."""


class ComputeProviderUnavailable(ComputeProviderError):
    """A read could not be completed and is safe to retry."""


class ComputeRequestRejected(ComputeProviderError):
    """The provider definitively rejected a request."""


class ComputeResourceNotFound(ComputeProviderError):
    """The requested provider resource no longer exists."""


class ComputeOwnershipMismatch(ComputeProviderError):
    """A mutating request targeted a resource not owned by its identity."""


class ComputeProvider(Protocol):
    def find_by_server_unit(
        self, system_id: str, server_unit_id: str
    ) -> Sequence[RuntimeObservation]: ...

    def create_runtime(self, request: RuntimeCreateRequest) -> RuntimeObservation: ...

    def observe_runtime(self, provider_resource_id: str) -> RuntimeObservation: ...

    def delete_runtime(
        self,
        provider_resource_id: str,
        identity: ResourceIdentity,
    ) -> None: ...
