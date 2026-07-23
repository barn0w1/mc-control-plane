from pathlib import Path

import pytest

from mc_control_plane.config import load_node_config


def _configuration(tmp_path: Path) -> str:
    return f"""
[control_plane]
database = "{tmp_path}/control-plane.db"
system_id = "mc-control-plane"
control_plane_url = "https://control.example.test"
host_bootstrap_key = "{tmp_path}/host-bootstrap.key"
agent_wheel = "{tmp_path}/host-agent.whl"
fixture_image = "docker.io/library/alpine@sha256:{"a" * 64}"
ssh_public_keys = ["{tmp_path}/id_ed25519.pub"]

[host_api]
bind = "127.0.0.1"
port = 8443

[r2]
account_id = "account"
bucket = "mccp-data"
parent_access_key_id = "parent"
cloudflare_api_token_file = "{tmp_path}/cloudflare-api-token"
"""


def test_load_node_config_has_safe_operational_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(_configuration(tmp_path))

    config = load_node_config(path)

    assert config.control_plane.database == tmp_path / "control-plane.db"
    assert config.control_plane.interval_seconds == 5.0
    assert config.control_plane.operation_limit == 32
    assert config.host_api.bind == "127.0.0.1"
    assert config.r2.lease_ttl_seconds == 3600


def test_load_node_config_rejects_unknown_and_relative_values(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.toml"
    unknown.write_text(_configuration(tmp_path) + "\n[unexpected]\nvalue = true\n")
    relative = tmp_path / "relative.toml"
    relative.write_text(
        _configuration(tmp_path).replace(str(tmp_path / "control-plane.db"), "x.db")
    )

    with pytest.raises(ValueError, match="unknown keys"):
        load_node_config(unknown)
    with pytest.raises(ValueError, match="absolute path"):
        load_node_config(relative)


def test_load_node_config_requires_tls_pair(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        _configuration(tmp_path).replace(
            "port = 8443",
            f'port = 8443\ntls_certificate = "{tmp_path}/certificate.pem"',
        )
    )

    with pytest.raises(ValueError, match="provided together"):
        load_node_config(path)
