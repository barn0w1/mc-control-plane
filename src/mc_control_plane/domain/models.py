"""Provider-independent domain entities and value objects."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from hashlib import blake2s

from mc_control_plane.domain.errors import InvalidModel
from mc_control_plane.domain.states import (
    DesiredState,
    OperationKind,
    OperationState,
    SnapshotKind,
    StartStep,
)


def _require_text(value: str, field: str) -> None:
    if not value or not value.strip():
        raise InvalidModel(f"{field} must not be empty")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidModel(f"{field} must be timezone-aware")


def _resource_tag(kind: str, value: str) -> str:
    """Build a stable provider-safe tag without exposing unbounded domain IDs."""
    digest = blake2s(value.encode(), digest_size=12).hexdigest()
    return f"mccp:{kind}={digest}"


def resource_scope_tags(system_id: str, server_unit_id: str) -> frozenset[str]:
    """Tags shared by all runs of one server unit."""
    _require_text(system_id, "system_id")
    _require_text(server_unit_id, "server_unit_id")
    return frozenset(
        {
            _resource_tag("system", system_id),
            _resource_tag("unit", server_unit_id),
        }
    )


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    region: str
    instance_type: str
    image: str
    container_image: str
    firewall_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("region", "instance_type", "image", "container_image"):
            _require_text(getattr(self, field), field)
        if self.firewall_id is not None:
            _require_text(self.firewall_id, "firewall_id")


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    system_id: str
    server_unit_id: str
    run_id: str

    def __post_init__(self) -> None:
        _require_text(self.system_id, "system_id")
        _require_text(self.server_unit_id, "server_unit_id")
        _require_text(self.run_id, "run_id")

    @property
    def tags(self) -> frozenset[str]:
        return resource_scope_tags(self.system_id, self.server_unit_id) | {
            _resource_tag("run", self.run_id)
        }

    def owns(self, tags: Iterable[str]) -> bool:
        return self.tags.issubset(tags)


@dataclass(frozen=True, slots=True)
class ServerUnit:
    id: str
    name: str
    desired_state: DesiredState
    runtime_spec: RuntimeSpec
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.name, "name")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    server_unit_id: str
    runtime_spec: RuntimeSpec
    source_snapshot_id: str | None
    started_at: datetime
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.server_unit_id, "server_unit_id")
        _require_aware(self.started_at, "started_at")
        if self.ended_at is not None:
            _require_aware(self.ended_at, "ended_at")


@dataclass(frozen=True, slots=True)
class RuntimeInstance:
    provider_resource_id: str
    run_id: str
    server_unit_id: str
    provider: str
    region: str
    tags: frozenset[str]
    provider_status: str
    observed_at: datetime
    created_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in (
            "provider_resource_id",
            "run_id",
            "server_unit_id",
            "provider",
            "region",
            "provider_status",
        ):
            _require_text(getattr(self, field), field)
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.created_at, "created_at")
        if self.deleted_at is not None:
            _require_aware(self.deleted_at, "deleted_at")


@dataclass(frozen=True, slots=True)
class Operation:
    id: str
    server_unit_id: str
    run_id: str | None
    kind: OperationKind
    state: OperationState
    step: StartStep | str
    attempt_count: int
    next_attempt_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.server_unit_id, "server_unit_id")
        if self.run_id is not None:
            _require_text(self.run_id, "run_id")
        if self.attempt_count < 0:
            raise InvalidModel("attempt_count must not be negative")
        if self.next_attempt_at is not None:
            _require_aware(self.next_attempt_at, "next_attempt_at")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: str
    server_unit_id: str
    run_id: str | None
    kind: SnapshotKind
    created_at: datetime
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.server_unit_id, "server_unit_id")
        if self.run_id is not None:
            _require_text(self.run_id, "run_id")
        _require_aware(self.created_at, "created_at")
        if self.verified_at is not None:
            _require_aware(self.verified_at, "verified_at")
