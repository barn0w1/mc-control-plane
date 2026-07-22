from datetime import UTC, datetime
from typing import Any, cast

import pytest

from mc_control_plane.adapters.outbound.compute.linode import (
    LinodeComputeProvider,
    LinodeRuntimePreflight,
)
from mc_control_plane.adapters.outbound.compute.linode_gate1 import (
    LinodeGate1CheckError,
    run_linode_gate1_check,
)
from mc_control_plane.application.ports.compute import ComputeLifecycle
from mc_control_plane.domain.models import RuntimeSpec
from tests.fakes import FakeComputeProvider


class Gate1FakeProvider(FakeComputeProvider):
    def validate_runtime_spec(
        self,
        spec: RuntimeSpec,
        *,
        require_metadata: bool = True,
        require_firewall: bool = True,
    ) -> LinodeRuntimePreflight:
        return LinodeRuntimePreflight(
            region=spec.region,
            instance_type=spec.instance_type,
            image=spec.image,
            firewall_id=spec.firewall_id,
            metadata_supported=require_metadata,
        )


@pytest.fixture
def spec() -> RuntimeSpec:
    return RuntimeSpec(
        region="us-ord",
        instance_type="g6-standard-2",
        image="linode/debian13",
        container_image="not-used-in-gate1",
        firewall_id="12345",
    )


def _as_linode(provider: Gate1FakeProvider) -> LinodeComputeProvider:
    return cast(LinodeComputeProvider, cast(Any, provider))


def test_gate1_check_creates_reaches_running_and_cleans_up(spec: RuntimeSpec) -> None:
    provider = Gate1FakeProvider()

    def advance(_seconds: float) -> None:
        if provider.resources:
            resource_id = next(iter(provider.resources))
            provider.set_status(resource_id, "running", ComputeLifecycle.RUNNING)

    result = run_linode_gate1_check(
        _as_linode(provider),
        spec,
        timeout_seconds=2,
        poll_seconds=1,
        now=lambda: datetime(2026, 7, 22, tzinfo=UTC),
        sleeper=advance,
        run_id_factory=lambda: "gate1-test",
    )

    assert result.metadata_confirmed is True
    assert result.firewall_confirmed is True
    assert result.backups_disabled is True
    assert result.cleanup_confirmed is True
    assert provider.resources == {}
    assert provider.deleted == ["linode-1"]


def test_gate1_check_recovers_an_uncertain_create_by_exact_tags(spec: RuntimeSpec) -> None:
    provider = Gate1FakeProvider()
    provider.uncertain_next_create = True

    def advance(_seconds: float) -> None:
        if provider.resources:
            resource_id = next(iter(provider.resources))
            provider.set_status(resource_id, "running", ComputeLifecycle.RUNNING)

    result = run_linode_gate1_check(
        _as_linode(provider),
        spec,
        timeout_seconds=3,
        poll_seconds=1,
        sleeper=advance,
        run_id_factory=lambda: "gate1-uncertain",
    )

    assert result.cleanup_confirmed is True
    assert provider.create_count == 1
    assert provider.resources == {}


def test_gate1_check_rejects_non_debian_image_before_create(spec: RuntimeSpec) -> None:
    provider = Gate1FakeProvider()
    wrong = RuntimeSpec(
        region=spec.region,
        instance_type=spec.instance_type,
        image="linode/ubuntu24.04",
        container_image=spec.container_image,
        firewall_id=spec.firewall_id,
    )

    with pytest.raises(LinodeGate1CheckError, match="Debian 13"):
        run_linode_gate1_check(_as_linode(provider), wrong)

    assert provider.create_count == 0


def test_gate1_check_cleans_up_when_metadata_confirmation_is_missing(
    spec: RuntimeSpec,
) -> None:
    provider = Gate1FakeProvider()

    def advance(_seconds: float) -> None:
        if provider.resources:
            resource_id = next(iter(provider.resources))
            current = provider.resources[resource_id]
            provider.resources[resource_id] = type(current)(
                provider_resource_id=current.provider_resource_id,
                provider=current.provider,
                region=current.region,
                raw_status="running",
                lifecycle=ComputeLifecycle.RUNNING,
                tags=current.tags,
                has_user_data=False,
                backups_enabled=False,
            )

    with pytest.raises(LinodeGate1CheckError, match="Metadata"):
        run_linode_gate1_check(
            _as_linode(provider),
            spec,
            timeout_seconds=2,
            poll_seconds=1,
            sleeper=advance,
            run_id_factory=lambda: "gate1-no-metadata",
        )

    assert provider.resources == {}
