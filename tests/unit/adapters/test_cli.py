import json
from pathlib import Path

from mc_control_plane.adapters.inbound.cli import main


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
