"""Explicit, billable Gate 1 lifecycle check for an Akamai Cloud account."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from time import sleep
from uuid import uuid4

from mc_control_plane.adapters.outbound.compute.linode import LinodeComputeProvider
from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeProviderError,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
    RuntimeObservation,
)
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec

DEBIAN_13_IMAGE = "linode/debian13"


class LinodeGate1CheckError(Exception):
    """The billable Gate 1 check did not meet its acceptance criteria."""


class LinodeGate1CleanupError(LinodeGate1CheckError):
    """The check could not prove that every resource it created was absent."""


@dataclass(frozen=True, slots=True)
class LinodeGate1Result:
    run_id: str
    provider_resource_id: str
    final_provider_status: str
    metadata_confirmed: bool
    firewall_confirmed: bool
    backups_disabled: bool
    cleanup_confirmed: bool


def run_linode_gate1_check(
    provider: LinodeComputeProvider,
    spec: RuntimeSpec,
    *,
    system_id: str = "mc-control-plane",
    timeout_seconds: float = 600.0,
    poll_seconds: float = 5.0,
    now: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] = sleep,
    run_id_factory: Callable[[], str] | None = None,
) -> LinodeGate1Result:
    """Create, observe, ownership-check, and delete one uniquely tagged VM.

    The caller must separately require an explicit billing confirmation. This
    function always attempts owned-resource cleanup after a create was attempted.
    """

    if spec.image != DEBIAN_13_IMAGE:
        raise LinodeGate1CheckError(
            f"Gate 1 requires Debian 13 image {DEBIAN_13_IMAGE!r}, got {spec.image!r}"
        )
    attempts = _attempt_count(timeout_seconds, poll_seconds)
    clock = now or (lambda: datetime.now(UTC))
    make_run_id = run_id_factory or (lambda: f"gate1-{uuid4().hex}")
    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("Gate 1 clock must return a timezone-aware datetime")

    identity = ResourceIdentity(
        system_id=system_id,
        server_unit_id="gate1-infra-lifecycle",
        run_id=make_run_id(),
    )
    request = RuntimeCreateRequest(
        identity=identity,
        spec=spec,
        metadata_user_data=_cloud_init(identity.run_id),
        expires_at=started_at + timedelta(hours=1),
    )
    provider.validate_runtime_spec(spec, require_metadata=True, require_firewall=True)

    create_attempted = False
    tracked_ids: set[str] = set()
    operation_error: BaseException | None = None
    successful_observation: RuntimeObservation | None = None

    try:
        existing = _exact_resources(provider, identity)
        if existing:
            raise LinodeGate1CheckError("unique Gate 1 identity unexpectedly already exists")

        create_attempted = True
        try:
            created = provider.create_runtime(request)
            tracked_ids.add(created.provider_resource_id)
        except ComputeActionUncertain:
            created = _wait_for_discovery(provider, identity, attempts, poll_seconds, sleeper)
            tracked_ids.add(created.provider_resource_id)

        successful_observation = _wait_for_running(
            provider,
            identity,
            created.provider_resource_id,
            attempts,
            poll_seconds,
            sleeper,
        )
        if successful_observation.has_user_data is not True:
            raise LinodeGate1CheckError(
                "Linode did not confirm that Metadata user data was attached"
            )
        if successful_observation.backups_enabled is not False:
            raise LinodeGate1CheckError(
                "Linode Backup was enabled or could not be confirmed disabled; "
                "check the account-wide backups_enabled setting"
            )
        expected_firewall = str(spec.firewall_id)
        attached_firewalls = provider.attached_firewall_ids(
            successful_observation.provider_resource_id
        )
        if expected_firewall not in attached_firewalls:
            raise LinodeGate1CheckError(
                f"expected firewall {expected_firewall} is not attached to the Linode"
            )
    except BaseException as error:
        operation_error = error

    if create_attempted:
        try:
            _cleanup(provider, identity, tracked_ids, attempts, poll_seconds, sleeper)
        except BaseException as cleanup_error:
            context = (
                f"; original check failed with {type(operation_error).__name__}: {operation_error}"
                if operation_error is not None
                else ""
            )
            raise LinodeGate1CleanupError(
                f"owned Gate 1 resource cleanup could not be confirmed{context}"
            ) from cleanup_error

    if operation_error is not None:
        raise operation_error.with_traceback(operation_error.__traceback__)
    if successful_observation is None:
        raise AssertionError("Gate 1 completed without a successful observation")
    return LinodeGate1Result(
        run_id=identity.run_id,
        provider_resource_id=successful_observation.provider_resource_id,
        final_provider_status=successful_observation.raw_status,
        metadata_confirmed=True,
        firewall_confirmed=True,
        backups_disabled=True,
        cleanup_confirmed=True,
    )


def cleanup_linode_gate1_resources(
    provider: LinodeComputeProvider,
    *,
    system_id: str,
    run_id: str,
    timeout_seconds: float = 600.0,
    poll_seconds: float = 5.0,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[str, ...]:
    """Delete only resources matching one complete Gate 1 identity."""

    identity = ResourceIdentity(
        system_id=system_id,
        server_unit_id="gate1-infra-lifecycle",
        run_id=run_id,
    )
    attempts = _attempt_count(timeout_seconds, poll_seconds)
    found = _exact_resources(provider, identity)
    resource_ids = {item.provider_resource_id for item in found}
    _cleanup(provider, identity, resource_ids, attempts, poll_seconds, sleeper)
    return tuple(sorted(resource_ids))


def _wait_for_discovery(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
    attempts: int,
    poll_seconds: float,
    sleeper: Callable[[float], None],
) -> RuntimeObservation:
    for attempt in range(attempts):
        found = _exact_resources(provider, identity)
        if len(found) > 1:
            raise LinodeGate1CheckError(
                "uncertain create produced more than one exactly owned Linode"
            )
        if found:
            return found[0]
        _sleep_between_attempts(attempt, attempts, poll_seconds, sleeper)
    raise LinodeGate1CheckError("created Linode could not be discovered before timeout")


def _wait_for_running(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
    provider_resource_id: str,
    attempts: int,
    poll_seconds: float,
    sleeper: Callable[[float], None],
) -> RuntimeObservation:
    for attempt in range(attempts):
        observation = provider.observe_runtime(provider_resource_id)
        if not identity.owns(observation.tags):
            raise LinodeGate1CheckError("created Linode lost its exact ownership tags")
        if observation.lifecycle is ComputeLifecycle.RUNNING:
            return observation
        if observation.lifecycle is not ComputeLifecycle.PENDING:
            raise LinodeGate1CheckError(
                f"Linode entered non-startable status {observation.raw_status!r}"
            )
        _sleep_between_attempts(attempt, attempts, poll_seconds, sleeper)
    raise LinodeGate1CheckError("Linode did not reach running before timeout")


def _cleanup(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
    tracked_ids: set[str],
    attempts: int,
    poll_seconds: float,
    sleeper: Callable[[float], None],
) -> None:
    discovery_error: ComputeProviderError | None = None
    try:
        tracked_ids.update(
            item.provider_resource_id for item in _exact_resources(provider, identity)
        )
    except ComputeProviderError as error:
        discovery_error = error
    if not tracked_ids and discovery_error is not None:
        raise discovery_error

    for provider_resource_id in sorted(tracked_ids):
        delete_uncertain = False
        try:
            provider.delete_runtime(provider_resource_id, identity)
        except ComputeActionUncertain:
            delete_uncertain = True

        for attempt in range(attempts):
            try:
                observation = provider.observe_runtime(provider_resource_id)
            except ComputeResourceNotFound:
                break
            if not identity.owns(observation.tags):
                raise LinodeGate1CleanupError(
                    f"resource {provider_resource_id} changed ownership during cleanup"
                )
            if delete_uncertain and observation.lifecycle is not ComputeLifecycle.DELETING:
                provider.delete_runtime(provider_resource_id, identity)
                delete_uncertain = False
            _sleep_between_attempts(attempt, attempts, poll_seconds, sleeper)
        else:
            raise LinodeGate1CleanupError(
                f"resource {provider_resource_id} was still present after cleanup timeout"
            )

    remaining = _exact_resources(provider, identity)
    if remaining:
        ids = ", ".join(item.provider_resource_id for item in remaining)
        raise LinodeGate1CleanupError(f"owned resources remain after cleanup: {ids}")


def _exact_resources(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
) -> list[RuntimeObservation]:
    return [
        item
        for item in provider.find_by_server_unit(identity.system_id, identity.server_unit_id)
        if identity.owns(item.tags)
    ]


def _attempt_count(timeout_seconds: float, poll_seconds: float) -> int:
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Gate 1 timeout and poll interval must be positive")
    return max(1, ceil(timeout_seconds / poll_seconds))


def _sleep_between_attempts(
    attempt: int,
    attempts: int,
    poll_seconds: float,
    sleeper: Callable[[float], None],
) -> None:
    if attempt + 1 < attempts:
        sleeper(poll_seconds)


def _cloud_init(run_id: str) -> str:
    return (
        "#cloud-config\n"
        "write_files:\n"
        "  - path: /run/mc-control-plane-gate1\n"
        "    owner: root:root\n"
        "    permissions: '0600'\n"
        f"    content: {run_id}\n"
    )
