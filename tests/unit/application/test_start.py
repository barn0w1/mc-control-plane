from datetime import timedelta

import pytest

from mc_control_plane.adapters.outbound.persistence import SQLiteUnitOfWorkFactory
from mc_control_plane.application.commands.start import (
    RequestStart,
    StartServerUnit,
)
from mc_control_plane.application.ports.compute import (
    ComputeLifecycle,
    ComputeProviderUnavailable,
    ComputeRequestRejected,
    ComputeResourceNotFound,
    RuntimeObservation,
)
from mc_control_plane.application.ports.host import HostObservation
from mc_control_plane.application.workflows.start import (
    StartWorkflow,
    delete_owned_runtime,
)
from mc_control_plane.domain.errors import ActiveRunExists, ResourceOwnershipMismatch
from mc_control_plane.domain.models import ResourceIdentity, ServerUnit
from mc_control_plane.domain.states import DesiredState, OperationState, StartStep
from tests.fakes import FakeComputeProvider, FakeHostManager, MutableClock, SequenceIds


def _add_server_unit(unit_of_work: SQLiteUnitOfWorkFactory, server_unit: ServerUnit) -> None:
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.commit()


def _workflow(
    unit_of_work: SQLiteUnitOfWorkFactory,
    compute: FakeComputeProvider,
    clock: MutableClock,
    host: FakeHostManager | None = None,
) -> StartWorkflow:
    manager = host or FakeHostManager()
    return StartWorkflow(
        unit_of_work,
        compute,
        clock,
        system_id="main",
        host_bootstrap=manager,
        host_observations=manager,
    )


def test_start_request_reserves_run_and_rejects_duplicate(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    request = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))

    accepted = request(StartServerUnit(server_unit.id))

    assert accepted.run_id == "run-1"
    with unit_of_work() as work:
        assert work.runs.get_active(server_unit.id) is not None
        assert work.server_units.get(server_unit.id).desired_state is DesiredState.RUNNING

    duplicate = RequestStart(unit_of_work, clock, SequenceIds("run-2", "operation-2"))
    with pytest.raises(ActiveRunExists):
        duplicate(StartServerUnit(server_unit.id))


def test_start_workflow_creates_once_and_advances_to_host_boundary(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    host = FakeHostManager()
    workflow = _workflow(unit_of_work, compute, clock, host)

    assert workflow.reconcile(accepted.operation_id).step is StartStep.CREATE_RUNTIME
    assert workflow.reconcile(accepted.operation_id).step is StartStep.WAIT_PROVIDER
    assert compute.create_count == 1

    compute.set_status("linode-1", "running", ComputeLifecycle.RUNNING)
    result = workflow.reconcile(accepted.operation_id)

    assert result.step is StartStep.WAIT_HOST
    assert result.state is OperationState.RUNNING
    assert compute.create_count == 1
    assert compute.resources["linode-1"].has_user_data is True

    host.observations[accepted.run_id] = HostObservation(
        run_id=accepted.run_id,
        agent_id=f"agent-{accepted.run_id}",
        protocol_version=1,
        agent_version="0.2.0",
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
    completed = workflow.reconcile(accepted.operation_id)
    assert completed.step is StartStep.COMPLETE
    assert completed.state is OperationState.SUCCEEDED


def test_uncertain_create_is_reobserved_and_adopted_after_restart(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    compute.uncertain_next_create = True
    host = FakeHostManager()
    workflow = _workflow(unit_of_work, compute, clock, host)

    workflow.reconcile(accepted.operation_id)
    uncertain = workflow.reconcile(accepted.operation_id)
    assert uncertain.state is OperationState.RETRY_WAIT
    assert uncertain.step is StartStep.DISCOVER_RUNTIME

    clock.advance(timedelta(seconds=5))
    restarted = _workflow(unit_of_work, compute, clock, host)
    adopted = restarted.reconcile(accepted.operation_id)

    assert adopted.step is StartStep.WAIT_PROVIDER
    assert compute.create_count == 1


def test_existing_runtime_for_different_run_blocks_creation(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-new", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    other = ResourceIdentity(system_id="main", server_unit_id=server_unit.id, run_id="run-old")
    compute.add(
        RuntimeObservation(
            provider_resource_id="linode-old",
            provider="akamai",
            region=server_unit.runtime_spec.region,
            raw_status="running",
            lifecycle=ComputeLifecycle.RUNNING,
            tags=other.tags,
        )
    )

    result = _workflow(unit_of_work, compute, clock).reconcile(accepted.operation_id)

    assert result.state is OperationState.BLOCKED
    assert compute.create_count == 0


def test_non_startable_compute_state_blocks_instead_of_waiting_forever(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    workflow = _workflow(unit_of_work, compute, clock)
    workflow.reconcile(accepted.operation_id)
    workflow.reconcile(accepted.operation_id)
    compute.set_status("linode-1", "billing_suspension", ComputeLifecycle.BLOCKED)

    result = workflow.reconcile(accepted.operation_id)

    assert result.state is OperationState.BLOCKED


def test_delete_guard_never_deletes_resource_with_wrong_owner() -> None:
    compute = FakeComputeProvider()
    owner = ResourceIdentity(system_id="main", server_unit_id="unit", run_id="run-1")
    other = ResourceIdentity(system_id="main", server_unit_id="unit", run_id="run-2")
    compute.add(
        RuntimeObservation(
            provider_resource_id="linode-1",
            provider="akamai",
            region="us-ord",
            raw_status="running",
            lifecycle=ComputeLifecycle.RUNNING,
            tags=other.tags,
        )
    )

    with pytest.raises(ResourceOwnershipMismatch):
        delete_owned_runtime(compute, owner, "linode-1")

    assert compute.deleted == []


def test_provider_read_failure_is_persisted_for_retry(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    compute.find_error = ComputeProviderUnavailable("temporary API failure")

    result = _workflow(unit_of_work, compute, clock).reconcile(accepted.operation_id)

    assert result.state is OperationState.RETRY_WAIT
    assert result.step is StartStep.DISCOVER_RUNTIME
    with unit_of_work() as work:
        operation = work.operations.get(accepted.operation_id)
        assert operation is not None
        assert operation.last_error_code == "compute_provider_unavailable"
        assert operation.next_attempt_at == clock.now() + timedelta(seconds=5)


def test_definitive_create_rejection_blocks_with_attempt_recorded(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    compute.create_error = ComputeRequestRejected("invalid image")
    workflow = _workflow(unit_of_work, compute, clock)
    workflow.reconcile(accepted.operation_id)

    result = workflow.reconcile(accepted.operation_id)

    assert result.state is OperationState.BLOCKED
    with unit_of_work() as work:
        operation = work.operations.get(accepted.operation_id)
        assert operation is not None
        assert operation.attempt_count == 1
        assert operation.last_error_code == "compute_request_rejected"


def test_missing_recorded_runtime_blocks_for_manual_review(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    _add_server_unit(unit_of_work, server_unit)
    accepted = RequestStart(unit_of_work, clock, SequenceIds("run-1", "operation-1"))(
        StartServerUnit(server_unit.id)
    )
    compute = FakeComputeProvider()
    workflow = _workflow(unit_of_work, compute, clock)
    workflow.reconcile(accepted.operation_id)
    workflow.reconcile(accepted.operation_id)
    compute.observe_error = ComputeResourceNotFound("linode-1")

    result = workflow.reconcile(accepted.operation_id)

    assert result.state is OperationState.BLOCKED
