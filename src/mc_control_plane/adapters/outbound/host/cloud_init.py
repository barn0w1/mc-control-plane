"""Render the minimal Debian 13 bootstrap for the independent Host agent."""

import base64
import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from urllib.parse import urlsplit

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class HostBootstrapSpec:
    control_plane_url: str
    agent_id: str
    run_id: str
    resource_identity: str
    enrollment_token: str
    agent_wheel_url: str
    agent_wheel_sha256: str
    agent_version: str
    fixture_image: str
    poll_seconds: int = 5
    ca_file: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "control_plane_url",
            "agent_id",
            "run_id",
            "resource_identity",
            "enrollment_token",
            "agent_wheel_url",
            "agent_wheel_sha256",
            "agent_version",
            "fixture_image",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must not be empty")
        for field in ("control_plane_url", "agent_wheel_url"):
            value = urlsplit(getattr(self, field))
            if value.scheme != "https" or not value.netloc or value.fragment:
                raise ValueError(f"{field} must be an HTTPS URL")
        if not _SHA256.fullmatch(self.agent_wheel_sha256):
            raise ValueError("agent_wheel_sha256 must be a lowercase SHA-256 digest")
        if not _DIGEST_IMAGE.fullmatch(self.fixture_image):
            raise ValueError("fixture_image must be fully qualified and digest-pinned")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.agent_version):
            raise ValueError("agent_version must use MAJOR.MINOR.PATCH")
        if self.poll_seconds <= 0 or self.poll_seconds > 300:
            raise ValueError("poll_seconds must be between 1 and 300")


def artifact_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def render_host_cloud_init(spec: HostBootstrapSpec) -> str:
    """Return cloud-config without interpolating secrets into shell commands or logs."""

    config = {
        "control_plane_url": spec.control_plane_url,
        "agent_id": spec.agent_id,
        "run_id": spec.run_id,
        "resource_identity": spec.resource_identity,
        "enrollment_token": spec.enrollment_token,
        "fixture_image": spec.fixture_image,
        "poll_seconds": spec.poll_seconds,
        "ca_file": spec.ca_file,
    }
    encoded_config = base64.b64encode(
        json.dumps(config, separators=(",", ":"), sort_keys=True).encode()
    ).decode()
    service = _service_unit()
    tmpfiles = _podman_tmpfiles()
    bootstrap = _bootstrap_script(spec)
    # JSON strings are valid YAML scalar values and avoid hand-written escaping.
    return (
        "#cloud-config\n"
        "package_update: true\n"
        "package_upgrade: false\n"
        "packages:\n"
        "  - ca-certificates\n"
        "  - curl\n"
        "  - podman\n"
        "  - python3\n"
        "  - python3-venv\n"
        "  - restic\n"
        "write_files:\n"
        "  - path: /etc/mc-control-plane-agent/config.json\n"
        "    owner: root:root\n"
        "    permissions: '0600'\n"
        "    encoding: b64\n"
        f"    content: {encoded_config}\n"
        "  - path: /etc/systemd/system/mccp-host-agent.service\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        f"{_indent(service, 6)}"
        "  - path: /etc/tmpfiles.d/mccp-podman-runtime.conf\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        f"{_indent(tmpfiles, 6)}"
        "  - path: /usr/local/sbin/mccp-bootstrap\n"
        "    owner: root:root\n"
        "    permissions: '0700'\n"
        "    content: |\n"
        f"{_indent(bootstrap, 6)}"
        "runcmd:\n"
        "  - [/usr/local/sbin/mccp-bootstrap]\n"
        "final_message: 'mc-control-plane Host bootstrap finished'\n"
    )


def _bootstrap_script(spec: HostBootstrapSpec) -> str:
    url = shlex.quote(spec.agent_wheel_url)
    digest = shlex.quote(spec.agent_wheel_sha256)
    version = shlex.quote(spec.agent_version)
    return f"""#!/bin/sh
set -eu
umask 077
install -d -m 0700 /var/lib/mc-control-plane-agent
install -d -m 0700 /var/lib/mc-control-plane-data
install -d -m 0700 /var/lib/containers
install -d -m 0700 /run/containers
install -d -m 0700 /run/libpod
install -d -m 0700 /run/crun
install -d -m 0755 /etc/containers/systemd
workload_user=mccp-minecraft
workload_uid=1000
workload_gid=1000
if getent group "$workload_user" >/dev/null; then
    test "$(getent group "$workload_user" | cut -d: -f3)" = "$workload_gid"
else
    ! getent group "$workload_gid" >/dev/null
    groupadd --gid "$workload_gid" "$workload_user"
fi
if getent passwd "$workload_user" >/dev/null; then
    test "$(id -u "$workload_user")" = "$workload_uid"
    test "$(id -g "$workload_user")" = "$workload_gid"
else
    ! getent passwd "$workload_uid" >/dev/null
    useradd --uid "$workload_uid" --gid "$workload_gid" --home-dir /nonexistent \
        --no-create-home --shell /usr/sbin/nologin --password '!' "$workload_user"
fi
test "$(getent passwd "$workload_user" | cut -d: -f7)" = /usr/sbin/nologin
python_version=$(python3 -c 'import platform; print(platform.python_version())')
podman_version=$(podman --version)
restic_version=$(restic version)
case "$python_version" in 3.13.*) ;; *) echo 'unsupported Debian Python version' >&2; exit 1 ;; esac
case "$podman_version" in *' 5.4.'*) ;; *) echo 'unsupported Podman version' >&2; exit 1 ;; esac
case "$restic_version" in 'restic 0.18.'*) ;; *) echo 'unsupported restic version' >&2; exit 1 ;; esac
artifact=/run/mccp_host_agent-{spec.agent_version}-py3-none-any.whl
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 {url} -o "$artifact"
actual=$(sha256sum "$artifact" | cut -d' ' -f1)
test "$actual" = {digest}
python3 -m venv /opt/mccp-host-agent
/opt/mccp-host-agent/bin/pip install --no-deps --disable-pip-version-check "$artifact"
installed=$(/opt/mccp-host-agent/bin/python -c 'import mccp_host_agent; print(mccp_host_agent.__version__)')
test "$installed" = {version}
rm -f "$artifact"
python3 - "$python_version" "$podman_version" "$restic_version" "$installed" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path('/var/lib/mc-control-plane-agent/bootstrap.json')
path.write_text(json.dumps({{
    'schema_version': 1,
    'python': sys.argv[1],
    'podman': sys.argv[2],
    'restic': sys.argv[3],
    'agent': sys.argv[4],
}}, separators=(',', ':'), sort_keys=True) + '\\n')
path.chmod(0o600)
PY
systemctl daemon-reload
systemctl enable --now mccp-host-agent.service
"""


def _service_unit() -> str:
    return """[Unit]
Description=mc-control-plane Host agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/mccp-host-agent/bin/mccp-host-agent
Restart=on-failure
RestartSec=5s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/etc/mc-control-plane-agent /etc/containers/systemd
ReadWritePaths=/var/lib/mc-control-plane-agent /var/lib/mc-control-plane-data
ReadWritePaths=/var/lib/containers /run/containers /run/libpod /run/crun
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def _podman_tmpfiles() -> str:
    return """d /run/containers 0700 root root -
d /run/libpod 0700 root root -
d /run/crun 0700 root root -
"""


def _indent(value: str, spaces: int) -> str:
    prefix = " " * spaces
    return "".join(f"{prefix}{line}\n" for line in value.rstrip("\n").splitlines())
