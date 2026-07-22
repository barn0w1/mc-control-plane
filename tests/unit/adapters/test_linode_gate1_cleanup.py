from typing import Any, cast

from mc_control_plane.adapters.outbound.compute.linode import LinodeComputeProvider
from mc_control_plane.adapters.outbound.compute.linode_gate1 import (
    cleanup_linode_gate1_resources,
)
from mc_control_plane.application.ports.compute import ComputeLifecycle, RuntimeObservation
from mc_control_plane.domain.models import ResourceIdentity
from tests.fakes import FakeComputeProvider


def test_recovery_cleanup_deletes_only_complete_identity_match() -> None:
    provider = FakeComputeProvider()
    target = ResourceIdentity("main", "gate1-infra-lifecycle", "gate1-target")
    other = ResourceIdentity("main", "gate1-infra-lifecycle", "gate1-other")
    provider.add(
        RuntimeObservation(
            provider_resource_id="linode-target",
            provider="akamai-linode",
            region="us-ord",
            raw_status="running",
            lifecycle=ComputeLifecycle.RUNNING,
            tags=target.tags,
        )
    )
    provider.add(
        RuntimeObservation(
            provider_resource_id="linode-other",
            provider="akamai-linode",
            region="us-ord",
            raw_status="running",
            lifecycle=ComputeLifecycle.RUNNING,
            tags=other.tags,
        )
    )

    deleted = cleanup_linode_gate1_resources(
        cast(LinodeComputeProvider, cast(Any, provider)),
        system_id="main",
        run_id="gate1-target",
        timeout_seconds=1,
        poll_seconds=1,
        sleeper=lambda _seconds: None,
    )

    assert deleted == ("linode-target",)
    assert set(provider.resources) == {"linode-other"}
