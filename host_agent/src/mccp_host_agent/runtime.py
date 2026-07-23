"""Fixed Host actions backed by systemd and Podman Quadlet."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol

FIXTURE_UNIT = "mccp-gate2-fixture.service"
FIXTURE_QUADLET = "mccp-gate2-fixture.container"
AGENT_UNIT = "mccp-host-agent.service"
MINECRAFT_UNIT = "mccp-minecraft.service"
MINECRAFT_QUADLET = "mccp-minecraft.container"
MINECRAFT_CONTAINER = "mccp-minecraft"
MINECRAFT_USER = "mccp-minecraft"
# Match the image-native minecraft identity.  Starting the container as this
# identity makes itzg skip its root-only usermod/groupmod/chown/gosu branch.
MINECRAFT_UID = 1000
MINECRAFT_GID = 1000
RESTORE_MARKER = ".mccp-restored-snapshot"
RESTIC = ("restic", "--insecure-no-password")
_DIGEST_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]+@sha256:[0-9a-f]{64}$")
_MINECRAFT_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
_PAPER_BUILD = re.compile(r"^[1-9][0-9]*$")
_MEMORY = re.compile(r"^[1-9][0-9]*(?:M|G)$")


@dataclass(frozen=True, slots=True)
class CompletedCommand:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> CompletedCommand: ...


class SubprocessCommandRunner:
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> CompletedCommand:
        result = subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None if environment is None else dict(environment),
            cwd=cwd,
        )
        return CompletedCommand(result.returncode, result.stdout, result.stderr)


class HostActionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HostRuntime:
    def __init__(
        self,
        fixture_image: str,
        *,
        runner: CommandRunner | None = None,
        quadlet_directory: Path = Path("/etc/containers/systemd"),
        generator: Path = Path("/usr/lib/systemd/system-generators/podman-system-generator"),
        run_id: str = "local-test-run",
        data_root: Path = Path("/var/lib/mc-control-plane-data"),
        workload_user: str = MINECRAFT_USER,
        workload_uid: int = MINECRAFT_UID,
        workload_gid: int = MINECRAFT_GID,
    ) -> None:
        self._fixture_image = fixture_image
        self._runner = runner or SubprocessCommandRunner()
        self._quadlet_directory = quadlet_directory
        self._generator = generator
        self._run_directory = data_root / hashlib.sha256(run_id.encode()).hexdigest()
        self._workload_user = workload_user
        self._workload_uid = workload_uid
        self._workload_gid = workload_gid

    def capabilities(self) -> dict[str, object]:
        os_release = self._os_release()
        return {
            "os_id": os_release.get("ID", "unknown"),
            "os_version": os_release.get("VERSION_ID", "unknown"),
            "python": self._version(("python3", "--version")),
            "podman": self._version(("podman", "--version")),
            "restic": self._version(("restic", "version")),
            "systemd": self._version(("systemctl", "--version")),
            "quadlet": self._generator.is_file()
            or isinstance(self._runner, SubprocessCommandRunner),
        }

    def boot_id(self) -> str:
        try:
            value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError as error:
            raise HostActionError("boot_id_unavailable", "could not read host boot ID") from error
        if not value:
            raise HostActionError("boot_id_unavailable", "host boot ID was empty")
        return value

    def service_states(self) -> dict[str, str]:
        return {
            "agent": self._service_state(AGENT_UNIT),
            "fixture": self._service_state(FIXTURE_UNIT),
            "minecraft": str(self.observe_minecraft()["minecraft"]),
        }

    def inspect(self) -> dict[str, object]:
        agent = self._service_details(AGENT_UNIT)
        fixture = self._service_details(FIXTURE_UNIT)
        minecraft = self._minecraft_observation()
        return {
            "boot_id": self.boot_id(),
            "capabilities": self.capabilities(),
            "service_states": {
                "agent": self._state_from_details(agent),
                "fixture": self._state_from_details(fixture),
                "minecraft": minecraft["minecraft"],
            },
            "service_details": {
                "agent": agent,
                "fixture": fixture,
                "minecraft": minecraft["systemd"],
            },
            "minecraft": minecraft,
        }

    def apply_fixture(self) -> dict[str, object]:
        content = self._quadlet()
        revision = hashlib.sha256(content.encode()).hexdigest()
        self._install_quadlet(content, FIXTURE_QUADLET, FIXTURE_UNIT)
        return {**self._fixture_observation(), "revision": revision}

    def _install_quadlet(self, content: str, filename: str, unit: str) -> None:
        with tempfile.TemporaryDirectory(prefix="mccp-quadlet-") as temporary:
            staging = Path(temporary)
            (staging / filename).write_text(content)
            environment = {**os.environ, "QUADLET_UNIT_DIRS": str(staging)}
            self._checked(
                (str(self._generator), "--dryrun"),
                timeout=30,
                environment=environment,
                code="quadlet_invalid",
            )
            self._checked(
                (
                    "systemd-analyze",
                    "--generators=true",
                    "verify",
                    unit,
                ),
                timeout=30,
                environment=environment,
                code="quadlet_invalid",
            )

        self._quadlet_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        destination = self._quadlet_directory / filename
        temporary_destination = destination.with_name(f".{destination.name}.new")
        temporary_destination.write_text(content)
        temporary_destination.chmod(0o644)
        temporary_destination.replace(destination)
        self._checked(("systemctl", "daemon-reload"), timeout=30, code="daemon_reload_failed")

    def start_fixture(self) -> dict[str, object]:
        self._checked(
            ("systemctl", "start", FIXTURE_UNIT), timeout=180, code="fixture_start_failed"
        )
        observation = self._fixture_observation()
        state = observation["fixture"]
        if state != "active":
            raise HostActionError("fixture_not_active", f"fixture service is {state}")
        return observation

    def observe_fixture(self) -> dict[str, object]:
        return self._fixture_observation()

    def stop_fixture(self) -> dict[str, object]:
        state = self._service_state(FIXTURE_UNIT)
        if state not in ("inactive", "not-found"):
            self._checked(
                ("systemctl", "stop", FIXTURE_UNIT), timeout=120, code="fixture_stop_failed"
            )
        observation = self._fixture_observation()
        final_state = observation["fixture"]
        if final_state not in ("inactive", "not-found"):
            details = observation["systemd"]
            raise HostActionError(
                "fixture_stop_incomplete",
                f"fixture service is {final_state}; systemd={details}",
            )
        return observation

    def apply_minecraft(
        self,
        *,
        image: str,
        minecraft_version: str,
        paper_build: str,
        memory: str,
        eula: bool,
    ) -> dict[str, object]:
        if not eula:
            raise HostActionError("minecraft_eula_required", "Minecraft EULA was not accepted")
        if not _DIGEST_IMAGE.fullmatch(image):
            raise HostActionError(
                "minecraft_image_invalid", "Minecraft image must be pinned by SHA-256 digest"
            )
        if not _MINECRAFT_VERSION.fullmatch(minecraft_version):
            raise HostActionError(
                "minecraft_version_invalid", "Minecraft version must be an exact numeric version"
            )
        if not _PAPER_BUILD.fullmatch(paper_build):
            raise HostActionError("paper_build_invalid", "Paper build must be a positive integer")
        if not _MEMORY.fullmatch(memory):
            raise HostActionError("minecraft_memory_invalid", "memory must use an M or G suffix")
        if self._service_state(MINECRAFT_UNIT) == "active":
            raise HostActionError(
                "minecraft_apply_while_running", "Minecraft must be stopped before Quadlet apply"
            )
        data = self._data_directory(require_empty=False)
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._prepare_minecraft_data(data)
        content = self._minecraft_quadlet(
            image=image,
            minecraft_version=minecraft_version,
            paper_build=paper_build,
            memory=memory,
            data=data,
            workload_uid=self._workload_uid,
            workload_gid=self._workload_gid,
        )
        revision = hashlib.sha256(content.encode()).hexdigest()
        self._install_quadlet(content, MINECRAFT_QUADLET, MINECRAFT_UNIT)
        return {**self._minecraft_observation(), "revision": revision}

    def start_minecraft(self) -> dict[str, object]:
        observation = self._minecraft_observation()
        if observation["minecraft"] != "ready":
            try:
                self._checked(
                    ("systemctl", "start", MINECRAFT_UNIT),
                    timeout=900,
                    code="minecraft_start_failed",
                )
            except HostActionError as error:
                raise HostActionError(
                    error.code,
                    f"{error}; {self._minecraft_start_diagnostics()}",
                ) from error
            observation = self._minecraft_observation()
        if observation["minecraft"] != "ready":
            raise HostActionError(
                "minecraft_not_ready", f"Minecraft state is {observation['minecraft']}"
            )
        return observation

    def observe_minecraft(self) -> dict[str, object]:
        return self._minecraft_observation()

    def stop_minecraft(self) -> dict[str, object]:
        self._recover_paused_minecraft()
        state = self._service_state(MINECRAFT_UNIT)
        if state not in ("inactive", "not-found"):
            self._checked(
                ("systemctl", "stop", MINECRAFT_UNIT),
                timeout=240,
                code="minecraft_stop_failed",
            )
        observation = self._minecraft_observation()
        if observation["minecraft"] != "stopped":
            raise HostActionError(
                "minecraft_stop_incomplete",
                f"Minecraft state is {observation['minecraft']}; systemd={observation['systemd']}",
            )
        return observation

    def snapshot_minecraft_data(
        self,
        command_id: str,
        lease: Mapping[str, object],
    ) -> dict[str, object]:
        self._recover_paused_minecraft()
        if self._minecraft_observation()["minecraft"] != "ready":
            raise HostActionError(
                "minecraft_not_ready", "Minecraft must be ready for a live snapshot"
            )
        self._rcon("save-off")
        paused = False
        try:
            self._rcon("save-all", "flush")
            self._checked(
                ("podman", "pause", MINECRAFT_CONTAINER),
                timeout=30,
                code="minecraft_pause_failed",
            )
            paused = True
            return self.snapshot_data(command_id, lease)
        finally:
            if paused:
                self._checked(
                    ("podman", "unpause", MINECRAFT_CONTAINER),
                    timeout=30,
                    code="minecraft_resume_failed",
                )
            self._rcon("save-on", code="minecraft_resume_failed")

    def init_data_repository(self, lease: Mapping[str, object]) -> dict[str, object]:
        with self._restic_environment(lease, required_permission="object-read-write") as env:
            probe = self._run_restic((*RESTIC, "cat", "config"), env, timeout=60)
            if probe.returncode == 10:
                initialized = self._run_restic(
                    (*RESTIC, "init", "--repository-version", "2"), env, timeout=120
                )
                if initialized.returncode != 0:
                    raise HostActionError(
                        "repository_init_failed",
                        self._restic_error(initialized, "repository init failed"),
                    )
                state = "created"
            elif probe.returncode == 0:
                state = "existing"
            else:
                raise HostActionError(
                    "repository_probe_failed", self._restic_error(probe, "repository probe failed")
                )
        return {"repository": "ready", "state": state}

    def write_data_fixture(self, revision: str) -> dict[str, object]:
        if revision not in {"initial", "modified"}:
            raise HostActionError("fixture_revision_invalid", "unknown data fixture revision")
        data = self._data_directory(require_empty=False)
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = data / "gate4-fixture.json"
        target.write_text(
            json.dumps({"gate": 4, "revision": revision}, separators=(",", ":")) + "\n"
        )
        target.chmod(0o600)
        return self.observe_data()

    def snapshot_data(self, command_id: str, lease: Mapping[str, object]) -> dict[str, object]:
        data = self._data_directory(require_empty=False)
        if not data.is_dir():
            raise HostActionError("data_missing", "Run data directory does not exist")
        idempotency_tag = "mccp-command-" + hashlib.sha256(command_id.encode()).hexdigest()
        with self._restic_environment(lease, required_permission="object-read-write") as env:
            existing = self._run_restic(
                (*RESTIC, "snapshots", "--json", "--tag", idempotency_tag),
                env,
                timeout=120,
            )
            if existing.returncode != 0:
                raise HostActionError(
                    "snapshot_lookup_failed",
                    self._restic_error(existing, "snapshot idempotency lookup failed"),
                )
            existing_id = self._existing_snapshot_id(existing.stdout)
            if existing_id is not None:
                return {"snapshot_id": existing_id, "reused": True, **self.observe_data()}
            result = self._run_restic(
                (
                    *RESTIC,
                    "backup",
                    "--json",
                    "--host",
                    "mc-control-plane",
                    "--tag",
                    "mc-control-plane",
                    "--tag",
                    idempotency_tag,
                    "--exclude",
                    f"/{RESTORE_MARKER}",
                    ".",
                ),
                env,
                timeout=1800,
                cwd=data,
            )
        if result.returncode != 0:
            code = "snapshot_partial" if result.returncode == 3 else "snapshot_failed"
            raise HostActionError(code, self._restic_error(result, code))
        snapshot_id = self._snapshot_id(result.stdout)
        return {"snapshot_id": snapshot_id, "reused": False, **self.observe_data()}

    def restore_data(self, snapshot_id: str, lease: Mapping[str, object]) -> dict[str, object]:
        if not snapshot_id or any(character not in "0123456789abcdef" for character in snapshot_id):
            raise HostActionError(
                "snapshot_id_invalid", "snapshot ID must be lowercase hexadecimal"
            )
        if len(snapshot_id) < 8 or len(snapshot_id) > 64:
            raise HostActionError("snapshot_id_invalid", "snapshot ID length is invalid")
        data = self._data_directory(require_empty=False)
        marker = data / RESTORE_MARKER
        if data.exists() and any(data.iterdir()):
            try:
                restored_id = marker.read_text().strip()
            except OSError:
                restored_id = ""
            if restored_id == snapshot_id:
                return {"snapshot_id": snapshot_id, "reused": True, **self.observe_data()}
            raise HostActionError("restore_target_not_empty", "restore target is not empty")
        staging = self._run_directory / ".restore-staging"
        if staging.is_symlink():
            raise HostActionError("unsafe_data_path", "restore staging path is a symlink")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o700, parents=True)
        try:
            with self._restic_environment(lease, required_permission="object-read-only") as env:
                result = self._run_restic(
                    (
                        *RESTIC,
                        "--no-lock",
                        "restore",
                        f"{snapshot_id}:/",
                        "--target",
                        str(staging),
                    ),
                    env,
                    timeout=1800,
                )
            if result.returncode != 0:
                raise HostActionError(
                    "restore_failed", self._restic_error(result, "restore failed")
                )
            (staging / RESTORE_MARKER).write_text(snapshot_id + "\n")
            (staging / RESTORE_MARKER).chmod(0o600)
            if data.exists():
                data.rmdir()
            staging.replace(data)
        except BaseException:
            if staging.exists() and not staging.is_symlink():
                shutil.rmtree(staging)
            raise
        return {"snapshot_id": snapshot_id, "reused": False, **self.observe_data()}

    def observe_data(self) -> dict[str, object]:
        data = self._data_directory(require_empty=False)
        if not data.exists():
            return {"data_state": "absent", "file_count": 0, "content_sha256": None}
        digest = hashlib.sha256()
        count = 0
        for path in sorted(data.rglob("*")):
            if path == data / RESTORE_MARKER:
                continue
            if path.is_symlink():
                raise HostActionError("unsafe_data_path", "Run data contains a non-file entry")
            if path.is_dir():
                continue
            if not path.is_file():
                raise HostActionError("unsafe_data_path", "Run data contains a special file")
            relative = path.relative_to(data).as_posix()
            digest.update(relative.encode() + b"\0")
            digest.update(path.read_bytes())
            count += 1
        return {
            "data_state": "ready",
            "file_count": count,
            "content_sha256": digest.hexdigest(),
        }

    def _service_state(self, unit: str) -> str:
        return self._state_from_details(self._service_details(unit))

    def _service_details(self, unit: str) -> dict[str, str]:
        result = self._runner.run(
            (
                "systemctl",
                "show",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--property=ExecMainCode",
                "--property=ExecMainStatus",
                unit,
            ),
            timeout=15,
        )
        values = {
            key: value
            for line in result.stdout.splitlines()
            if "=" in line
            for key, value in (line.split("=", 1),)
        }
        if result.returncode != 0:
            values["ShowReturnCode"] = str(result.returncode)
            if result.stderr.strip():
                values["ShowError"] = result.stderr.strip().splitlines()[-1][:300]
        return values

    @staticmethod
    def _state_from_details(values: Mapping[str, str]) -> str:
        if values.get("LoadState") == "not-found":
            return "not-found"
        return values.get("ActiveState", "unknown")

    def _fixture_observation(self) -> dict[str, object]:
        details = self._service_details(FIXTURE_UNIT)
        return {"fixture": self._state_from_details(details), "systemd": details}

    def _minecraft_observation(self) -> dict[str, object]:
        systemd = self._service_details(MINECRAFT_UNIT)
        service_state = self._state_from_details(systemd)
        container = self._minecraft_container_state()
        if (
            service_state == "active"
            and container["status"] == "running"
            and container["health"] == "healthy"
            and container["paused"] is False
        ):
            state = "ready"
        elif service_state in ("inactive", "not-found") and container["status"] == "absent":
            state = "stopped"
        elif service_state == "failed" or container["health"] == "unhealthy":
            state = "failed"
        elif service_state == "active" or container["status"] in ("created", "running"):
            state = "starting"
        else:
            state = "unknown"
        return {"minecraft": state, "systemd": systemd, "container": container}

    def _minecraft_container_state(self) -> dict[str, object]:
        result = self._runner.run(
            (
                "podman",
                "inspect",
                "--format",
                "{{json .State}}",
                MINECRAFT_CONTAINER,
            ),
            timeout=15,
        )
        if result.returncode != 0:
            return {"status": "absent", "health": "absent", "paused": False}
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise HostActionError(
                "minecraft_observation_invalid", "Podman returned invalid container state"
            ) from error
        if not isinstance(value, dict):
            raise HostActionError(
                "minecraft_observation_invalid", "Podman returned invalid container state"
            )
        health_value = value.get("Healthcheck")
        if not isinstance(health_value, dict):
            health_value = value.get("Health")
        health = health_value.get("Status") if isinstance(health_value, dict) else None
        return {
            "status": str(value.get("Status", "unknown")),
            "health": str(health or "unknown"),
            "paused": value.get("Paused") is True,
        }

    def _minecraft_start_diagnostics(self) -> str:
        try:
            observation = self._minecraft_observation()
        except (HostActionError, OSError, subprocess.SubprocessError):
            observation = {"minecraft": "unavailable"}
        configured_user = self._diagnostic_command(
            (
                "podman",
                "inspect",
                "--format",
                "{{.Config.User}}",
                MINECRAFT_CONTAINER,
            ),
            limit=80,
        )
        processes = self._diagnostic_command(
            ("podman", "top", MINECRAFT_CONTAINER, "user,pid,comm"),
            limit=240,
        )
        try:
            logs = self._runner.run(
                ("podman", "logs", "--tail", "20", MINECRAFT_CONTAINER),
                timeout=15,
            )
            log_tail = " | ".join(logs.stdout.strip().splitlines()[-4:])[-240:]
            if not log_tail:
                log_tail = "unavailable"
        except (OSError, subprocess.SubprocessError):
            log_tail = "unavailable"
        return (
            "diagnostics="
            + json.dumps(observation, separators=(",", ":"), sort_keys=True)[:220]
            + f"; configured_user={configured_user}; processes={processes}; logs={log_tail}"
        )

    def _diagnostic_command(self, arguments: Sequence[str], *, limit: int) -> str:
        try:
            result = self._runner.run(arguments, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        if result.returncode != 0:
            return "unavailable"
        output = " | ".join(result.stdout.strip().splitlines())
        return output[-limit:] if output else "unavailable"

    def _recover_paused_minecraft(self) -> None:
        state = self._minecraft_container_state()
        if state["paused"] is True:
            self._checked(
                ("podman", "unpause", MINECRAFT_CONTAINER),
                timeout=30,
                code="minecraft_resume_failed",
            )
            self._rcon("save-on", code="minecraft_resume_failed")

    def _rcon(self, *command: str, code: str = "minecraft_quiesce_failed") -> None:
        self._checked(
            ("podman", "exec", MINECRAFT_CONTAINER, "rcon-cli", *command),
            timeout=30,
            code=code,
        )

    def _version(self, arguments: Sequence[str]) -> str:
        try:
            result = self._runner.run(arguments, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        if result.returncode != 0:
            return "unavailable"
        return result.stdout.strip().splitlines()[0][:200] if result.stdout.strip() else "unknown"

    @staticmethod
    def _os_release() -> dict[str, str]:
        try:
            lines = Path("/etc/os-release").read_text().splitlines()
        except OSError:
            return {}
        return {
            key: value.strip('"')
            for line in lines
            if "=" in line
            for key, value in (line.split("=", 1),)
        }

    def _checked(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
        code: str,
        environment: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        try:
            result = self._runner.run(arguments, timeout=timeout, environment=environment, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as error:
            raise HostActionError(code, f"{arguments[0]} could not run") from error
        if result.returncode != 0:
            message = (
                result.stderr.strip().splitlines()[-1][:300] if result.stderr.strip() else code
            )
            raise HostActionError(code, message)

    def _data_directory(self, *, require_empty: bool) -> Path:
        self._run_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._run_directory.is_symlink():
            raise HostActionError("unsafe_data_path", "Run data root is a symlink")
        self._run_directory.mkdir(mode=0o700, exist_ok=True)
        data = self._run_directory / "data"
        if data.is_symlink():
            raise HostActionError("unsafe_data_path", "Run data path is a symlink")
        if require_empty and data.exists() and any(data.iterdir()):
            raise HostActionError("restore_target_not_empty", "restore target is not empty")
        return data

    def _prepare_minecraft_data(self, data: Path) -> None:
        passwd = self._runner.run(
            ("getent", "passwd", self._workload_user),
            timeout=10,
        )
        fields = passwd.stdout.strip().split(":")
        if (
            passwd.returncode != 0
            or len(fields) != 7
            or fields[0] != self._workload_user
            or fields[2] != str(self._workload_uid)
            or fields[3] != str(self._workload_gid)
            or fields[6] != "/usr/sbin/nologin"
        ):
            raise HostActionError(
                "minecraft_identity_invalid",
                "dedicated Minecraft workload account is missing or inconsistent",
            )
        group = self._runner.run(
            ("getent", "group", self._workload_user),
            timeout=10,
        )
        group_fields = group.stdout.strip().split(":")
        if (
            group.returncode != 0
            or len(group_fields) != 4
            or group_fields[0] != self._workload_user
            or group_fields[2] != str(self._workload_gid)
        ):
            raise HostActionError(
                "minecraft_identity_invalid",
                "dedicated Minecraft workload group is missing or inconsistent",
            )
        try:
            os.chown(data, self._workload_uid, self._workload_gid)
            data.chmod(0o700)
        except OSError as error:
            raise HostActionError(
                "minecraft_data_ownership_failed",
                "could not apply Minecraft data directory ownership",
            ) from error
        for path in (data, *sorted(data.rglob("*"))):
            if path == data / RESTORE_MARKER:
                continue
            if path != data and (path.is_symlink() or not (path.is_dir() or path.is_file())):
                raise HostActionError(
                    "unsafe_data_path",
                    "Minecraft data contains a symlink or special file",
                )
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError as error:
                raise HostActionError(
                    "minecraft_data_ownership_invalid",
                    "could not inspect Minecraft data ownership",
                ) from error
            if metadata.st_uid != self._workload_uid or metadata.st_gid != self._workload_gid:
                relative = "." if path == data else path.relative_to(data).as_posix()
                raise HostActionError(
                    "minecraft_data_ownership_invalid",
                    f"Minecraft data entry {relative!r} is owned by "
                    f"{metadata.st_uid}:{metadata.st_gid}; expected "
                    f"{self._workload_uid}:{self._workload_gid}",
                )

    def _restic_environment(
        self, lease: Mapping[str, object], *, required_permission: str
    ) -> "_ResticEnvironment":
        return _ResticEnvironment(lease, required_permission)

    def _run_restic(
        self,
        arguments: Sequence[str],
        environment: Mapping[str, str],
        *,
        timeout: float,
        cwd: Path | None = None,
    ) -> CompletedCommand:
        if tuple(arguments[: len(RESTIC)]) != RESTIC:
            raise HostActionError(
                "restic_password_mode_missing",
                "repository command must explicitly use passwordless mode",
            )
        try:
            return self._runner.run(arguments, timeout=timeout, environment=environment, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as error:
            raise HostActionError("restic_execution_failed", "restic could not run") from error

    @staticmethod
    def _snapshot_id(output: str) -> str:
        for line in reversed(output.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("message_type") == "summary":
                snapshot_id = value.get("snapshot_id")
                if isinstance(snapshot_id, str) and snapshot_id:
                    return snapshot_id
        raise HostActionError("snapshot_id_missing", "restic success omitted snapshot ID")

    @staticmethod
    def _existing_snapshot_id(output: str) -> str | None:
        try:
            value = json.loads(output)
        except json.JSONDecodeError as error:
            raise HostActionError(
                "snapshot_lookup_invalid", "restic snapshots returned invalid JSON"
            ) from error
        if not isinstance(value, list):
            raise HostActionError(
                "snapshot_lookup_invalid", "restic snapshots returned an invalid document"
            )
        ids = [item.get("id") for item in value if isinstance(item, dict)]
        valid = [item for item in ids if isinstance(item, str) and item]
        if len(valid) > 1:
            raise HostActionError(
                "snapshot_idempotency_conflict", "multiple snapshots use one command identity"
            )
        return valid[0] if valid else None

    @staticmethod
    def _restic_error(result: CompletedCommand, fallback: str) -> str:
        message = result.stderr.strip().splitlines()
        return (message[-1] if message else fallback)[:300]

    def _quadlet(self) -> str:
        return (
            "[Unit]\n"
            "Description=mc-control-plane Gate 2 fixture\n\n"
            "[Container]\n"
            f"Image={self._fixture_image}\n"
            "ContainerName=mccp-gate2-fixture\n"
            "Pull=missing\n"
            "Exec=sh -c \"trap 'exit 0' TERM INT; while true; do sleep 1; done\"\n\n"
            "[Service]\n"
            "Restart=on-failure\n"
            "TimeoutStartSec=180\n"
            "TimeoutStopSec=120\n"
        )

    @staticmethod
    def _minecraft_quadlet(
        *,
        image: str,
        minecraft_version: str,
        paper_build: str,
        memory: str,
        data: Path,
        workload_uid: int,
        workload_gid: int,
    ) -> str:
        return (
            "[Unit]\n"
            "Description=mc-control-plane Paper Minecraft server\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Container]\n"
            f"Image={image}\n"
            f"ContainerName={MINECRAFT_CONTAINER}\n"
            f"User={workload_uid}\n"
            f"Group={workload_gid}\n"
            "Pull=missing\n"
            f"Volume={data}:/data\n"
            "PublishPort=25565:25565/tcp\n"
            "Environment=EULA=TRUE\n"
            f"Environment=UID={workload_uid}\n"
            f"Environment=GID={workload_gid}\n"
            "Environment=SKIP_CHOWN_DATA=TRUE\n"
            "Environment=TYPE=PAPER\n"
            f"Environment=VERSION={minecraft_version}\n"
            f"Environment=PAPER_BUILD={paper_build}\n"
            f"Environment=MEMORY={memory}\n"
            "Environment=ENABLE_RCON=TRUE\n"
            "Environment=STOP_DURATION=120\n"
            "StopTimeout=180\n"
            "HealthCmd=mc-health\n"
            "HealthInterval=10s\n"
            "HealthTimeout=10s\n"
            "HealthRetries=60\n"
            "HealthStartPeriod=2m\n"
            "Notify=healthy\n"
            "DropCapability=all\n"
            "NoNewPrivileges=true\n\n"
            "[Service]\n"
            "Restart=on-failure\n"
            "TimeoutStartSec=900\n"
            "TimeoutStopSec=240\n"
        )


class _ResticEnvironment:
    def __init__(self, lease: Mapping[str, object], required_permission: str) -> None:
        required = {
            "schema_version",
            "repository",
            "access_key_id",
            "secret_access_key",
            "session_token",
            "permission",
            "expires_at",
        }
        if set(lease) != required or lease.get("schema_version") != 2:
            raise HostActionError("data_lease_invalid", "data lease fields are invalid")
        values = {name: lease.get(name) for name in required - {"schema_version"}}
        if not all(isinstance(value, str) and value for value in values.values()):
            raise HostActionError("data_lease_invalid", "data lease values are invalid")
        if values["permission"] != required_permission:
            raise HostActionError("data_lease_permission", "data lease permission is insufficient")
        ambient = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("RESTIC_PASSWORD") and name != "RESTIC_KEY_HINT"
        }
        self.environment = {
            **ambient,
            "RESTIC_REPOSITORY": str(values["repository"]),
            "AWS_ACCESS_KEY_ID": str(values["access_key_id"]),
            "AWS_SECRET_ACCESS_KEY": str(values["secret_access_key"]),
            "AWS_SESSION_TOKEN": str(values["session_token"]),
            "AWS_DEFAULT_REGION": "auto",
            "AWS_REGION": "auto",
            "AWS_EC2_METADATA_DISABLED": "true",
        }

    def __enter__(self) -> Mapping[str, str]:
        return self.environment

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None
