from datetime import timedelta
from typing import Any, cast

from mc_control_plane.adapters.outbound.compute.linode import (
    LinodeComputeProvider,
    LinodeRuntimePreflight,
)
from mc_control_plane.adapters.outbound.compute.linode_gate2 import run_linode_gate2_check
from mc_control_plane.adapters.outbound.host import HostBootstrapSpec
from mc_control_plane.adapters.outbound.persistence import HostProtocolStore, SQLiteDatabase
from mc_control_plane.application.ports.compute import ComputeLifecycle
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec
from tests.fakes import FakeComputeProvider, MutableClock


class Gate2FakeProvider(FakeComputeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.rebooted = False

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
            linode_interfaces_supported=True,
        )

    def reboot_runtime(
        self,
        provider_resource_id: str,
        identity: ResourceIdentity,
    ) -> None:
        observation = self.observe_runtime(provider_resource_id)
        if not identity.owns(observation.tags):
            raise AssertionError("test resource ownership changed")
        self.rebooted = True


def _as_linode(provider: Gate2FakeProvider) -> LinodeComputeProvider:
    return cast(LinodeComputeProvider, cast(Any, provider))


def test_gate2_check_runs_both_boot_sequences_and_cleans_up(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    provider = Gate2FakeProvider()
    store = HostProtocolStore(database)
    enrollment = store.issue_enrollment(
        run_id="gate2-test",
        resource_identity="gate2-test",
        expires_at=clock.now() + timedelta(minutes=30),
        now=clock.now(),
    )
    agent_token = "agent-secret"
    enrolled = False
    fixture = "inactive"

    def poll(results: list[dict[str, object]] | None = None):
        return store.poll(
            agent_token,
            {
                "protocol_version": 1,
                "agent_id": "agent-gate2-test",
                "run_id": "gate2-test",
                "agent_version": "0.1.0",
                "boot_id": "boot-2" if provider.rebooted else "boot-1",
                "capabilities": {
                    "os_id": "debian",
                    "os_version": "13",
                    "python": "Python 3.13.5",
                    "podman": "podman version 5.4.2",
                    "restic": "restic 0.18.0",
                    "quadlet": True,
                },
                "service_states": {"agent": "active", "fixture": fixture},
                "results": [] if results is None else results,
            },
            now=clock.now(),
        )

    def advance(_seconds: float) -> None:
        nonlocal enrolled, fixture
        if provider.resources:
            resource_id = next(iter(provider.resources))
            provider.set_status(resource_id, "running", ComputeLifecycle.RUNNING)
        if not enrolled:
            store.enroll(
                {
                    "protocol_version": 1,
                    "agent_id": "agent-gate2-test",
                    "run_id": "gate2-test",
                    "resource_identity": "gate2-test",
                    "agent_version": "0.1.0",
                    "enrollment_token": enrollment.token,
                    "agent_token": agent_token,
                },
                now=clock.now(),
            )
            enrolled = True
        if provider.rebooted:
            fixture = "inactive"
        command = poll()
        if command is None:
            return
        if command.kind.value == "start_fixture":
            fixture = "active"
        elif command.kind.value == "stop_fixture":
            fixture = "inactive"
        poll(
            [
                {
                    "command_id": command.command_id,
                    "state": "succeeded",
                    "error_code": None,
                    "message": None,
                    "observation": {"fixture": fixture},
                }
            ]
        )

    spec = RuntimeSpec(
        region="jp-tyo-3",
        instance_type="g6-nanode-1",
        image="linode/debian13",
        container_image="not-used-in-gate2",
        firewall_id="79203454",
    )
    bootstrap = HostBootstrapSpec(
        control_plane_url="https://control.example.test:8443",
        agent_id="agent-gate2-test",
        run_id="gate2-test",
        resource_identity="gate2-test",
        enrollment_token=enrollment.token,
        agent_wheel_url="https://control.example.test:8443/artifacts/agent.whl",
        agent_wheel_sha256="a" * 64,
        agent_version="0.1.0",
        fixture_image=f"docker.io/library/alpine@sha256:{'b' * 64}",
    )

    result = run_linode_gate2_check(
        _as_linode(provider),
        store,
        spec,
        bootstrap,
        timeout_seconds=20,
        poll_seconds=1,
        now=clock.now,
        sleeper=advance,
    )

    assert result.first_boot_id == "boot-1"
    assert result.second_boot_id == "boot-2"
    assert result.cleanup_confirmed is True
    assert provider.rebooted is True
    assert provider.resources == {}
    assert provider.deleted == ["linode-1"]
