"""Gate 5 acceptance workflow for the Paper Minecraft lifecycle."""

import json
import re
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

_DIGEST_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]+@sha256:[0-9a-f]{64}$")
_MINECRAFT_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
_PAPER_BUILD = re.compile(r"^[1-9][0-9]*$")
_MEMORY = re.compile(r"^[1-9][0-9]*(?:M|G)$")


class Gate5Error(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Gate5Result:
    manual_snapshot_id: str
    stop_snapshot_id: str
    restored_stop_snapshot_id: str
    manual_content_sha256: str
    stop_content_sha256: str
    run_ids: tuple[str, str]


def run_gate5_minecraft_lifecycle(
    unit_of_work: UnitOfWorkFactory,
    store: HostProtocolStore,
    provider: ComputeProvider,
    reconciler: OperationReconciler,
    clock: Clock,
    ids: IdGenerator,
    *,
    server_unit_id: str,
    system_id: str,
    minecraft_image: str,
    minecraft_version: str,
    paper_build: str,
    memory: str,
    eula_accepted: bool,
    timeout_seconds: float = 2400,
    poll_seconds: float = 5,
    sleeper: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> Gate5Result:
    """Exercise Paper start, live snapshot, stop, restore, and restart on fresh Hosts."""

    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Gate 5 timeout and poll interval must be positive")
    if not eula_accepted:
        raise ValueError("Minecraft EULA acceptance is required")
    _validate_minecraft_spec(minecraft_image, minecraft_version, paper_build, memory)
    report = progress or (lambda _message: None)
    minecraft = {
        "image": minecraft_image,
        "minecraft_version": minecraft_version,
        "paper_build": paper_build,
        "memory": memory,
        "eula": eula_accepted,
    }

    first_run, first_agent = _start_host(
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
    _command(
        store,
        first_agent,
        server_unit_id,
        HostCommandKind.INIT_DATA_REPOSITORY,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    _apply_and_start(
        store,
        first_agent,
        server_unit_id,
        minecraft,
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    manual = _command(
        store,
        first_agent,
        server_unit_id,
        HostCommandKind.SNAPSHOT_MINECRAFT,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    manual_id = _snapshot_id(manual)
    manual_hash = _content_hash(manual)
    _commit_snapshot(unit_of_work, manual_id, server_unit_id, first_run, SnapshotKind.MANUAL, clock)
    report(f"live snapshot committed: snapshot={manual_id} run={first_run}")
    _stop_minecraft(
        store,
        first_agent,
        server_unit_id,
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    stopped = _command(
        store,
        first_agent,
        server_unit_id,
        HostCommandKind.SNAPSHOT_DATA,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    stop_id = _snapshot_id(stopped)
    stop_hash = _content_hash(stopped)
    _commit_snapshot(unit_of_work, stop_id, server_unit_id, first_run, SnapshotKind.STOP, clock)
    report(f"stopped snapshot committed before delete: snapshot={stop_id} run={first_run}")
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

    second_run, second_agent = _start_host(
        unit_of_work,
        store,
        reconciler,
        clock,
        ids,
        server_unit_id,
        stop_id,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    restored = _command(
        store,
        second_agent,
        server_unit_id,
        HostCommandKind.RESTORE_DATA,
        {"snapshot_id": stop_id},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    if _content_hash(restored) != stop_hash:
        raise Gate5Error("fresh Host restore did not reproduce the stopped Minecraft data")
    _mark_snapshot_verified(unit_of_work, stop_id, server_unit_id, clock)
    report(f"stopped snapshot independently verified: snapshot={stop_id} run={second_run}")
    _apply_and_start(
        store,
        second_agent,
        server_unit_id,
        minecraft,
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    _stop_minecraft(
        store,
        second_agent,
        server_unit_id,
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    final_snapshot = _command(
        store,
        second_agent,
        server_unit_id,
        HostCommandKind.SNAPSHOT_DATA,
        {},
        clock,
        timeout_seconds,
        poll_seconds,
        sleeper,
        report,
    )
    final_id = _snapshot_id(final_snapshot)
    _commit_snapshot(unit_of_work, final_id, server_unit_id, second_run, SnapshotKind.STOP, clock)
    report(f"restored-run stop snapshot committed: snapshot={final_id} run={second_run}")
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
    return Gate5Result(
        manual_id,
        stop_id,
        final_id,
        manual_hash,
        stop_hash,
        (first_run, second_run),
    )


def _apply_and_start(
    store: HostProtocolStore,
    agent_id: str,
    server_unit_id: str,
    minecraft: dict[str, object],
    clock: Clock,
    timeout: float,
    poll: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> None:
    applied = _command(
        store,
        agent_id,
        server_unit_id,
        HostCommandKind.APPLY_MINECRAFT,
        minecraft,
        clock,
        timeout,
        poll,
        sleeper,
        report,
    )
    _require_minecraft_state(applied, "stopped")
    started = _command(
        store,
        agent_id,
        server_unit_id,
        HostCommandKind.START_MINECRAFT,
        {},
        clock,
        timeout,
        poll,
        sleeper,
        report,
    )
    _require_minecraft_state(started, "ready")
    observed = _command(
        store,
        agent_id,
        server_unit_id,
        HostCommandKind.OBSERVE_MINECRAFT,
        {},
        clock,
        timeout,
        poll,
        sleeper,
        report,
    )
    _require_minecraft_state(observed, "ready")


def _stop_minecraft(
    store: HostProtocolStore,
    agent_id: str,
    server_unit_id: str,
    clock: Clock,
    timeout: float,
    poll: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> None:
    stopped = _command(
        store,
        agent_id,
        server_unit_id,
        HostCommandKind.STOP_MINECRAFT,
        {},
        clock,
        timeout,
        poll,
        sleeper,
        report,
    )
    _require_minecraft_state(stopped, "stopped")


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
            raise Gate5Error("Gate 5 start Operation disappeared")
        if operation.state is OperationState.SUCCEEDED:
            agent = store.get_agent_for_run(accepted.run_id)
            if agent is None or agent.status != "connected":
                raise Gate5Error("Host start completed without a connected agent")
            return accepted.run_id, agent.agent_id
        if operation.state in {OperationState.BLOCKED, OperationState.CANCELLED}:
            raise Gate5Error(
                f"Host start stopped in {operation.state.value}: "
                f"{operation.last_error_code}: {operation.last_error_message}"
            )
        sleeper(poll)
    raise Gate5Error("Host start did not complete before timeout")


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
    command_id = f"gate5-{uuid4().hex}"
    payload = {"server_unit_id": server_unit_id, **extra_payload}
    store.queue_command(
        command_id=command_id,
        agent_id=agent_id,
        operation_id="gate5-minecraft-lifecycle",
        step=kind.value,
        kind=kind,
        payload=payload,
        deadline=clock.now() + timedelta(seconds=timeout),
        now=clock.now(),
    )
    report(f"queued {kind.value}: command={command_id}")
    deadline = monotonic() + timeout
    last_state: str | None = None
    while monotonic() < deadline:
        command = store.get_command(command_id)
        state = "absent" if command is None else command.state.value
        if state != last_state:
            report(f"command poll: command={command_id} state={state}")
            last_state = state
        if command is not None and command.state is HostCommandState.SUCCEEDED:
            result = command.result or {}
            observation = result.get("observation")
            if not isinstance(observation, dict):
                raise Gate5Error("successful Host command omitted its observation")
            report(
                f"command result: command={command_id} "
                f"result={json.dumps(result, separators=(',', ':'), sort_keys=True)[:3000]}"
            )
            return cast(dict[str, Any], observation)
        if command is not None and command.state is HostCommandState.FAILED:
            raise Gate5Error(f"Host command {kind.value} failed: {command.result}")
        sleeper(poll)
    raise Gate5Error(f"Host command {kind.value} did not complete before timeout")


def _commit_snapshot(
    unit_of_work: UnitOfWorkFactory,
    snapshot_id: str,
    server_unit_id: str,
    run_id: str,
    kind: SnapshotKind,
    clock: Clock,
) -> None:
    with unit_of_work() as work:
        work.snapshots.add(Snapshot(snapshot_id, server_unit_id, run_id, kind, clock.now(), None))
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
            raise Gate5Error("restored snapshot is not owned by the Server Unit")
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


def _require_minecraft_state(observation: dict[str, Any], expected: str) -> None:
    actual = observation.get("minecraft")
    if actual != expected:
        raise Gate5Error(f"Minecraft state is {actual!r}; expected {expected!r}")


def _content_hash(observation: dict[str, Any]) -> str:
    value = observation.get("content_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise Gate5Error("Host data observation omitted its SHA-256 digest")
    return value


def _snapshot_id(observation: dict[str, Any]) -> str:
    value = observation.get("snapshot_id")
    if not isinstance(value, str) or not value:
        raise Gate5Error("restic snapshot command omitted its snapshot ID")
    return value


def _validate_minecraft_spec(
    image: str, minecraft_version: str, paper_build: str, memory: str
) -> None:
    if not _DIGEST_IMAGE.fullmatch(image):
        raise ValueError("Minecraft image must be pinned by SHA-256 digest")
    if not _MINECRAFT_VERSION.fullmatch(minecraft_version):
        raise ValueError("Minecraft version must be an exact numeric version")
    if not _PAPER_BUILD.fullmatch(paper_build):
        raise ValueError("Paper build must be a positive integer")
    if not _MEMORY.fullmatch(memory):
        raise ValueError("Minecraft memory must use an M or G suffix")
