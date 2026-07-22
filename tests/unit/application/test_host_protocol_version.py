from mccp_host_agent import __version__ as agent_version

from mc_control_plane.application.host_protocol import (
    HOST_AGENT_ARTIFACT_PATH,
    HOST_AGENT_VERSION,
)


def test_control_plane_artifact_identity_matches_agent_package() -> None:
    assert HOST_AGENT_VERSION == agent_version == "0.3.0"
    assert HOST_AGENT_ARTIFACT_PATH == "/artifacts/mccp-host-agent-0.3.0.whl"
