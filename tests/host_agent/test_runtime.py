import json
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
        if values[:2] == ("restic", "cat"):
            self.restic_environments.append(dict(environment or {}))
            return CompletedCommand(0 if self.repository_exists else 10, "", "")
        if values[:2] == ("restic", "init"):
            self.repository_exists = True
            return CompletedCommand(0, "created\n", "")
        if values[:2] == ("restic", "snapshots"):
            document = (
                [] if self.existing_snapshot_id is None else [{"id": self.existing_snapshot_id}]
            )
            return CompletedCommand(0, json.dumps(document), "")
        if values[:2] == ("restic", "backup"):
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


def _lease(permission: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "repository": "s3:https://account.r2.cloudflarestorage.com/bucket/prefix",
        "access_key_id": "temporary-access",
        "secret_access_key": "temporary-secret",
        "session_token": "temporary-session",
        "restic_password": "repository-password",
        "permission": permission,
        "expires_at": "2026-07-22T12:15:00+00:00",
    }


def test_data_repository_snapshot_and_restore_use_fixed_run_directory(tmp_path: Path) -> None:
    runner = FakeRunner()
    runtime = HostRuntime(IMAGE, runner=runner, run_id="run/../../../escape", data_root=tmp_path)

    assert runtime.init_data_repository(_lease("object-read-write"))["state"] == "created"
    written = runtime.write_data_fixture("initial")
    snapshotted = runtime.snapshot_data("command-1", _lease("object-read-write"))

    assert written["file_count"] == 1
    assert snapshotted["snapshot_id"] == "a" * 64
    repeated = runtime.snapshot_data("command-1", _lease("object-read-write"))
    assert repeated["reused"] is True
    assert sum(call[:2] == ("restic", "backup") for call in runner.calls) == 1
    assert any(cwd is not None and cwd.is_relative_to(tmp_path) for cwd in runner.cwds)
    assert any("AWS_SESSION_TOKEN" in env for env in runner.restic_environments)
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
