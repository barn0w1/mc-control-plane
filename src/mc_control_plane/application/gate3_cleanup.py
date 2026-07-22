"""Explicitly remove the billable runtime used by Gate 3 acceptance."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from math import ceil
from time import sleep

from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeProvider,
    ComputeResourceNotFound,
)
from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.domain.errors import ControlPlaneError, ServerUnitNotFound
from mc_control_plane.domain.models import ResourceIdentity
from mc_control_plane.domain.states import DesiredState, OperationState


class Gate3CleanupError(ControlPlaneError):
    code = "gate3_cleanup_error"


@dataclass(frozen=True, slots=True)
class Gate3CleanupResult:
    run_id: str | None
    deleted_resource_ids: tuple[str, ...]
    already_absent: bool


def cleanup_gate3_runtime(
    unit_of_work: UnitOfWorkFactory,
    provider: ComputeProvider,
    clock: Clock,
    *,
    server_unit_id: str,
    system_id: str,
    timeout_seconds: float = 600,
    poll_seconds: float = 5,
    sleeper: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> Gate3CleanupResult:
    """Delete only the exact active Run identity, then close local active state."""
    attempts = _attempts(timeout_seconds, poll_seconds)
    report = progress or (lambda _message: None)
    with unit_of_work() as work:
        unit = work.server_units.get(server_unit_id)
        if unit is None:
            raise ServerUnitNotFound(server_unit_id)
        run = work.runs.get_active(server_unit_id)
        runtime = None if run is None else work.runtime_instances.get_active_for_run(run.id)

    if run is None:
        return Gate3CleanupResult(None, (), True)

    identity = ResourceIdentity(system_id, server_unit_id, run.id)
    resource_ids = {
        item.provider_resource_id
        for item in provider.find_by_server_unit(system_id, server_unit_id)
        if identity.owns(item.tags)
    }
    if runtime is not None:
        if not identity.owns(runtime.tags):
            raise Gate3CleanupError("persisted runtime does not match the active Run identity")
        resource_ids.add(runtime.provider_resource_id)

    for resource_id in sorted(resource_ids):
        report(f"deleting owned Linode: resource={resource_id}")
        uncertain = False
        try:
            provider.delete_runtime(resource_id, identity)
        except ComputeResourceNotFound:
            pass
        except ComputeActionUncertain:
            uncertain = True
        for attempt in range(attempts):
            try:
                observed = provider.observe_runtime(resource_id)
            except ComputeResourceNotFound:
                report(f"cleanup confirmed absent: resource={resource_id}")
                break
            if not identity.owns(observed.tags):
                raise Gate3CleanupError("resource ownership changed during cleanup")
            if uncertain and observed.lifecycle is not ComputeLifecycle.DELETING:
                provider.delete_runtime(resource_id, identity)
                uncertain = False
            _pause(attempt, attempts, poll_seconds, sleeper)
        else:
            raise Gate3CleanupError(f"resource {resource_id} remained after cleanup")

    for attempt in range(attempts):
        remaining = [
            item
            for item in provider.find_by_server_unit(system_id, server_unit_id)
            if identity.owns(item.tags)
        ]
        report(f"cleanup discovery poll {attempt + 1}/{attempts}: matches={len(remaining)}")
        if not remaining:
            break
        _pause(attempt, attempts, poll_seconds, sleeper)
    else:
        raise Gate3CleanupError("owned Gate 3 resources remain discoverable")

    now = clock.now()
    with unit_of_work() as work:
        current_unit = work.server_units.get(server_unit_id)
        current_run = work.runs.get_active(server_unit_id)
        if current_unit is None or current_run is None or current_run.id != run.id:
            raise Gate3CleanupError("active Run changed while cleanup was in progress")
        current_runtime = work.runtime_instances.get_active_for_run(run.id)
        if current_runtime is not None:
            if (
                runtime is None
                or current_runtime.provider_resource_id != runtime.provider_resource_id
            ):
                raise Gate3CleanupError("active runtime changed while cleanup was in progress")
            work.runtime_instances.save(
                replace(
                    current_runtime,
                    provider_status="deleted",
                    observed_at=now,
                    deleted_at=now,
                )
            )
        work.runs.save(replace(current_run, ended_at=now))
        work.server_units.save(
            replace(current_unit, desired_state=DesiredState.STOPPED, updated_at=now)
        )
        current_operation = work.operations.get_active(server_unit_id)
        if current_operation is not None:
            work.operations.save(
                replace(
                    current_operation,
                    state=OperationState.CANCELLED,
                    next_attempt_at=None,
                    last_error_code="gate3_acceptance_cleanup",
                    last_error_message="cancelled by explicit Gate 3 acceptance cleanup",
                    updated_at=now,
                )
            )
        work.commit()

    return Gate3CleanupResult(run.id, tuple(sorted(resource_ids)), not resource_ids)


def _attempts(timeout: float, poll: float) -> int:
    if timeout <= 0 or poll <= 0:
        raise ValueError("Gate 3 cleanup timeout and poll interval must be positive")
    return max(1, ceil(timeout / poll))


def _pause(
    attempt: int,
    attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
) -> None:
    if attempt + 1 < attempts:
        sleeper(interval)
