import json
import os
from pathlib import Path

from mc_control_plane.adapters.inbound.cli import main
from mc_control_plane.adapters.outbound.host import create_bootstrap_key


def test_gate1_cli_requires_explicit_billable_confirmation(
    tmp_path: Path,
) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    result = main(
        [
            "linode-gate1-check",
            "--region",
            "us-ord",
            "--instance-type",
            "g6-standard-2",
            "--firewall-id",
            "12345",
            "--ssh-public-key",
            str(key),
        ]
    )

    assert result == 2


def test_gate1_cleanup_requires_explicit_delete_confirmation(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    result = main(
        [
            "linode-gate1-cleanup",
            "--run-id",
            "gate1-recovery",
            "--ssh-public-key",
            str(key),
        ]
    )

    assert result == 2


def test_gate2_cli_requires_explicit_billable_confirmation(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")
    wheel = tmp_path / "agent.whl"
    wheel.write_bytes(b"wheel")

    result = main(
        [
            "linode-gate2-check",
            "--region",
            "us-ord",
            "--instance-type",
            "g6-standard-2",
            "--firewall-id",
            "12345",
            "--ssh-public-key",
            str(key),
            "--database",
            str(tmp_path / "control.db"),
            "--control-plane-url",
            "https://control.example.test",
            "--agent-wheel",
            str(wheel),
            "--fixture-image",
            "docker.io/library/alpine@sha256:" + "a" * 64,
        ]
    )

    assert result == 2


def test_gate2_cleanup_requires_explicit_delete_confirmation(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    result = main(
        [
            "linode-gate2-cleanup",
            "--run-id",
            "gate2-recovery",
            "--ssh-public-key",
            str(key),
        ]
    )

    assert result == 2


def test_gate3_cleanup_requires_explicit_delete_confirmation(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    result = main(
        [
            "linode-gate3-cleanup",
            "--database",
            str(tmp_path / "control.db"),
            "--server-unit-id",
            "survival",
            "--ssh-public-key",
            str(key),
        ]
    )

    assert result == 2


def test_gate4_cli_requires_explicit_three_host_confirmation(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    result = main(
        [
            "linode-gate4-check",
            "--database",
            str(tmp_path / "control.db"),
            "--server-unit-id",
            "survival",
            "--host-bootstrap-key",
            str(tmp_path / "bootstrap.key"),
            "--control-plane-url",
            "https://control.example.test",
            "--agent-wheel",
            str(tmp_path / "agent.whl"),
            "--fixture-image",
            "docker.io/library/alpine@sha256:" + "a" * 64,
            "--region",
            "jp-tyo-3",
            "--instance-type",
            "g6-nanode-1",
            "--firewall-id",
            "12345",
            "--ssh-public-key",
            str(key),
        ]
    )

    assert result == 2


def _gate5_arguments(tmp_path: Path, key: Path) -> list[str]:
    return [
        "linode-gate5-check",
        "--database",
        str(tmp_path / "control.db"),
        "--server-unit-id",
        "survival",
        "--host-bootstrap-key",
        str(tmp_path / "bootstrap.key"),
        "--control-plane-url",
        "https://control.example.test",
        "--agent-wheel",
        str(tmp_path / "agent.whl"),
        "--fixture-image",
        "docker.io/library/alpine@sha256:" + "a" * 64,
        "--minecraft-image",
        "docker.io/itzg/minecraft-server@sha256:" + "b" * 64,
        "--minecraft-version",
        "1.21.8",
        "--paper-build",
        "42",
        "--region",
        "jp-tyo-3",
        "--instance-type",
        "g6-nanode-1",
        "--firewall-id",
        "12345",
        "--ssh-public-key",
        str(key),
    ]


def test_gate5_cli_requires_explicit_two_host_confirmation(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    assert main([*_gate5_arguments(tmp_path, key), "--accept-minecraft-eula"]) == 2


def test_gate5_cli_requires_explicit_eula_acceptance(tmp_path: Path) -> None:
    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAA test")

    assert main([*_gate5_arguments(tmp_path, key), "--confirm-billable-two-host-check"]) == 2


def test_server_unit_create_start_and_status_cli(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "control.db"

    created = main(
        [
            "server-unit-create",
            "--database",
            str(database),
            "--id",
            "survival",
            "--name",
            "Survival",
            "--region",
            "jp-tyo-3",
            "--instance-type",
            "g6-nanode-1",
            "--firewall-id",
            "79203454",
            "--minecraft-image",
            "docker.io/itzg/minecraft-server@sha256:"
            "9faa6aefeedd5a883c3ee241653fd1421529bdbafc428d0513e43cae0f2b7d68",
            "--minecraft-version",
            "1.21.8",
            "--paper-build",
            "1",
            "--accept-minecraft-eula",
        ]
    )
    started = main(
        [
            "server-unit-start",
            "--database",
            str(database),
            "--server-unit-id",
            "survival",
        ]
    )
    capsys.readouterr()
    status_result = main(
        [
            "server-unit-status",
            "--database",
            str(database),
            "--server-unit-id",
            "survival",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert created == started == status_result == 0
    assert output["server_unit"]["id"] == "survival"
    assert output["operation"]["state"] == "pending"
    assert output["provider"] is None
    assert output["host"] is None


def test_host_bootstrap_key_cli_refuses_overwrite(tmp_path: Path) -> None:
    key = tmp_path / "bootstrap.key"

    assert main(["host-bootstrap-key-create", str(key)]) == 0
    assert main(["host-bootstrap-key-create", str(key)]) == 1
    assert key.stat().st_mode & 0o777 == 0o600


def test_short_status_uses_node_configuration(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "control.db"
    assert (
        main(
            [
                "server-unit-create",
                "--database",
                str(database),
                "--id",
                "survival",
                "--name",
                "Survival",
                "--region",
                "jp-tyo-3",
                "--instance-type",
                "g6-nanode-1",
                "--firewall-id",
                "79203454",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[control_plane]
database = "{database}"
system_id = "mc-control-plane"
control_plane_url = "https://control.example.test"
host_bootstrap_key = "{tmp_path}/host-bootstrap.key"
agent_wheel = "{tmp_path}/host-agent.whl"
fixture_image = "docker.io/library/alpine@sha256:{"a" * 64}"
ssh_public_keys = ["{tmp_path}/id_ed25519.pub"]

[host_api]

[r2]
account_id = "account"
bucket = "mccp-data"
parent_access_key_id = "parent"
cloudflare_api_token_file = "{tmp_path}/cloudflare-api-token"
"""
    )

    result = main(["status", "survival", "--config", str(config)])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["server_unit"]["id"] == "survival"


def test_node_check_validates_local_runtime_inputs(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bootstrap_key = tmp_path / "host-bootstrap.key"
    create_bootstrap_key(bootstrap_key)
    wheel = tmp_path / "host-agent.whl"
    wheel.write_bytes(b"wheel")
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text("ssh-ed25519 AAAA test")
    cloudflare_token = tmp_path / "cloudflare-api-token"
    cloudflare_token.write_text("x" * 32)
    os.chmod(cloudflare_token, 0o600)
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[control_plane]
database = "{tmp_path}/control.db"
system_id = "mc-control-plane"
control_plane_url = "https://control.example.test"
host_bootstrap_key = "{bootstrap_key}"
agent_wheel = "{wheel}"
fixture_image = "docker.io/library/alpine@sha256:{"a" * 64}"
ssh_public_keys = ["{ssh_key}"]

[host_api]

[r2]
account_id = "account"
bucket = "mccp-data"
parent_access_key_id = "parent"
cloudflare_api_token_file = "{cloudflare_token}"
"""
    )
    monkeypatch.setenv("LINODE_TOKEN", "linode-token")

    result = main(["node-check", "--config", str(config)])

    assert result == 0
    assert "Node configuration passed" in capsys.readouterr().out
