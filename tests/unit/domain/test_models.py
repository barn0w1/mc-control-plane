from datetime import UTC, datetime

import pytest

from mc_control_plane.domain.errors import InvalidModel
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec, resource_scope_tags


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
    run_tag = next(tag for tag in identity.tags if tag.startswith("mccp:run="))

    assert identity.owns(identity.tags | {"unrelated"})
    assert not identity.owns(identity.tags - {run_tag})


def test_resource_tags_are_stable_and_fit_linode_limits() -> None:
    long_value = "server-unit-" * 100
    identity = ResourceIdentity(system_id=long_value, server_unit_id=long_value, run_id=long_value)

    assert (
        identity.tags
        == ResourceIdentity(
            system_id=long_value,
            server_unit_id=long_value,
            run_id=long_value,
        ).tags
    )
    assert resource_scope_tags(long_value, long_value) < identity.tags
    assert all(3 <= len(tag) <= 50 for tag in identity.tags)


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
