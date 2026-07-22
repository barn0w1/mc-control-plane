"""Shared pytest fixtures."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mc_control_plane.adapters.outbound.persistence import (
    SQLiteDatabase,
    SQLiteUnitOfWorkFactory,
)
from mc_control_plane.domain.models import RuntimeSpec, ServerUnit
from mc_control_plane.domain.states import DesiredState
from tests.fakes import MutableClock


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 7, 22, 12, 0, tzinfo=UTC))


@pytest.fixture
def runtime_spec() -> RuntimeSpec:
    return RuntimeSpec(
        region="us-ord",
        instance_type="g6-standard-2",
        image="linode/ubuntu24.04",
        container_image="itzg/minecraft-server:latest",
        firewall_id="12345",
    )


@pytest.fixture
def database(tmp_path: Path) -> SQLiteDatabase:
    database = SQLiteDatabase(tmp_path / "control-plane.db")
    database.migrate()
    return database


@pytest.fixture
def unit_of_work(database: SQLiteDatabase) -> SQLiteUnitOfWorkFactory:
    return SQLiteUnitOfWorkFactory(database)


@pytest.fixture
def server_unit(runtime_spec: RuntimeSpec, clock: MutableClock) -> ServerUnit:
    now = clock.now()
    return ServerUnit(
        id="survival",
        name="Survival",
        desired_state=DesiredState.STOPPED,
        runtime_spec=runtime_spec,
        created_at=now,
        updated_at=now,
    )
