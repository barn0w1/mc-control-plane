import base64
import json
import re
from dataclasses import replace

import pytest

from mc_control_plane.adapters.outbound.host import HostBootstrapSpec, render_host_cloud_init

IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64


def _spec() -> HostBootstrapSpec:
    return HostBootstrapSpec(
        control_plane_url="https://control.example.test",
        agent_id="agent-1",
        run_id="run-1",
        resource_identity="resource-1",
        enrollment_token="one-time-secret",
        agent_wheel_url="https://artifacts.example.test/mccp_host_agent-0.1.0.whl",
        agent_wheel_sha256="b" * 64,
        agent_version="0.1.0",
        fixture_image=IMAGE,
    )


def test_cloud_init_installs_fixed_host_baseline_without_logging_secret() -> None:
    rendered = render_host_cloud_init(_spec())

    assert rendered.startswith("#cloud-config\n")
    assert "package_upgrade: false" in rendered
    assert all(f"  - {package}\n" in rendered for package in ("podman", "restic", "python3"))
    assert 'test "$actual" = ' + "b" * 64 in rendered
    assert 'test "$installed" = 0.1.0' in rendered
    assert "ExecStart=/opt/mccp-host-agent/bin/mccp-host-agent" in rendered
    assert "one-time-secret" not in rendered

    encoded = re.search(r"content: ([A-Za-z0-9+/=]+)\n  - path: /etc/systemd", rendered)
    assert encoded is not None
    config = json.loads(base64.b64decode(encoded.group(1)))
    assert config["enrollment_token"] == "one-time-secret"
    assert config["fixture_image"] == IMAGE


def test_cloud_init_rejects_mutable_artifacts_and_plain_http() -> None:
    spec = _spec()
    with pytest.raises(ValueError, match="HTTPS"):
        replace(spec, agent_wheel_url="http://example.test/agent.whl")
    with pytest.raises(ValueError, match="digest-pinned"):
        replace(spec, fixture_image="docker.io/library/alpine:latest")
