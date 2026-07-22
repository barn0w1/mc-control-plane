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
