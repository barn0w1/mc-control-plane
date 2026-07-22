import pytest

from mc_control_plane.application.gate5 import _validate_minecraft_spec


def test_gate5_requires_reproducibly_pinned_minecraft_inputs() -> None:
    _validate_minecraft_spec(
        "docker.io/itzg/minecraft-server@sha256:" + "a" * 64,
        "1.21.8",
        "42",
        "512M",
    )

    with pytest.raises(ValueError, match="digest"):
        _validate_minecraft_spec(
            "docker.io/itzg/minecraft-server:latest",
            "1.21.8",
            "42",
            "512M",
        )
    with pytest.raises(ValueError, match="exact numeric"):
        _validate_minecraft_spec(
            "docker.io/itzg/minecraft-server@sha256:" + "a" * 64,
            "LATEST",
            "42",
            "512M",
        )
