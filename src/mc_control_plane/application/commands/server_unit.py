"""Create a Server Unit without starting external resources."""

from dataclasses import dataclass

from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.domain.errors import PersistenceConflict
from mc_control_plane.domain.models import RuntimeSpec, ServerUnit
from mc_control_plane.domain.states import DesiredState


@dataclass(frozen=True, slots=True)
class CreateServerUnit:
    server_unit_id: str
    name: str
    runtime_spec: RuntimeSpec


class RequestServerUnitCreation:
    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    def __call__(self, command: CreateServerUnit) -> ServerUnit:
        now = self._clock.now()
        server_unit = ServerUnit(
            id=command.server_unit_id,
            name=command.name,
            desired_state=DesiredState.STOPPED,
            runtime_spec=command.runtime_spec,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._unit_of_work() as work:
                work.server_units.add(server_unit)
                work.commit()
        except PersistenceConflict as error:
            raise ValueError(f"Server Unit already exists: {command.server_unit_id}") from error
        return server_unit
