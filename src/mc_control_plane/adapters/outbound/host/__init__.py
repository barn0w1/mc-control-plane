"""Host bootstrap and protocol adapters."""

from mc_control_plane.adapters.outbound.host.cloud_init import (
    HostBootstrapSpec,
    artifact_sha256,
    render_host_cloud_init,
)
from mc_control_plane.adapters.outbound.host.durable import (
    DurableHostManager,
    DurableHostSettings,
    StoredHostObservations,
    create_bootstrap_key,
    load_bootstrap_key,
)

__all__ = [
    "DurableHostManager",
    "DurableHostSettings",
    "HostBootstrapSpec",
    "StoredHostObservations",
    "artifact_sha256",
    "create_bootstrap_key",
    "load_bootstrap_key",
    "render_host_cloud_init",
]
