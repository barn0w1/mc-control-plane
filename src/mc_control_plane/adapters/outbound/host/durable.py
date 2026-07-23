"""Reproducible Host bootstrap backed by a local Control Plane secret."""

import base64
import hashlib
import hmac
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from mc_control_plane.adapters.outbound.host.cloud_init import (
    HostBootstrapSpec,
    artifact_sha256,
    render_host_cloud_init,
)
from mc_control_plane.adapters.outbound.persistence.host_protocol import HostProtocolStore
from mc_control_plane.application.host_protocol import (
    HOST_AGENT_ARTIFACT_PATH,
    HOST_AGENT_VERSION,
    HostEnrollmentError,
)
from mc_control_plane.application.ports.host import HostBootstrapError, HostObservation
from mc_control_plane.domain.models import ResourceIdentity, Run


@dataclass(frozen=True, slots=True)
class DurableHostSettings:
    control_plane_url: str
    agent_wheel: Path
    fixture_image: str
    enrollment_ttl: timedelta = timedelta(hours=2)
    poll_seconds: int = 5

    def __post_init__(self) -> None:
        if self.enrollment_ttl <= timedelta(0):
            raise ValueError("enrollment TTL must be positive")


class StoredHostObservations:
    def __init__(self, store: HostProtocolStore) -> None:
        self._store = store

    def get_for_run(self, run_id: str) -> HostObservation | None:
        agent = self._store.get_agent_for_run(run_id)
        if agent is None:
            return None
        return HostObservation(
            run_id=agent.run_id,
            agent_id=agent.agent_id,
            protocol_version=agent.protocol_version,
            agent_version=agent.agent_version,
            status=agent.status,
            boot_id=agent.boot_id,
            capabilities=agent.capabilities,
            service_states=agent.service_states,
            observed_at=agent.observed_at,
        )


class DurableHostManager(StoredHostObservations):
    def __init__(
        self,
        store: HostProtocolStore,
        settings: DurableHostSettings,
        bootstrap_key: bytes,
    ) -> None:
        if len(bootstrap_key) < 32:
            raise ValueError("Host bootstrap key must contain at least 32 bytes")
        expected = f"mccp_host_agent-{HOST_AGENT_VERSION}-py3-none-any.whl"
        allowed_names = {expected, "host-agent.whl"}
        if settings.agent_wheel.name not in allowed_names:
            raise ValueError(
                f"Host agent wheel must be named {expected} or use the managed stable name"
            )
        wheel = settings.agent_wheel.read_bytes()
        super().__init__(store)
        self._store = store
        self._settings = settings
        self._key = bootstrap_key
        self._artifact_digest = artifact_sha256(wheel)

    def metadata_for(
        self,
        run: Run,
        identity: ResourceIdentity,
        now: datetime,
    ) -> str:
        if run.id != identity.run_id or run.server_unit_id != identity.server_unit_id:
            raise ValueError("Host bootstrap identity does not match Run")
        try:
            token = self._token(run.id, identity.run_id)
            self._store.ensure_enrollment(
                token=token,
                run_id=run.id,
                resource_identity=identity.run_id,
                expires_at=now + self._settings.enrollment_ttl,
                now=now,
            )
            return render_host_cloud_init(
                HostBootstrapSpec(
                    control_plane_url=self._settings.control_plane_url,
                    agent_id=f"agent-{run.id}",
                    run_id=run.id,
                    resource_identity=identity.run_id,
                    enrollment_token=token,
                    agent_wheel_url=(
                        self._settings.control_plane_url.rstrip("/") + HOST_AGENT_ARTIFACT_PATH
                    ),
                    agent_wheel_sha256=self._artifact_digest,
                    agent_version=HOST_AGENT_VERSION,
                    fixture_image=self._settings.fixture_image,
                    poll_seconds=self._settings.poll_seconds,
                )
            )
        except (HostEnrollmentError, OSError, ValueError) as error:
            raise HostBootstrapError(str(error)) from error

    def _token(self, run_id: str, resource_identity: str) -> str:
        context = f"mccp-host-enrollment-v1\0{run_id}\0{resource_identity}".encode()
        return base64.urlsafe_b64encode(
            hmac.new(self._key, context, hashlib.sha256).digest()
        ).decode()


def create_bootstrap_key(path: Path) -> None:
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def load_bootstrap_key(path: Path) -> bytes:
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Host bootstrap key must be a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Host bootstrap key must not be accessible by group or others")
    try:
        key = base64.b64decode(path.read_bytes().strip(), altchars=b"-_", validate=True)
    except ValueError as error:
        raise ValueError("Host bootstrap key is not valid URL-safe base64") from error
    if len(key) != 32:
        raise ValueError("Host bootstrap key must decode to exactly 32 bytes")
    return key
