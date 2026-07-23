from collections.abc import Callable
from typing import Any

import pytest

from mc_control_plane.application.gate5 import (
    Gate5Error,
    _validate_minecraft_spec,
    _verify_host_runtime,
)


class _Store:
    pass


class _Clock:
    pass


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


def test_gate5_rejects_host_that_cannot_observe_podman(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_command(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "capabilities": {"podman": "podman version 5.4.2"},
            "service_states": {"minecraft": "unknown"},
        }

    monkeypatch.setattr("mc_control_plane.application.gate5._command", fake_command)

    with pytest.raises(Gate5Error, match="cannot observe"):
        _verify_host_runtime(
            _Store(),  # type: ignore[arg-type]
            "agent-1",
            "unit-1",
            _Clock(),  # type: ignore[arg-type]
            10,
            1,
            lambda _: None,
            lambda _: None,
        )


def test_gate5_accepts_fresh_observable_podman(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    def fake_command(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "capabilities": {"podman": "podman version 5.4.2"},
            "service_states": {"minecraft": "not-found"},
        }

    monkeypatch.setattr("mc_control_plane.application.gate5._command", fake_command)
    report: Callable[[str], None] = messages.append

    _verify_host_runtime(
        _Store(),  # type: ignore[arg-type]
        "agent-1",
        "unit-1",
        _Clock(),  # type: ignore[arg-type]
        10,
        1,
        lambda _: None,
        report,
    )

    assert messages == ["fresh Host Podman preflight passed"]
