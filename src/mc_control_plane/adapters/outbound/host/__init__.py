"""Host bootstrap and protocol adapters."""

from mc_control_plane.adapters.outbound.host.cloud_init import (
    HostBootstrapSpec,
    artifact_sha256,
    render_host_cloud_init,
)

__all__ = ["HostBootstrapSpec", "artifact_sha256", "render_host_cloud_init"]
