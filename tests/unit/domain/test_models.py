from datetime import UTC, datetime

import pytest

from mc_control_plane.domain.errors import InvalidModel
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec


def test_runtime_spec_rejects_empty_required_value() -> None:
    with pytest.raises(InvalidModel):
        RuntimeSpec(
            region="",
            instance_type="type",
            image="image",
            container_image="container",
        )


def test_resource_identity_requires_all_ownership_tags() -> None:
    identity = ResourceIdentity(system_id="main", server_unit_id="survival", run_id="run-1")

    assert identity.owns(identity.tags | {"unrelated"})
    assert not identity.owns(identity.tags - {"mccp:run=run-1"})


def test_domain_datetime_must_be_timezone_aware() -> None:
    from mc_control_plane.domain.models import ServerUnit
    from mc_control_plane.domain.states import DesiredState

    spec = RuntimeSpec(region="r", instance_type="t", image="i", container_image="c")
    with pytest.raises(InvalidModel):
        ServerUnit(
            id="unit",
            name="Unit",
            desired_state=DesiredState.STOPPED,
            runtime_spec=spec,
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
