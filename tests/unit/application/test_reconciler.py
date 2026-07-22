from datetime import timedelta

from mc_control_plane.adapters.outbound.persistence import SQLiteUnitOfWorkFactory
from mc_control_plane.application.commands.start import RequestStart, StartServerUnit
from mc_control_plane.application.ports.compute import ComputeLifecycle
from mc_control_plane.application.ports.host import HostObservation
from mc_control_plane.application.queries.status import GetServerUnitStatus
from mc_control_plane.application.reconciler import OperationReconciler
from mc_control_plane.application.workflows.start import StartWorkflow
from mc_control_plane.domain.models import ServerUnit
from mc_control_plane.domain.states import OperationState, StartStep
from tests.fakes import FakeComputeProvider, FakeHostManager, MutableClock, SequenceIds


def _ready(run_id: str, clock: MutableClock) -> HostObservation:
    return HostObservation(
        run_id=run_id,
        agent_id=f"agent-{run_id}",
        protocol_version=1,
        agent_version="0.2.1",
        status="connected",
        boot_id="boot-1",
        capabilities={
            "os_id": "debian",
            "os_version": "13",
            "python": "Python 3.13.5",
            "podman": "podman version 5.4.2",
            "restic": "restic 0.18.0",
            "quadlet": True,
        },
        service_states={"agent": "active", "fixture": "not-found"},
        observed_at=clock.now(),
    )


def _reconciler(
    unit_of_work: SQLiteUnitOfWorkFactory,
    compute: FakeComputeProvider,
    host: FakeHostManager,
    clock: MutableClock,
) -> OperationReconciler:
    workflow = StartWorkflow(
        unit_of_work,  # type: ignore[arg-type]
        compute,
        clock,
        "main",
        host,
        host,
    )
    return OperationReconciler(unit_of_work, workflow, clock)  # type: ignore[arg-type]


def test_reconciler_resumes_each_persisted_step_and_completes_host_boundary(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.commit()
    accepted = RequestStart(  # type: ignore[arg-type]
        unit_of_work, clock, SequenceIds("run-1", "operation-1")
    )(StartServerUnit(server_unit.id))
    compute = FakeComputeProvider()
    host = FakeHostManager()

    assert (
        _reconciler(unit_of_work, compute, host, clock).run_once().results[0].step
        is StartStep.CREATE_RUNTIME
    )
    assert (
        _reconciler(unit_of_work, compute, host, clock).run_once().results[0].step
        is StartStep.WAIT_PROVIDER
    )
    assert compute.create_count == 1

    pending = _reconciler(unit_of_work, compute, host, clock).run_once().results[0]
    assert pending.state is OperationState.RETRY_WAIT
    assert _reconciler(unit_of_work, compute, host, clock).run_once().due_count == 0

    compute.set_status("linode-1", "running", ComputeLifecycle.RUNNING)
    clock.advance(timedelta(seconds=5))
    waiting_host = _reconciler(unit_of_work, compute, host, clock).run_once().results[0]
    assert waiting_host.step is StartStep.WAIT_HOST
    no_host = _reconciler(unit_of_work, compute, host, clock).run_once().results[0]
    assert no_host.state is OperationState.RETRY_WAIT

    host.observations[accepted.run_id] = _ready(accepted.run_id, clock)
    clock.advance(timedelta(seconds=5))
    completed = _reconciler(unit_of_work, compute, host, clock).run_once().results[0]

    assert completed.state is OperationState.SUCCEEDED
    assert completed.step is StartStep.COMPLETE
    assert compute.create_count == 1
    assert host.bootstrap_calls == [accepted.run_id]
    status = GetServerUnitStatus(unit_of_work, host, clock)(server_unit.id)  # type: ignore[arg-type]
    assert status.operation is not None
    assert status.operation["state"] == "succeeded"
    assert status.provider is not None and status.provider["status"] == "running"
    assert status.host is not None and status.host["status"] == "connected"


def test_incompatible_agent_blocks_without_additional_compute_action(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.commit()
    accepted = RequestStart(  # type: ignore[arg-type]
        unit_of_work, clock, SequenceIds("run-2", "operation-2")
    )(StartServerUnit(server_unit.id))
    compute = FakeComputeProvider()
    host = FakeHostManager()
    reconciler = _reconciler(unit_of_work, compute, host, clock)
    reconciler.run_once()
    reconciler.run_once()
    compute.set_status("linode-1", "running", ComputeLifecycle.RUNNING)
    reconciler.run_once()
    incompatible = _ready(accepted.run_id, clock)
    host.observations[accepted.run_id] = HostObservation(
        run_id=incompatible.run_id,
        agent_id=incompatible.agent_id,
        protocol_version=incompatible.protocol_version,
        agent_version="9.9.9",
        status=incompatible.status,
        boot_id=incompatible.boot_id,
        capabilities=incompatible.capabilities,
        service_states=incompatible.service_states,
        observed_at=incompatible.observed_at,
    )

    result = reconciler.run_once().results[0]

    assert result.state is OperationState.BLOCKED
    assert compute.create_count == 1
    with unit_of_work() as work:
        operation = work.operations.get(accepted.operation_id)
        assert operation is not None
        assert operation.last_error_code == "host_agent_incompatible"


def test_unexpected_error_defers_the_latest_persisted_operation(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.commit()
    accepted = RequestStart(  # type: ignore[arg-type]
        unit_of_work, clock, SequenceIds("run-3", "operation-3")
    )(StartServerUnit(server_unit.id))
    compute = FakeComputeProvider()
    host = FakeHostManager()
    reconciler = _reconciler(unit_of_work, compute, host, clock)
    reconciler.run_once()
    compute.create_error = RuntimeError("unexpected SDK failure")  # type: ignore[assignment]

    cycle = reconciler.run_once()

    assert cycle.failures[0].error_type == "RuntimeError"
    with unit_of_work() as work:
        operation = work.operations.get(accepted.operation_id)
    assert operation is not None
    assert operation.state is OperationState.RETRY_WAIT
    assert operation.attempt_count == 1
    assert operation.last_error_code == "reconcile_unexpected_error"
