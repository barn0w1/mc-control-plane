"""Billable Gate 2 lifecycle check spanning Linode, cloud-init, agent, and Quadlet."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from time import sleep

from mc_control_plane.adapters.outbound.compute.linode import LinodeComputeProvider
from mc_control_plane.adapters.outbound.compute.linode_gate1 import DEBIAN_13_IMAGE
from mc_control_plane.adapters.outbound.host import HostBootstrapSpec, render_host_cloud_init
from mc_control_plane.adapters.outbound.persistence import HostProtocolStore
from mc_control_plane.application.host_gate2 import run_host_gate2_sequence
from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
    RuntimeObservation,
)
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec


class LinodeGate2CheckError(Exception):
    pass


class LinodeGate2CleanupError(LinodeGate2CheckError):
    pass


@dataclass(frozen=True, slots=True)
class LinodeGate2Result:
    run_id: str
    agent_id: str
    provider_resource_id: str
    first_boot_id: str
    second_boot_id: str
    cleanup_confirmed: bool


def run_linode_gate2_check(
    provider: LinodeComputeProvider,
    store: HostProtocolStore,
    spec: RuntimeSpec,
    bootstrap: HostBootstrapSpec,
    *,
    system_id: str = "mc-control-plane",
    timeout_seconds: float = 900,
    poll_seconds: float = 5,
    now: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> LinodeGate2Result:
    if spec.image != DEBIAN_13_IMAGE:
        raise LinodeGate2CheckError(f"Gate 2 requires {DEBIAN_13_IMAGE}")
    if bootstrap.run_id != bootstrap.resource_identity:
        raise LinodeGate2CheckError("Gate 2 bootstrap identity must equal its unique Run ID")
    attempts = _attempts(timeout_seconds, poll_seconds)
    clock = now or (lambda: datetime.now(UTC))
    report = progress or (lambda _message: None)
    identity = ResourceIdentity(system_id, "gate2-host-foundation", bootstrap.run_id)
    request = RuntimeCreateRequest(
        identity=identity,
        spec=spec,
        metadata_user_data=render_host_cloud_init(bootstrap),
        expires_at=clock() + timedelta(hours=2),
    )
    provider.validate_runtime_spec(spec, require_metadata=True, require_firewall=True)
    report("preflight passed")

    tracked_ids: set[str] = set()
    operation_error: BaseException | None = None
    first_boot = ""
    second_boot = ""
    resource_id = "unknown"
    try:
        if _exact(provider, identity):
            raise LinodeGate2CheckError("unique Gate 2 identity unexpectedly already exists")
        report("creating Linode with Host bootstrap")
        try:
            created = provider.create_runtime(request)
        except ComputeActionUncertain:
            created = _discover(provider, identity, attempts, poll_seconds, sleeper, report)
        tracked_ids.add(created.provider_resource_id)
        resource_id = created.provider_resource_id
        _running(provider, identity, resource_id, attempts, poll_seconds, sleeper, report)
        report(f"Linode running: resource={resource_id}")

        run_host_gate2_sequence(
            store,
            agent_id=bootstrap.agent_id,
            now=clock,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            sleeper=sleeper,
            progress=report,
        )
        agent = store.get_agent(bootstrap.agent_id)
        if agent is None or agent.boot_id is None:
            raise LinodeGate2CheckError("first Host boot ID was not observed")
        first_boot = agent.boot_id
        report(f"first Host sequence passed: boot={first_boot}")

        try:
            provider.reboot_runtime(resource_id, identity)
        except ComputeActionUncertain:
            report("reboot result uncertain; waiting for a new authenticated boot observation")
        second_boot = _new_boot(
            store,
            bootstrap.agent_id,
            first_boot,
            attempts,
            poll_seconds,
            sleeper,
            report,
        )
        report(f"Host returned after VM reboot: boot={second_boot}")
        run_host_gate2_sequence(
            store,
            agent_id=bootstrap.agent_id,
            now=clock,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            sleeper=sleeper,
            progress=report,
        )
    except BaseException as error:
        operation_error = error

    try:
        _cleanup(provider, identity, tracked_ids, attempts, poll_seconds, sleeper, report)
    except BaseException as cleanup_error:
        raise LinodeGate2CleanupError(
            "owned Gate 2 Linode cleanup could not be confirmed"
        ) from cleanup_error
    if operation_error is not None:
        raise operation_error.with_traceback(operation_error.__traceback__)
    return LinodeGate2Result(
        run_id=bootstrap.run_id,
        agent_id=bootstrap.agent_id,
        provider_resource_id=resource_id,
        first_boot_id=first_boot,
        second_boot_id=second_boot,
        cleanup_confirmed=True,
    )


def cleanup_linode_gate2_resources(
    provider: LinodeComputeProvider,
    *,
    system_id: str,
    run_id: str,
    timeout_seconds: float = 600,
    poll_seconds: float = 5,
    sleeper: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    identity = ResourceIdentity(system_id, "gate2-host-foundation", run_id)
    found = _exact(provider, identity)
    ids = {item.provider_resource_id for item in found}
    _cleanup(
        provider,
        identity,
        ids,
        _attempts(timeout_seconds, poll_seconds),
        poll_seconds,
        sleeper,
        progress or (lambda _message: None),
    )
    return tuple(sorted(ids))


def _discover(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
    attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> RuntimeObservation:
    for attempt in range(attempts):
        found = _exact(provider, identity)
        report(f"discovery poll {attempt + 1}/{attempts}: matches={len(found)}")
        if len(found) > 1:
            raise LinodeGate2CheckError("uncertain create produced multiple owned Linodes")
        if found:
            return found[0]
        _pause(attempt, attempts, interval, sleeper)
    raise LinodeGate2CheckError("created Linode could not be discovered")


def _running(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
    resource_id: str,
    attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> None:
    for attempt in range(attempts):
        observation = provider.observe_runtime(resource_id)
        report(f"startup poll {attempt + 1}/{attempts}: status={observation.raw_status}")
        if not identity.owns(observation.tags):
            raise LinodeGate2CheckError("created Linode lost ownership tags")
        if observation.lifecycle is ComputeLifecycle.RUNNING:
            return
        if observation.lifecycle is not ComputeLifecycle.PENDING:
            raise LinodeGate2CheckError(f"Linode entered {observation.raw_status}")
        _pause(attempt, attempts, interval, sleeper)
    raise LinodeGate2CheckError("Linode did not reach running")


def _new_boot(
    store: HostProtocolStore,
    agent_id: str,
    previous: str,
    attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> str:
    for attempt in range(attempts):
        agent = store.get_agent(agent_id)
        boot = "absent" if agent is None or agent.boot_id is None else agent.boot_id
        report(f"reboot poll {attempt + 1}/{attempts}: boot={boot}")
        if (
            agent is not None
            and agent.status == "connected"
            and agent.boot_id not in (None, previous)
        ):
            states = agent.service_states or {}
            if states.get("agent") != "active":
                raise LinodeGate2CheckError("agent service was not active after reboot")
            if states.get("fixture") not in ("inactive", "not-found"):
                raise LinodeGate2CheckError("fixture started unexpectedly after reboot")
            return agent.boot_id
        _pause(attempt, attempts, interval, sleeper)
    raise LinodeGate2CheckError("Host agent did not return with a new boot ID")


def _cleanup(
    provider: LinodeComputeProvider,
    identity: ResourceIdentity,
    ids: set[str],
    attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> None:
    ids.update(item.provider_resource_id for item in _exact(provider, identity))
    for resource_id in sorted(ids):
        report(f"deleting owned Linode: resource={resource_id}")
        delete_uncertain = False
        try:
            provider.delete_runtime(resource_id, identity)
        except ComputeActionUncertain:
            delete_uncertain = True
        for attempt in range(attempts):
            try:
                observation = provider.observe_runtime(resource_id)
            except ComputeResourceNotFound:
                report(f"cleanup confirmed absent: resource={resource_id}")
                break
            if not identity.owns(observation.tags):
                raise LinodeGate2CleanupError("resource ownership changed during cleanup")
            if delete_uncertain and observation.lifecycle is not ComputeLifecycle.DELETING:
                provider.delete_runtime(resource_id, identity)
                delete_uncertain = False
            _pause(attempt, attempts, interval, sleeper)
        else:
            raise LinodeGate2CleanupError(f"resource {resource_id} remained after cleanup")
    if _exact(provider, identity):
        raise LinodeGate2CleanupError("owned Gate 2 resources remain")


def _exact(provider: LinodeComputeProvider, identity: ResourceIdentity) -> list[RuntimeObservation]:
    return [
        item
        for item in provider.find_by_server_unit(identity.system_id, identity.server_unit_id)
        if identity.owns(item.tags)
    ]


def _attempts(timeout: float, poll: float) -> int:
    if timeout <= 0 or poll <= 0:
        raise ValueError("Gate 2 timeout and poll interval must be positive")
    return max(1, ceil(timeout / poll))


def _pause(
    attempt: int,
    attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
) -> None:
    if attempt + 1 < attempts:
        sleeper(interval)
