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
