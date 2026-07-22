"""Compose layer observations without collapsing their independent freshness."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from mc_control_plane.application.ports.host import HostObservationProvider
from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.domain.errors import ServerUnitNotFound


@dataclass(frozen=True, slots=True)
class ServerUnitStatus:
    server_unit: dict[str, Any]
    run: dict[str, Any] | None
    operation: dict[str, Any] | None
    provider: dict[str, Any] | None
    host: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class GetServerUnitStatus:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        host_observations: HostObservationProvider,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._host_observations = host_observations
        self._clock = clock

    def __call__(self, server_unit_id: str) -> ServerUnitStatus:
        with self._unit_of_work() as work:
            unit = work.server_units.get(server_unit_id)
            if unit is None:
                raise ServerUnitNotFound(server_unit_id)
            run = work.runs.get_active(server_unit_id)
            operation = work.operations.get_latest(server_unit_id)
            runtime = None if run is None else work.runtime_instances.get_active_for_run(run.id)

        host = None if run is None else self._host_observations.get_for_run(run.id)
        now = self._clock.now()
        return ServerUnitStatus(
            server_unit={
                "id": unit.id,
                "name": unit.name,
                "desired_state": unit.desired_state.value,
            },
            run=(
                None
                if run is None
                else {
                    "id": run.id,
                    "started_at": run.started_at.isoformat(),
                }
            ),
            operation=(
                None
                if operation is None
                else {
                    "id": operation.id,
                    "kind": operation.kind.value,
                    "state": operation.state.value,
                    "step": str(operation.step),
                    "attempt_count": operation.attempt_count,
                    "next_attempt_at": _time(operation.next_attempt_at),
                    "last_error_code": operation.last_error_code,
                    "last_error_message": operation.last_error_message,
                    "updated_at": operation.updated_at.isoformat(),
                }
            ),
            provider=(
                None
                if runtime is None
                else {
                    "resource_id": runtime.provider_resource_id,
                    "status": runtime.provider_status,
                    "observed_at": runtime.observed_at.isoformat(),
                    "age_seconds": max(0.0, (now - runtime.observed_at).total_seconds()),
                }
            ),
            host=(
                None
                if host is None
                else {
                    "agent_id": host.agent_id,
                    "status": host.status,
                    "protocol_version": host.protocol_version,
                    "agent_version": host.agent_version,
                    "boot_id": host.boot_id,
                    "observed_at": _time(host.observed_at),
                    "age_seconds": (
                        None
                        if host.observed_at is None
                        else max(0.0, (now - host.observed_at).total_seconds())
                    ),
                    "capabilities": host.capabilities,
                    "service_states": host.service_states,
                }
            ),
        )


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
