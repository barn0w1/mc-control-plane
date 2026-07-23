"""Accept normal-operation snapshot and stop requests."""

from dataclasses import dataclass, replace

from mc_control_plane.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock, IdGenerator
from mc_control_plane.domain.errors import (
    ActiveRunNotFound,
    OperationConflict,
    OperationNotBlocked,
    OperationNotFound,
    PersistenceConflict,
    ServerUnitNotFound,
)
from mc_control_plane.domain.models import Operation
from mc_control_plane.domain.states import (
    DesiredState,
    OperationKind,
    OperationState,
    SnapshotStep,
    StartStep,
    StopStep,
)


@dataclass(frozen=True, slots=True)
class LifecycleAccepted:
    operation_id: str
    run_id: str


def _require_idle(work: UnitOfWork, server_unit_id: str) -> None:
    active = work.operations.get_active(server_unit_id)
    if active is not None:
        raise OperationConflict(
            server_unit_id,
            operation_id=active.id,
            kind=active.kind.value,
            state=active.state.value,
            step=str(active.step),
        )


class RequestSnapshot:
    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock, ids: IdGenerator) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._ids = ids

    def __call__(self, server_unit_id: str) -> LifecycleAccepted:
        now = self._clock.now()
        operation_id = self._ids.new()
        try:
            with self._unit_of_work() as work:
                unit = work.server_units.get(server_unit_id)
                if unit is None:
                    raise ServerUnitNotFound(server_unit_id)
                _require_idle(work, server_unit_id)
                run = work.runs.get_active(server_unit_id)
                if run is None:
                    raise ActiveRunNotFound(server_unit_id)
                operation = Operation(
                    operation_id,
                    server_unit_id,
                    run.id,
                    OperationKind.SNAPSHOT,
                    OperationState.PENDING,
                    SnapshotStep.CREATE_SNAPSHOT,
                    0,
                    None,
                    None,
                    None,
                    now,
                    now,
                )
                work.operations.add(operation)
                work.commit()
        except PersistenceConflict as error:
            raise OperationConflict(server_unit_id) from error
        return LifecycleAccepted(operation_id, run.id)


class RequestStop:
    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock, ids: IdGenerator) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._ids = ids

    def __call__(self, server_unit_id: str) -> LifecycleAccepted:
        now = self._clock.now()
        operation_id = self._ids.new()
        try:
            with self._unit_of_work() as work:
                unit = work.server_units.get(server_unit_id)
                if unit is None:
                    raise ServerUnitNotFound(server_unit_id)
                _require_idle(work, server_unit_id)
                run = work.runs.get_active(server_unit_id)
                if run is None:
                    raise ActiveRunNotFound(server_unit_id)
                operation = Operation(
                    operation_id,
                    server_unit_id,
                    run.id,
                    OperationKind.STOP,
                    OperationState.PENDING,
                    StopStep.STOP_WORKLOAD,
                    0,
                    None,
                    None,
                    None,
                    now,
                    now,
                )
                work.server_units.save(
                    replace(unit, desired_state=DesiredState.STOPPED, updated_at=now)
                )
                work.operations.add(operation)
                work.commit()
        except PersistenceConflict as error:
            raise OperationConflict(server_unit_id) from error
        return LifecycleAccepted(operation_id, run.id)


class RequestOperationRetry:
    """Explicitly resume a blocked Operation after its cause was corrected."""

    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    def __call__(self, operation_id: str) -> Operation:
        now = self._clock.now()
        with self._unit_of_work() as work:
            operation = work.operations.get(operation_id)
            if operation is None:
                raise OperationNotFound(operation_id)
            if operation.state is not OperationState.BLOCKED:
                raise OperationNotBlocked(operation_id)
            step = _retry_step(operation)
            retried = replace(
                operation,
                state=OperationState.RETRY_WAIT,
                step=step,
                attempt_count=operation.attempt_count + 1,
                next_attempt_at=now,
                last_error_code=None,
                last_error_message=None,
                updated_at=now,
            )
            work.operations.save(retried)
            work.commit()
        return retried


def _retry_step(operation: Operation) -> str:
    wait_to_action = {
        (
            OperationKind.START,
            StartStep.WAIT_DATA_REPOSITORY.value,
        ): StartStep.INIT_DATA_REPOSITORY.value,
        (OperationKind.START, StartStep.WAIT_RESTORE.value): StartStep.RESTORE_SNAPSHOT.value,
        (OperationKind.START, StartStep.WAIT_APPLY.value): StartStep.APPLY_WORKLOAD.value,
        (OperationKind.START, StartStep.WAIT_WORKLOAD.value): StartStep.START_WORKLOAD.value,
        (
            OperationKind.SNAPSHOT,
            SnapshotStep.WAIT_SNAPSHOT.value,
        ): SnapshotStep.CREATE_SNAPSHOT.value,
        (OperationKind.STOP, StopStep.WAIT_WORKLOAD.value): StopStep.STOP_WORKLOAD.value,
        (OperationKind.STOP, StopStep.WAIT_SNAPSHOT.value): StopStep.CREATE_SNAPSHOT.value,
    }
    return wait_to_action.get((operation.kind, str(operation.step)), str(operation.step))
