"""Strict configuration for the private single-node control plane."""

import tomllib
from collections.abc import Set
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("/etc/mc-control-plane/config.toml")


@dataclass(frozen=True, slots=True)
class ControlPlaneNodeConfig:
    database: Path
    system_id: str
    control_plane_url: str
    host_bootstrap_key: Path
    agent_wheel: Path
    fixture_image: str
    ssh_public_keys: tuple[Path, ...]
    interval_seconds: float = 5.0
    operation_limit: int = 32


@dataclass(frozen=True, slots=True)
class HostApiNodeConfig:
    bind: str = "127.0.0.1"
    port: int = 8443
    tls_certificate: Path | None = None
    tls_private_key: Path | None = None


@dataclass(frozen=True, slots=True)
class R2NodeConfig:
    account_id: str
    bucket: str
    parent_access_key_id: str
    cloudflare_api_token_file: Path
    lease_ttl_seconds: int = 3600


@dataclass(frozen=True, slots=True)
class NodeConfig:
    control_plane: ControlPlaneNodeConfig
    host_api: HostApiNodeConfig
    r2: R2NodeConfig


def load_node_config(path: Path) -> NodeConfig:
    """Load a complete node config and reject ambiguous or unknown input."""

    try:
        document = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid TOML configuration: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("configuration root must be a TOML table")
    _exact_keys(document, {"control_plane", "host_api", "r2"}, "configuration")

    control = _table(document, "control_plane")
    _exact_keys(
        control,
        {
            "database",
            "system_id",
            "control_plane_url",
            "host_bootstrap_key",
            "agent_wheel",
            "fixture_image",
            "ssh_public_keys",
            "interval_seconds",
            "operation_limit",
        },
        "control_plane",
        optional={"interval_seconds", "operation_limit"},
    )
    host_api = _table(document, "host_api")
    _exact_keys(
        host_api,
        {"bind", "port", "tls_certificate", "tls_private_key"},
        "host_api",
        optional={"bind", "port", "tls_certificate", "tls_private_key"},
    )
    r2 = _table(document, "r2")
    _exact_keys(
        r2,
        {
            "account_id",
            "bucket",
            "parent_access_key_id",
            "cloudflare_api_token_file",
            "lease_ttl_seconds",
        },
        "r2",
        optional={"lease_ttl_seconds"},
    )

    ssh_keys_value = control["ssh_public_keys"]
    if not isinstance(ssh_keys_value, list) or not ssh_keys_value:
        raise ValueError("control_plane.ssh_public_keys must be a non-empty array")
    ssh_keys = tuple(
        _absolute_path(item, "control_plane.ssh_public_keys") for item in ssh_keys_value
    )
    interval = _positive_number(control.get("interval_seconds", 5.0), "interval_seconds")
    limit = _positive_integer(control.get("operation_limit", 32), "operation_limit")
    port = _positive_integer(host_api.get("port", 8443), "host_api.port")
    if port > 65535:
        raise ValueError("host_api.port must be at most 65535")
    ttl = _positive_integer(r2.get("lease_ttl_seconds", 3600), "r2.lease_ttl_seconds")

    certificate = _optional_path(host_api.get("tls_certificate"), "host_api.tls_certificate")
    private_key = _optional_path(host_api.get("tls_private_key"), "host_api.tls_private_key")
    if (certificate is None) != (private_key is None):
        raise ValueError("host_api TLS certificate and private key must be provided together")

    result = NodeConfig(
        control_plane=ControlPlaneNodeConfig(
            database=_absolute_path(control["database"], "control_plane.database"),
            system_id=_text(control["system_id"], "control_plane.system_id"),
            control_plane_url=_https_url(
                control["control_plane_url"], "control_plane.control_plane_url"
            ),
            host_bootstrap_key=_absolute_path(
                control["host_bootstrap_key"], "control_plane.host_bootstrap_key"
            ),
            agent_wheel=_absolute_path(control["agent_wheel"], "control_plane.agent_wheel"),
            fixture_image=_text(control["fixture_image"], "control_plane.fixture_image"),
            ssh_public_keys=ssh_keys,
            interval_seconds=interval,
            operation_limit=limit,
        ),
        host_api=HostApiNodeConfig(
            bind=_text(host_api.get("bind", "127.0.0.1"), "host_api.bind"),
            port=port,
            tls_certificate=certificate,
            tls_private_key=private_key,
        ),
        r2=R2NodeConfig(
            account_id=_text(r2["account_id"], "r2.account_id"),
            bucket=_text(r2["bucket"], "r2.bucket"),
            parent_access_key_id=_text(r2["parent_access_key_id"], "r2.parent_access_key_id"),
            cloudflare_api_token_file=_absolute_path(
                r2["cloudflare_api_token_file"], "r2.cloudflare_api_token_file"
            ),
            lease_ttl_seconds=ttl,
        ),
    )
    return result


def _table(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a TOML table")
    return value


def _exact_keys(
    value: dict[str, Any],
    allowed: set[str],
    name: str,
    *,
    optional: Set[str] = frozenset(),
) -> None:
    unknown = set(value) - allowed
    missing = allowed - optional - set(value)
    if unknown:
        raise ValueError(f"{name} has unknown keys: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"{name} is missing keys: {', '.join(sorted(missing))}")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _https_url(value: object, name: str) -> str:
    text = _text(value, name)
    if not text.startswith("https://") or text.endswith("/"):
        raise ValueError(f"{name} must be an HTTPS origin without a trailing slash")
    return text


def _absolute_path(value: object, name: str) -> Path:
    path = Path(_text(value, name))
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def _optional_path(value: object, name: str) -> Path | None:
    return None if value is None else _absolute_path(value, name)


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value
