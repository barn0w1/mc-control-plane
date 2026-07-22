from dataclasses import replace

from mc_control_plane.adapters.outbound.persistence import SQLiteUnitOfWorkFactory
from mc_control_plane.application.commands.start import RequestStart, StartServerUnit
from mc_control_plane.application.gate3_cleanup import cleanup_gate3_runtime
from mc_control_plane.application.ports.compute import ComputeLifecycle, RuntimeObservation
from mc_control_plane.domain.models import ResourceIdentity, RuntimeInstance, ServerUnit
from mc_control_plane.domain.states import DesiredState, OperationState
from tests.fakes import FakeComputeProvider, MutableClock, SequenceIds


def test_gate3_cleanup_deletes_exact_runtime_and_closes_local_state(
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
    identity = ResourceIdentity("main", server_unit.id, accepted.run_id)
    observation = RuntimeObservation(
        provider_resource_id="linode-1",
        provider="akamai",
        region=server_unit.runtime_spec.region,
        raw_status="running",
        lifecycle=ComputeLifecycle.RUNNING,
        tags=identity.tags,
    )
    compute = FakeComputeProvider()
    compute.add(observation)
    with unit_of_work() as work:
        work.runtime_instances.add(
            RuntimeInstance(
                provider_resource_id=observation.provider_resource_id,
                run_id=accepted.run_id,
                server_unit_id=server_unit.id,
                provider=observation.provider,
                region=observation.region,
                tags=observation.tags,
                provider_status=observation.raw_status,
                observed_at=clock.now(),
                created_at=clock.now(),
            )
        )
        operation = work.operations.get(accepted.operation_id)
        assert operation is not None
        work.operations.save(replace(operation, state=OperationState.SUCCEEDED))
        work.commit()

    result = cleanup_gate3_runtime(
        unit_of_work,  # type: ignore[arg-type]
        compute,
        clock,
        server_unit_id=server_unit.id,
        system_id="main",
        sleeper=lambda _seconds: None,
    )

    assert result.deleted_resource_ids == ("linode-1",)
    assert compute.deleted == ["linode-1"]
    with unit_of_work() as work:
        unit = work.server_units.get(server_unit.id)
        run = work.runs.get(accepted.run_id)
        runtime = work.runtime_instances.get_by_provider_id("linode-1")
        operation = work.operations.get(accepted.operation_id)
    assert unit is not None and unit.desired_state is DesiredState.STOPPED
    assert run is not None and run.ended_at == clock.now()
    assert runtime is not None and runtime.deleted_at == clock.now()
    assert operation is not None and operation.state is OperationState.SUCCEEDED

    repeated = cleanup_gate3_runtime(
        unit_of_work,  # type: ignore[arg-type]
        compute,
        clock,
        server_unit_id=server_unit.id,
        system_id="main",
        sleeper=lambda _seconds: None,
    )
    assert repeated.already_absent


def test_gate3_cleanup_cancels_an_incomplete_operation(
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

    result = cleanup_gate3_runtime(
        unit_of_work,  # type: ignore[arg-type]
        FakeComputeProvider(),
        clock,
        server_unit_id=server_unit.id,
        system_id="main",
        sleeper=lambda _seconds: None,
    )

    assert result.already_absent
    with unit_of_work() as work:
        operation = work.operations.get(accepted.operation_id)
    assert operation is not None and operation.state is OperationState.CANCELLED
    assert operation.last_error_code == "gate3_acceptance_cleanup"
