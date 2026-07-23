from dataclasses import replace
from datetime import timedelta

import pytest

from mc_control_plane.adapters.outbound.persistence import SQLiteUnitOfWorkFactory
from mc_control_plane.application.commands.lifecycle import (
    RequestOperationRetry,
    RequestSnapshot,
    RequestStop,
)
from mc_control_plane.application.commands.start import RequestStart, StartServerUnit
from mc_control_plane.application.ports.compute import ComputeLifecycle
from mc_control_plane.application.ports.host import HostObservation
from mc_control_plane.application.workflows.snapshot import SnapshotWorkflow
from mc_control_plane.application.workflows.start import StartWorkflow
from mc_control_plane.application.workflows.stop import StopWorkflow
from mc_control_plane.domain.errors import OperationConflict
from mc_control_plane.domain.models import MinecraftSpec, ServerUnit
from mc_control_plane.domain.states import OperationState, SnapshotStep
from tests.fakes import (
    FakeComputeProvider,
    FakeHostCommandGateway,
    FakeHostManager,
    MutableClock,
    SequenceIds,
)

_MINECRAFT_IMAGE = (
    "docker.io/itzg/minecraft-server@sha256:"
    "9faa6aefeedd5a883c3ee241653fd1421529bdbafc428d0513e43cae0f2b7d68"
)


def test_normal_start_snapshot_stop_is_serial_and_snapshot_precedes_delete(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    configured = replace(
        server_unit,
        minecraft_spec=MinecraftSpec(_MINECRAFT_IMAGE, "1.21.8", "1", "512M", True),
    )
    with unit_of_work() as work:
        work.server_units.add(configured)
        work.commit()

    start = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-start"))(
        StartServerUnit(configured.id, use_latest_snapshot=True, require_minecraft_spec=True)
    )
    compute = FakeComputeProvider()
    host = FakeHostManager()
    commands = FakeHostCommandGateway(start.run_id, clock.now())
    start_workflow = StartWorkflow(
        unit_of_work,
        compute,
        clock,
        system_id="main",
        host_bootstrap=host,
        host_observations=host,
        host_commands=commands,
    )

    start_workflow.reconcile(start.operation_id)
    start_workflow.reconcile(start.operation_id)
    compute.set_status("linode-1", "running", ComputeLifecycle.RUNNING)
    start_workflow.reconcile(start.operation_id)
    host.observations[start.run_id] = HostObservation(
        start.run_id,
        commands.agent.agent_id,
        1,
        "0.3.4",
        "connected",
        "boot-1",
        {
            "os_id": "debian",
            "os_version": "13",
            "python": "Python 3.13.5",
            "podman": "podman version 5.4.2",
            "restic": "restic 0.18.0",
            "quadlet": True,
        },
        {"agent": "active"},
        clock.now(),
    )
    start_workflow.reconcile(start.operation_id)
    start_workflow.reconcile(start.operation_id)
    commands.succeed(
        "operation-operation-start-init_data_repository-attempt-1",
        {"repository": "ready", "state": "created"},
    )
    start_workflow.reconcile(start.operation_id)
    start_workflow.reconcile(start.operation_id)
    commands.succeed(
        "operation-operation-start-apply_workload-attempt-1",
        {"minecraft": "stopped"},
    )
    start_workflow.reconcile(start.operation_id)
    start_workflow.reconcile(start.operation_id)
    commands.succeed(
        "operation-operation-start-start_workload-attempt-1",
        {"minecraft": "ready"},
    )
    started = start_workflow.reconcile(start.operation_id)
    assert started.state is OperationState.SUCCEEDED

    snapshot = RequestSnapshot(unit_of_work, clock, SequenceIds("operation-snapshot"))(
        configured.id
    )
    with pytest.raises(OperationConflict) as conflict:
        RequestStop(unit_of_work, clock, SequenceIds("operation-conflict"))(configured.id)
    assert conflict.value.operation_id == snapshot.operation_id

    with unit_of_work() as work:
        current = work.operations.get(snapshot.operation_id)
        assert current is not None
        work.operations.save(
            replace(
                current,
                state=OperationState.BLOCKED,
                step=SnapshotStep.WAIT_SNAPSHOT,
            )
        )
        work.commit()
    retried = RequestOperationRetry(unit_of_work, clock)(snapshot.operation_id)
    assert retried.step == SnapshotStep.CREATE_SNAPSHOT.value
    assert retried.attempt_count == 1

    snapshot_workflow = SnapshotWorkflow(unit_of_work, commands, clock)
    snapshot_workflow.reconcile(snapshot.operation_id)
    commands.succeed(
        "operation-operation-snapshot-create_snapshot-attempt-1",
        {"snapshot_id": "manual-1", "minecraft": "ready"},
    )
    assert snapshot_workflow.reconcile(snapshot.operation_id).state is OperationState.SUCCEEDED

    clock.advance(timedelta(seconds=1))
    stop = RequestStop(unit_of_work, clock, SequenceIds("operation-stop"))(configured.id)
    stop_workflow = StopWorkflow(
        unit_of_work,
        commands,
        compute,
        clock,
        system_id="main",
    )
    stop_workflow.reconcile(stop.operation_id)
    commands.succeed(
        "operation-operation-stop-stop_workload-attempt-0",
        {"minecraft": "stopped"},
    )
    stop_workflow.reconcile(stop.operation_id)
    stop_workflow.reconcile(stop.operation_id)
    commands.succeed(
        "operation-operation-stop-create_snapshot-attempt-0",
        {"snapshot_id": "stop-1", "data_state": "ready"},
    )
    stop_workflow.reconcile(stop.operation_id)
    with unit_of_work() as work:
        assert work.snapshots.get("stop-1") is not None
        assert work.runtime_instances.get_active_for_run(start.run_id) is not None

    stop_workflow.reconcile(stop.operation_id)
    stopped = stop_workflow.reconcile(stop.operation_id)
    assert stopped.state is OperationState.SUCCEEDED
    assert compute.deleted == ["linode-1"]
    with unit_of_work() as work:
        assert work.runs.get_active(configured.id) is None

    restarted = RequestStart(unit_of_work, clock, SequenceIds("run-2", "operation-restart"))(
        StartServerUnit(
            configured.id,
            use_latest_snapshot=True,
            require_minecraft_spec=True,
        )
    )
    with unit_of_work() as work:
        restarted_run = work.runs.get(restarted.run_id)
    assert restarted_run is not None
    assert restarted_run.source_snapshot_id == "stop-1"
    assert restarted_run.minecraft_spec == configured.minecraft_spec

    restored_host = FakeHostManager()
    restored_commands = FakeHostCommandGateway(restarted.run_id, clock.now())
    restored_workflow = StartWorkflow(
        unit_of_work,
        compute,
        clock,
        system_id="main",
        host_bootstrap=restored_host,
        host_observations=restored_host,
        host_commands=restored_commands,
    )
    restored_workflow.reconcile(restarted.operation_id)
    restored_workflow.reconcile(restarted.operation_id)
    compute.set_status("linode-2", "running", ComputeLifecycle.RUNNING)
    restored_workflow.reconcile(restarted.operation_id)
    restored_host.observations[restarted.run_id] = replace(
        host.observations[start.run_id],
        run_id=restarted.run_id,
        agent_id=restored_commands.agent.agent_id,
    )
    restored_workflow.reconcile(restarted.operation_id)
    restored_workflow.reconcile(restarted.operation_id)
    restore_id = "operation-operation-restart-restore_snapshot-attempt-1"
    restore_command = restored_commands.get_command(restore_id)
    assert restore_command is not None
    assert restore_command.payload["snapshot_id"] == "stop-1"
    restored_commands.succeed(
        restore_id,
        {"snapshot_id": "stop-1", "data_state": "ready"},
    )
    restored_workflow.reconcile(restarted.operation_id)
    with unit_of_work() as work:
        restored_snapshot = work.snapshots.get("stop-1")
    assert restored_snapshot is not None
    assert restored_snapshot.verified_at == clock.now()
