"""Accept a request to start a Server Unit."""

from dataclasses import dataclass, replace

from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock, IdGenerator
from mc_control_plane.domain.errors import (
    ActiveRunExists,
    OperationConflict,
    PersistenceConflict,
    ServerUnitNotFound,
)
from mc_control_plane.domain.models import Operation, Run
from mc_control_plane.domain.states import (
    DesiredState,
    OperationKind,
    OperationState,
    StartStep,
)


@dataclass(frozen=True, slots=True)
class StartServerUnit:
    server_unit_id: str
    source_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartAccepted:
    operation_id: str
    run_id: str


class RequestStart:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._ids = ids

    def __call__(self, command: StartServerUnit) -> StartAccepted:
        now = self._clock.now()
        run_id = self._ids.new()
        operation_id = self._ids.new()

        try:
            with self._unit_of_work() as work:
                server_unit = work.server_units.get(command.server_unit_id)
                if server_unit is None:
                    raise ServerUnitNotFound(command.server_unit_id)
                if work.runs.get_active(server_unit.id) is not None:
                    raise ActiveRunExists(server_unit.id)
                if work.operations.get_active(server_unit.id) is not None:
                    raise OperationConflict(server_unit.id)

                run = Run(
                    id=run_id,
                    server_unit_id=server_unit.id,
                    runtime_spec=server_unit.runtime_spec,
                    source_snapshot_id=command.source_snapshot_id,
                    started_at=now,
                )
                operation = Operation(
                    id=operation_id,
                    server_unit_id=server_unit.id,
                    run_id=run.id,
                    kind=OperationKind.START,
                    state=OperationState.PENDING,
                    step=StartStep.DISCOVER_RUNTIME,
                    attempt_count=0,
                    next_attempt_at=None,
                    last_error_code=None,
                    last_error_message=None,
                    created_at=now,
                    updated_at=now,
                )
                work.server_units.save(
                    replace(
                        server_unit,
                        desired_state=DesiredState.RUNNING,
                        updated_at=now,
                    )
                )
                work.runs.add(run)
                work.operations.add(operation)
                work.commit()
        except PersistenceConflict as error:
            raise ActiveRunExists(command.server_unit_id) from error

        return StartAccepted(operation_id=operation_id, run_id=run_id)
