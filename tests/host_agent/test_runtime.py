from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from mccp_host_agent.runtime import CompletedCommand, HostActionError, HostRuntime

IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fixture_state = "inactive"
        self.stop_state = "inactive"

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
    ) -> CompletedCommand:
        values = tuple(arguments)
        self.calls.append(values)
        if values[:3] == ("systemctl", "show", "--property=LoadState"):
            unit = values[-1]
            state = "active" if unit == "mccp-host-agent.service" else self.fixture_state
            result = "exit-code" if state == "failed" else "success"
            status = "137" if state == "failed" else "0"
            return CompletedCommand(
                0,
                (
                    f"LoadState=loaded\nActiveState={state}\nSubState={state}\n"
                    f"Result={result}\nExecMainCode=1\nExecMainStatus={status}\n"
                ),
                "",
            )
        if values[:2] == ("systemctl", "start"):
            self.fixture_state = "active"
        if values[:2] == ("systemctl", "stop"):
            self.fixture_state = self.stop_state
        if values[-1:] == ("--version",):
            return CompletedCommand(0, f"{values[0]} version-test\n", "")
        if values == ("restic", "version"):
            return CompletedCommand(0, "restic 0.18.0\n", "")
        return CompletedCommand(0, "", "")


def test_quadlet_is_validated_before_atomic_install(tmp_path: Path) -> None:
    runner = FakeRunner()
    quadlets = tmp_path / "quadlets"
    runtime = HostRuntime(
        IMAGE,
        runner=runner,
        quadlet_directory=quadlets,
        generator=Path("/generator"),
    )

    result = runtime.apply_fixture()

    assert len(result["revision"]) == 64
    installed = (quadlets / "mccp-gate2-fixture.container").read_text()
    assert f"Image={IMAGE}" in installed
    assert "sleep 1" in installed
    assert "sleep 300" not in installed
    assert ("/generator", "--dryrun") in runner.calls
    assert (
        "systemd-analyze",
        "--generators=true",
        "verify",
        "mccp-gate2-fixture.service",
    ) in runner.calls
    assert runner.calls.index(("/generator", "--dryrun")) < runner.calls.index(
        ("systemctl", "daemon-reload")
    )


def test_fixture_start_observe_stop_are_idempotent(tmp_path: Path) -> None:
    runner = FakeRunner()
    runtime = HostRuntime(IMAGE, runner=runner, quadlet_directory=tmp_path)

    assert runtime.start_fixture()["fixture"] == "active"
    assert runtime.observe_fixture()["fixture"] == "active"
    assert runtime.stop_fixture()["fixture"] == "inactive"
    assert runtime.stop_fixture()["fixture"] == "inactive"
    assert runner.calls.count(("systemctl", "stop", "mccp-gate2-fixture.service")) == 1


def test_fixture_stop_reports_failed_systemd_details(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.fixture_state = "active"
    runner.stop_state = "failed"
    runtime = HostRuntime(IMAGE, runner=runner, quadlet_directory=tmp_path)

    with pytest.raises(HostActionError, match=r"ExecMainStatus.*137") as captured:
        runtime.stop_fixture()

    assert captured.value.code == "fixture_stop_incomplete"
