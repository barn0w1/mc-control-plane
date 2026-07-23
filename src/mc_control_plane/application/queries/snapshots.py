"""Read committed recovery points without exposing persistence details."""

from dataclasses import dataclass

from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.domain.errors import ServerUnitNotFound


@dataclass(frozen=True, slots=True)
class SnapshotView:
    id: str
    run_id: str | None
    kind: str
    created_at: str
    verified_at: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
        }


class ListServerUnitSnapshots:
    def __init__(self, unit_of_work: UnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    def __call__(self, server_unit_id: str) -> tuple[SnapshotView, ...]:
        with self._unit_of_work() as work:
            if work.server_units.get(server_unit_id) is None:
                raise ServerUnitNotFound(server_unit_id)
            snapshots = work.snapshots.list_for_server_unit(server_unit_id)
        return tuple(
            SnapshotView(
                item.id,
                item.run_id,
                item.kind.value,
                item.created_at.isoformat(),
                None if item.verified_at is None else item.verified_at.isoformat(),
            )
            for item in snapshots
        )
