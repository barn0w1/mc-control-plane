"""Fixed Host actions backed by systemd and Podman Quadlet."""

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

FIXTURE_UNIT = "mccp-gate2-fixture.service"
FIXTURE_QUADLET = "mccp-gate2-fixture.container"
AGENT_UNIT = "mccp-host-agent.service"


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
    ) -> CompletedCommand: ...


class SubprocessCommandRunner:
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
        environment: Mapping[str, str] | None = None,
    ) -> CompletedCommand:
        result = subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None if environment is None else dict(environment),
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
    ) -> None:
        self._fixture_image = fixture_image
        self._runner = runner or SubprocessCommandRunner()
        self._quadlet_directory = quadlet_directory
        self._generator = generator

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
        }

    def inspect(self) -> dict[str, object]:
        agent = self._service_details(AGENT_UNIT)
        fixture = self._service_details(FIXTURE_UNIT)
        return {
            "boot_id": self.boot_id(),
            "capabilities": self.capabilities(),
            "service_states": {
                "agent": self._state_from_details(agent),
                "fixture": self._state_from_details(fixture),
            },
            "service_details": {"agent": agent, "fixture": fixture},
        }

    def apply_fixture(self) -> dict[str, object]:
        content = self._quadlet()
        revision = hashlib.sha256(content.encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="mccp-quadlet-") as temporary:
            staging = Path(temporary)
            (staging / FIXTURE_QUADLET).write_text(content)
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
                    FIXTURE_UNIT,
                ),
                timeout=30,
                environment=environment,
                code="quadlet_invalid",
            )

        self._quadlet_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        destination = self._quadlet_directory / FIXTURE_QUADLET
        temporary_destination = destination.with_name(f".{destination.name}.new")
        temporary_destination.write_text(content)
        temporary_destination.chmod(0o644)
        temporary_destination.replace(destination)
        self._checked(("systemctl", "daemon-reload"), timeout=30, code="daemon_reload_failed")
        return {**self._fixture_observation(), "revision": revision}

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
    ) -> None:
        try:
            result = self._runner.run(arguments, timeout=timeout, environment=environment)
        except (OSError, subprocess.SubprocessError) as error:
            raise HostActionError(code, f"{arguments[0]} could not run") from error
        if result.returncode != 0:
            message = (
                result.stderr.strip().splitlines()[-1][:300] if result.stderr.strip() else code
            )
            raise HostActionError(code, message)

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
