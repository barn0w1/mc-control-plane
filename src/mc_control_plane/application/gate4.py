"""Gate 4 acceptance workflow for restic/R2 data lifecycle on fresh Hosts."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from time import monotonic, sleep
from typing import Any, cast
from uuid import uuid4

from mc_control_plane.adapters.outbound.persistence import HostProtocolStore
from mc_control_plane.application.commands.start import RequestStart, StartServerUnit
from mc_control_plane.application.gate3_cleanup import cleanup_gate3_runtime
from mc_control_plane.application.host_protocol import HostCommandKind, HostCommandState
from mc_control_plane.application.ports.compute import ComputeProvider
from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock, IdGenerator
from mc_control_plane.application.reconciler import OperationReconciler
from mc_control_plane.domain.models import Snapshot
from mc_control_plane.domain.states import OperationState, SnapshotKind


class Gate4Error(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Gate4Result:
    initial_snapshot_id: str
    modified_snapshot_id: str
    initial_content_sha256: str
    modified_content_sha256: str
    run_ids: tuple[str, str, str]


def run_gate4_data_lifecycle(
    unit_of_work: UnitOfWorkFactory,
    store: HostProtocolStore,
    provider: ComputeProvider,
    reconciler: OperationReconciler,
    clock: Clock,
    ids: IdGenerator,
    *,
    server_unit_id: str,
    system_id: str,
    timeout_seconds: float = 1800,
    poll_seconds: float = 5,
    sleeper: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> Gate4Result:
    """Exercise seed, restore/modify, and independent fresh-Host restore phases."""

    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Gate 4 timeout and poll interval must be positive")
    report = progress or (lambda _message: None)
    runs: list[str] = []

    run1, agent1 = _start_host(
        unit_of_work,
        store,
        reconciler,
        clock,
        ids,
        server_unit_id,
        None,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    runs.append(run1)
    _command(
        store,
        agent1,
        server_unit_id,
        HostCommandKind.INIT_DATA_REPOSITORY,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    initial = _command(
        store,
        agent1,
        server_unit_id,
        HostCommandKind.WRITE_DATA_FIXTURE,
        {"revision": "initial"},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    initial_hash = _content_hash(initial)
    first_snapshot = _command(
        store,
        agent1,
        server_unit_id,
        HostCommandKind.SNAPSHOT_DATA,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    first_id = _snapshot_id(first_snapshot)
    _commit_snapshot(unit_of_work, first_id, server_unit_id, run1, clock)
    report(f"snapshot committed before delete: snapshot={first_id} run={run1}")
    _cleanup(
        unit_of_work,
        provider,
        clock,
        server_unit_id,
        system_id,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )

    run2, agent2 = _start_host(
        unit_of_work,
        store,
        reconciler,
        clock,
        ids,
        server_unit_id,
        first_id,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    runs.append(run2)
    restored_initial = _command(
        store,
        agent2,
        server_unit_id,
        HostCommandKind.RESTORE_DATA,
        {"snapshot_id": first_id},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    if _content_hash(restored_initial) != initial_hash:
        raise Gate4Error("first fresh Host restore did not reproduce the initial fixture")
    _mark_snapshot_verified(unit_of_work, first_id, server_unit_id, clock)
    report(f"snapshot independently verified: snapshot={first_id} run={run2}")
    modified = _command(
        store,
        agent2,
        server_unit_id,
        HostCommandKind.WRITE_DATA_FIXTURE,
        {"revision": "modified"},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    modified_hash = _content_hash(modified)
    if modified_hash == initial_hash:
        raise Gate4Error("fixture modification did not change the observed data digest")
    second_snapshot = _command(
        store,
        agent2,
        server_unit_id,
        HostCommandKind.SNAPSHOT_DATA,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    second_id = _snapshot_id(second_snapshot)
    _commit_snapshot(unit_of_work, second_id, server_unit_id, run2, clock)
    report(f"snapshot committed before delete: snapshot={second_id} run={run2}")
    _cleanup(
        unit_of_work,
        provider,
        clock,
        server_unit_id,
        system_id,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )

    run3, agent3 = _start_host(
        unit_of_work,
        store,
        reconciler,
        clock,
        ids,
        server_unit_id,
        second_id,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    runs.append(run3)
    restored_modified = _command(
        store,
        agent3,
        server_unit_id,
        HostCommandKind.RESTORE_DATA,
        {"snapshot_id": second_id},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    if _content_hash(restored_modified) != modified_hash:
        raise Gate4Error("second fresh Host restore did not reproduce the modified fixture")
    _mark_snapshot_verified(unit_of_work, second_id, server_unit_id, clock)
    report(f"snapshot independently verified: snapshot={second_id} run={run3}")
    _cleanup(
        unit_of_work,
        provider,
        clock,
        server_unit_id,
        system_id,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    return Gate4Result(
        first_id, second_id, initial_hash, modified_hash, cast(tuple[str, str, str], tuple(runs))
    )


def _start_host(
    unit_of_work: UnitOfWorkFactory,
    store: HostProtocolStore,
    reconciler: OperationReconciler,
    clock: Clock,
    ids: IdGenerator,
    server_unit_id: str,
    source_snapshot_id: str | None,
    timeout: float,
    poll: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> tuple[str, str]:
    accepted = RequestStart(unit_of_work, clock, ids)(
        StartServerUnit(server_unit_id, source_snapshot_id)
    )
    report(f"start accepted: run={accepted.run_id} source={source_snapshot_id or 'empty'}")
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        cycle = reconciler.run_once()
        for result in cycle.results:
            report(
                f"reconcile: operation={result.operation_id} state={result.state.value} "
                f"step={result.step.value}"
            )
        with unit_of_work() as work:
            operation = work.operations.get(accepted.operation_id)
        if operation is None:
            raise Gate4Error("Gate 4 start Operation disappeared")
        if operation.state is OperationState.SUCCEEDED:
            agent = store.get_agent_for_run(accepted.run_id)
            if agent is None or agent.status != "connected":
                raise Gate4Error("Host start completed without a connected agent")
            return accepted.run_id, agent.agent_id
        if operation.state in {OperationState.BLOCKED, OperationState.CANCELLED}:
            raise Gate4Error(
                f"Host start stopped in {operation.state.value}: "
                f"{operation.last_error_code}: {operation.last_error_message}"
            )
        sleeper(poll)
    raise Gate4Error("Host start did not complete before timeout")


def _command(
    store: HostProtocolStore,
    agent_id: str,
    server_unit_id: str,
    kind: HostCommandKind,
    extra_payload: dict[str, object],
    clock: Clock,
    timeout: float,
    poll: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> dict[str, Any]:
    command_id = f"gate4-{uuid4().hex}"
    payload = {"server_unit_id": server_unit_id, **extra_payload}
    store.queue_command(
        command_id=command_id,
        agent_id=agent_id,
        operation_id="gate4-data-lifecycle",
        step=kind.value,
        kind=kind,
        payload=payload,
        deadline=clock.now() + timedelta(seconds=timeout),
        now=clock.now(),
    )
    report(f"queued {kind.value}: command={command_id}")
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        command = store.get_command(command_id)
        state = "absent" if command is None else command.state.value
        report(f"command poll: command={command_id} state={state}")
        if command is not None and command.state is HostCommandState.SUCCEEDED:
            result = command.result or {}
            observation = result.get("observation")
            if not isinstance(observation, dict):
                raise Gate4Error("successful Host command omitted its observation")
            report(
                f"command result: command={command_id} "
                f"result={json.dumps(result, separators=(',', ':'), sort_keys=True)[:2000]}"
            )
            return cast(dict[str, Any], observation)
        if command is not None and command.state is HostCommandState.FAILED:
            raise Gate4Error(f"Host command {kind.value} failed: {command.result}")
        sleeper(poll)
    raise Gate4Error(f"Host command {kind.value} did not complete before timeout")


def _commit_snapshot(
    unit_of_work: UnitOfWorkFactory,
    snapshot_id: str,
    server_unit_id: str,
    run_id: str | None,
    clock: Clock,
) -> None:
    now = clock.now()
    with unit_of_work() as work:
        work.snapshots.add(
            Snapshot(snapshot_id, server_unit_id, run_id, SnapshotKind.MANUAL, now, None)
        )
        work.commit()


def _mark_snapshot_verified(
    unit_of_work: UnitOfWorkFactory,
    snapshot_id: str,
    server_unit_id: str,
    clock: Clock,
) -> None:
    with unit_of_work() as work:
        snapshot = work.snapshots.get(snapshot_id)
        if snapshot is None or snapshot.server_unit_id != server_unit_id:
            raise Gate4Error("restored snapshot is not owned by the Server Unit")
        if snapshot.verified_at is None:
            work.snapshots.save(replace(snapshot, verified_at=clock.now()))
        work.commit()


def _cleanup(
    unit_of_work: UnitOfWorkFactory,
    provider: ComputeProvider,
    clock: Clock,
    server_unit_id: str,
    system_id: str,
    timeout: float,
    poll: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> None:
    cleanup_gate3_runtime(
        unit_of_work,
        provider,
        clock,
        server_unit_id=server_unit_id,
        system_id=system_id,
        timeout_seconds=timeout,
        poll_seconds=poll,
        sleeper=sleeper,
        progress=report,
    )


def _content_hash(observation: dict[str, Any]) -> str:
    value = observation.get("content_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise Gate4Error("Host data observation omitted its SHA-256 digest")
    return value


def _snapshot_id(observation: dict[str, Any]) -> str:
    value = observation.get("snapshot_id")
    if not isinstance(value, str) or not value:
        raise Gate4Error("restic snapshot command omitted its snapshot ID")
    return value
