from datetime import timedelta

from mc_control_plane.adapters.outbound.persistence import SQLiteUnitOfWorkFactory
from mc_control_plane.application.gate4 import _commit_snapshot, _mark_snapshot_verified
from mc_control_plane.domain.models import ServerUnit
from tests.fakes import MutableClock


def test_snapshot_is_verified_only_after_an_independent_restore(
    unit_of_work: SQLiteUnitOfWorkFactory,
    server_unit: ServerUnit,
    clock: MutableClock,
) -> None:
    with unit_of_work() as work:
        work.server_units.add(server_unit)
        work.commit()

    _commit_snapshot(unit_of_work, "snapshot-1", server_unit.id, None, clock)

    with unit_of_work() as work:
        committed = work.snapshots.get("snapshot-1")
        assert committed is not None
        assert committed.verified_at is None

    clock.advance(timedelta(minutes=2))
    _mark_snapshot_verified(unit_of_work, "snapshot-1", server_unit.id, clock)

    with unit_of_work() as work:
        verified = work.snapshots.get("snapshot-1")
        assert verified is not None
        assert verified.verified_at == clock.now()
