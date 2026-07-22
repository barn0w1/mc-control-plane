"""Enrollment, polling, and fixed command execution."""

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mccp_host_agent import PROTOCOL_VERSION, __version__
from mccp_host_agent.client import HostApiClient
from mccp_host_agent.config import AgentConfig, save_config
from mccp_host_agent.journal import CommandJournal
from mccp_host_agent.runtime import HostActionError, HostRuntime


class HostAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        config_path: Path,
        token_path: Path,
        journal: CommandJournal,
        client: HostApiClient,
        runtime: HostRuntime,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._token_path = token_path
        self._journal = journal
        self._client = client
        self._runtime = runtime

    def run_once(self) -> int:
        agent_token = self._agent_token()
        if self._config.enrollment_token is not None:
            self._enroll(agent_token)

        pending = self._journal.unreported()
        response = self._client.poll(
            agent_token,
            {
                "protocol_version": PROTOCOL_VERSION,
                "agent_id": self._config.agent_id,
                "run_id": self._config.run_id,
                "agent_version": __version__,
                "boot_id": self._runtime.boot_id(),
                "capabilities": self._runtime.capabilities(),
                "service_states": self._runtime.service_states(),
                "results": [item.value for item in pending],
            },
        )
        if response.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("Control Plane protocol version is incompatible")
        self._journal.mark_reported([item.command_id for item in pending])
        command = response.get("command")
        if command is not None:
            if not isinstance(command, dict) or not all(isinstance(key, str) for key in command):
                raise ValueError("Control Plane returned an invalid command")
            self._handle(cast(dict[str, Any], command))
            return 0
        delay = response.get("poll_after_seconds", self._config.poll_seconds)
        if not isinstance(delay, int) or delay <= 0 or delay > 300:
            raise ValueError("Control Plane returned an invalid poll interval")
        return delay

    def _enroll(self, agent_token: str) -> None:
        enrollment_token = self._config.enrollment_token
        if enrollment_token is None:
            return
        response = self._client.enroll(
            {
                "protocol_version": PROTOCOL_VERSION,
                "agent_id": self._config.agent_id,
                "run_id": self._config.run_id,
                "resource_identity": self._config.resource_identity,
                "agent_version": __version__,
                "enrollment_token": enrollment_token,
                "agent_token": agent_token,
            }
        )
        if (
            response.get("protocol_version") != PROTOCOL_VERSION
            or response.get("status") != "enrolled"
        ):
            raise ValueError("Control Plane rejected enrollment")
        self._config = self._config.without_enrollment_token()
        save_config(self._config_path, self._config)

    def _agent_token(self) -> str:
        try:
            token = self._token_path.read_text().strip()
        except FileNotFoundError:
            token = secrets.token_urlsafe(32)
            self._token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                self._token_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w") as stream:
                stream.write(token)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        if len(token) < 32:
            raise ValueError("agent credential is invalid")
        return token

    def _handle(self, command: dict[str, Any]) -> None:
        required = {
            "command_id",
            "run_id",
            "operation_id",
            "step",
            "kind",
            "payload_version",
            "payload",
            "deadline",
        }
        if set(command) != required:
            raise ValueError("command fields do not match protocol v1")
        for field in ("command_id", "run_id", "operation_id", "step", "kind", "deadline"):
            if not isinstance(command[field], str) or not command[field].strip():
                raise ValueError(f"command {field} is invalid")
        if command["run_id"] != self._config.run_id:
            raise ValueError("command run identity does not match")
        if command["payload_version"] != 1 or command["payload"] != {}:
            raise ValueError("command payload is not supported")
        deadline = datetime.fromisoformat(cast(str, command["deadline"]))
        if deadline.tzinfo is None or deadline.utcoffset() is None or deadline <= datetime.now(UTC):
            raise ValueError("command deadline has passed or is invalid")

        digest = hashlib.sha256(
            json.dumps(command, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        command_id = cast(str, command["command_id"])
        saved = self._journal.receive(command_id, digest)
        if saved is not None:
            return
        try:
            observation = self._execute(cast(str, command["kind"]))
            result: dict[str, Any] = {
                "command_id": command_id,
                "state": "succeeded",
                "error_code": None,
                "message": None,
                "observation": observation,
            }
        except HostActionError as error:
            result = {
                "command_id": command_id,
                "state": "failed",
                "error_code": error.code,
                "message": str(error)[:500],
                "observation": self._runtime.inspect(),
            }
        self._journal.complete(command_id, result)

    def _execute(self, kind: str) -> dict[str, object]:
        actions = {
            "inspect_host": self._runtime.inspect,
            "apply_fixture": self._runtime.apply_fixture,
            "start_fixture": self._runtime.start_fixture,
            "observe_fixture": self._runtime.observe_fixture,
            "stop_fixture": self._runtime.stop_fixture,
        }
        try:
            action = actions[kind]
        except KeyError as error:
            raise ValueError("unknown command kind") from error
        return action()
