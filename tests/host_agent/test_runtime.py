import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from mccp_host_agent.runtime import CompletedCommand, HostActionError, HostRuntime

IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64
MINECRAFT_IMAGE = "docker.io/itzg/minecraft-server@sha256:" + "b" * 64


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fixture_state = "inactive"
        self.stop_state = "inactive"
        self.minecraft_state = "inactive"
        self.minecraft_container_status = "absent"
        self.minecraft_health = "absent"
        self.minecraft_paused = False
        self.repository_exists = False
        self.backup_returncode = 0
        self.restic_environments: list[Mapping[str, str]] = []
        self.cwds: list[Path | None] = []
        self.existing_snapshot_id: str | None = None

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> CompletedCommand:
        values = tuple(arguments)
        self.calls.append(values)
        self.cwds.append(cwd)
        if values[:3] == ("restic", "--insecure-no-password", "cat"):
            self.restic_environments.append(dict(environment or {}))
            return CompletedCommand(0 if self.repository_exists else 10, "", "")
        if values[:3] == ("restic", "--insecure-no-password", "init"):
            self.repository_exists = True
            return CompletedCommand(0, "created\n", "")
        if values[:3] == ("restic", "--insecure-no-password", "snapshots"):
            document = (
                [] if self.existing_snapshot_id is None else [{"id": self.existing_snapshot_id}]
            )
            return CompletedCommand(0, json.dumps(document), "")
        if values[:3] == ("restic", "--insecure-no-password", "backup"):
            self.restic_environments.append(dict(environment or {}))
            result = CompletedCommand(
                self.backup_returncode,
                json.dumps({"message_type": "summary", "snapshot_id": "a" * 64}) + "\n",
                "partial" if self.backup_returncode else "",
            )
            if self.backup_returncode == 0:
                self.existing_snapshot_id = "a" * 64
            return result
        if "restore" in values:
            self.restic_environments.append(dict(environment or {}))
            target = Path(values[values.index("--target") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "gate4-fixture.json").write_text('{"gate":4,"revision":"initial"}\n')
            return CompletedCommand(0, "", "")
        if values[:3] == ("systemctl", "show", "--property=LoadState"):
            unit = values[-1]
            if unit == "mccp-host-agent.service":
                state = "active"
            elif unit == "mccp-minecraft.service":
                state = self.minecraft_state
            else:
                state = self.fixture_state
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
            if values[-1] == "mccp-minecraft.service":
                self.minecraft_state = "active"
                self.minecraft_container_status = "running"
                self.minecraft_health = "healthy"
            else:
                self.fixture_state = "active"
        if values[:2] == ("systemctl", "stop"):
            if values[-1] == "mccp-minecraft.service":
                self.minecraft_state = "inactive"
                self.minecraft_container_status = "absent"
                self.minecraft_health = "absent"
                self.minecraft_paused = False
            else:
                self.fixture_state = self.stop_state
        if values[:2] == ("podman", "inspect"):
            if self.minecraft_container_status == "absent":
                return CompletedCommand(1, "", "no such container")
            return CompletedCommand(
                0,
                json.dumps(
                    {
                        "Status": self.minecraft_container_status,
                        "Paused": self.minecraft_paused,
                        "Healthcheck": {"Status": self.minecraft_health},
                    }
                ),
                "",
            )
        if values[:2] == ("podman", "pause"):
            self.minecraft_paused = True
        if values[:2] == ("podman", "unpause"):
            self.minecraft_paused = False
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


def test_minecraft_quadlet_is_pinned_and_health_gated(tmp_path: Path) -> None:
    runner = FakeRunner()
    quadlets = tmp_path / "quadlets"
    runtime = HostRuntime(
        IMAGE,
        runner=runner,
        quadlet_directory=quadlets,
        generator=Path("/generator"),
        run_id="run-1",
        data_root=tmp_path / "data",
    )

    result = runtime.apply_minecraft(
        image=MINECRAFT_IMAGE,
        minecraft_version="1.21.8",
        paper_build="42",
        memory="512M",
        eula=True,
    )

    assert result["minecraft"] == "stopped"
    assert len(result["revision"]) == 64
    installed = (quadlets / "mccp-minecraft.container").read_text()
    assert f"Image={MINECRAFT_IMAGE}" in installed
    assert "Environment=TYPE=PAPER" in installed
    assert "Environment=VERSION=1.21.8" in installed
    assert "Environment=PAPER_BUILD=42" in installed
    assert "Environment=MEMORY=512M" in installed
    assert "Environment=EULA=TRUE" in installed
    assert "PublishPort=25565:25565/tcp" in installed
    assert "HealthCmd=mc-health" in installed
    assert "Notify=healthy" in installed
    assert "StopTimeout=180" in installed
    assert "TimeoutStopSec=240" in installed
    assert "latest" not in installed
    assert (
        "systemd-analyze",
        "--generators=true",
        "verify",
        "mccp-minecraft.service",
    ) in runner.calls


def test_minecraft_lifecycle_and_live_snapshot_are_consistent(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.repository_exists = True
    runtime = HostRuntime(
        IMAGE,
        runner=runner,
        quadlet_directory=tmp_path / "quadlets",
        generator=Path("/generator"),
        run_id="run-1",
        data_root=tmp_path / "data",
    )
    runtime.apply_minecraft(
        image=MINECRAFT_IMAGE,
        minecraft_version="1.21.8",
        paper_build="42",
        memory="512M",
        eula=True,
    )
    (runtime._data_directory(require_empty=False) / "world.dat").write_bytes(b"world")

    assert runtime.start_minecraft()["minecraft"] == "ready"
    assert runtime.observe_minecraft()["minecraft"] == "ready"
    snapshot = runtime.snapshot_minecraft_data("command-1", _lease("object-read-write"))
    assert snapshot["snapshot_id"] == "a" * 64
    assert runtime.observe_minecraft()["minecraft"] == "ready"
    assert runtime.stop_minecraft()["minecraft"] == "stopped"
    assert runtime.stop_minecraft()["minecraft"] == "stopped"

    save_off = ("podman", "exec", "mccp-minecraft", "rcon-cli", "save-off")
    flush = ("podman", "exec", "mccp-minecraft", "rcon-cli", "save-all", "flush")
    pause = ("podman", "pause", "mccp-minecraft")
    unpause = ("podman", "unpause", "mccp-minecraft")
    save_on = ("podman", "exec", "mccp-minecraft", "rcon-cli", "save-on")
    backup_index = next(
        index
        for index, call in enumerate(runner.calls)
        if call[:3] == ("restic", "--insecure-no-password", "backup")
    )
    assert runner.calls.index(save_off) < runner.calls.index(flush)
    assert runner.calls.index(flush) < runner.calls.index(pause) < backup_index
    assert backup_index < runner.calls.index(unpause) < runner.calls.index(save_on)


def test_live_snapshot_recovers_a_previously_paused_minecraft(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.repository_exists = True
    runner.minecraft_state = "active"
    runner.minecraft_container_status = "running"
    runner.minecraft_health = "healthy"
    runner.minecraft_paused = True
    runtime = HostRuntime(IMAGE, runner=runner, run_id="run-1", data_root=tmp_path)
    runtime._data_directory(require_empty=False).mkdir(parents=True, exist_ok=True)
    (runtime._data_directory(require_empty=False) / "world.dat").write_bytes(b"world")

    runtime.snapshot_minecraft_data("command-1", _lease("object-read-write"))

    first_unpause = runner.calls.index(("podman", "unpause", "mccp-minecraft"))
    first_save_on = runner.calls.index(("podman", "exec", "mccp-minecraft", "rcon-cli", "save-on"))
    next_save_off = runner.calls.index(("podman", "exec", "mccp-minecraft", "rcon-cli", "save-off"))
    assert first_unpause < first_save_on < next_save_off


def _lease(permission: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "repository": "s3:https://account.r2.cloudflarestorage.com/bucket/prefix",
        "access_key_id": "temporary-access",
        "secret_access_key": "temporary-secret",
        "session_token": "temporary-session",
        "permission": permission,
        "expires_at": "2026-07-22T12:15:00+00:00",
    }


def test_data_repository_snapshot_and_restore_use_fixed_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESTIC_PASSWORD", "ambient-password")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", "/ambient/password-file")
    monkeypatch.setenv("RESTIC_PASSWORD_COMMAND", "ambient-command")
    monkeypatch.setenv("RESTIC_KEY_HINT", "ambient-key")
    runner = FakeRunner()
    runtime = HostRuntime(IMAGE, runner=runner, run_id="run/../../../escape", data_root=tmp_path)

    assert runtime.init_data_repository(_lease("object-read-write"))["state"] == "created"
    written = runtime.write_data_fixture("initial")
    snapshotted = runtime.snapshot_data("command-1", _lease("object-read-write"))

    assert written["file_count"] == 1
    assert snapshotted["snapshot_id"] == "a" * 64
    repeated = runtime.snapshot_data("command-1", _lease("object-read-write"))
    assert repeated["reused"] is True
    assert (
        sum(call[:3] == ("restic", "--insecure-no-password", "backup") for call in runner.calls)
        == 1
    )
    assert any(cwd is not None and cwd.is_relative_to(tmp_path) for cwd in runner.cwds)
    assert any("AWS_SESSION_TOKEN" in env for env in runner.restic_environments)
    assert all(
        not any(name.startswith("RESTIC_PASSWORD") for name in env) and "RESTIC_KEY_HINT" not in env
        for env in runner.restic_environments
    )
    repository_commands = [
        call for call in runner.calls if call[0] == "restic" and "version" not in call
    ]
    assert repository_commands
    assert all(call[1] == "--insecure-no-password" for call in repository_commands)
    assert not any(path.name == "escape" for path in tmp_path.rglob("*"))

    fresh = HostRuntime(IMAGE, runner=runner, run_id="fresh-run", data_root=tmp_path)
    restored = fresh.restore_data("a" * 64, _lease("object-read-only"))
    assert restored["content_sha256"] == written["content_sha256"]
    repeated_restore = fresh.restore_data("a" * 64, _lease("object-read-only"))
    assert repeated_restore["reused"] is True
    assert sum("restore" in call for call in runner.calls) == 1


def test_partial_restic_backup_is_never_a_snapshot(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.repository_exists = True
    runner.backup_returncode = 3
    runtime = HostRuntime(IMAGE, runner=runner, run_id="run-1", data_root=tmp_path)
    runtime.write_data_fixture("initial")

    with pytest.raises(HostActionError) as captured:
        runtime.snapshot_data("command-1", _lease("object-read-write"))

    assert captured.value.code == "snapshot_partial"
