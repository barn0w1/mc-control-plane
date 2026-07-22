from pathlib import Path
from typing import Any

from mccp_host_agent.agent import HostAgent
from mccp_host_agent.config import AgentConfig, load_config, save_config
from mccp_host_agent.journal import CommandJournal

IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64


class FakeClient:
    def __init__(self, command: dict[str, Any] | None = None) -> None:
        self.command = command
        self.enrollments: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []

    def enroll(self, value: dict[str, Any]) -> dict[str, Any]:
        self.enrollments.append(value)
        return {"protocol_version": 1, "agent_id": "agent-1", "status": "enrolled"}

    def poll(self, agent_token: str, value: dict[str, Any]) -> dict[str, Any]:
        assert len(agent_token) >= 32
        self.polls.append(value)
        command, self.command = self.command, None
        return {"protocol_version": 1, "command": command, "poll_after_seconds": 5}


class FakeRuntime:
    def __init__(self) -> None:
        self.applies = 0
        self.minecraft_applies: list[dict[str, object]] = []
        self.minecraft_snapshots: list[tuple[str, dict[str, Any]]] = []

    def boot_id(self) -> str:
        return "boot-1"

    def capabilities(self) -> dict[str, object]:
        return {"quadlet": True}

    def service_states(self) -> dict[str, str]:
        return {"agent": "active", "fixture": "inactive"}

    def inspect(self) -> dict[str, object]:
        return {"fixture": "inactive"}

    def apply_fixture(self) -> dict[str, object]:
        self.applies += 1
        return {"fixture": "inactive", "revision": "revision"}

    def start_fixture(self) -> dict[str, object]:
        return {"fixture": "active"}

    def observe_fixture(self) -> dict[str, object]:
        return {"fixture": "active"}

    def stop_fixture(self) -> dict[str, object]:
        return {"fixture": "inactive"}

    def apply_minecraft(self, **values: object) -> dict[str, object]:
        self.minecraft_applies.append(values)
        return {"minecraft": "stopped"}

    def start_minecraft(self) -> dict[str, object]:
        return {"minecraft": "ready"}

    def observe_minecraft(self) -> dict[str, object]:
        return {"minecraft": "ready"}

    def stop_minecraft(self) -> dict[str, object]:
        return {"minecraft": "stopped"}

    def snapshot_minecraft_data(self, command_id: str, lease: dict[str, Any]) -> dict[str, object]:
        self.minecraft_snapshots.append((command_id, lease))
        return {"snapshot_id": "a" * 64}


def _command() -> dict[str, Any]:
    return {
        "command_id": "command-1",
        "run_id": "run-1",
        "operation_id": "gate2-check",
        "step": "apply_fixture",
        "kind": "apply_fixture",
        "payload_version": 1,
        "payload": {},
        "deadline": "2099-01-01T00:00:00+00:00",
    }


def test_agent_removes_enrollment_token_and_journals_before_reporting(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = AgentConfig(
        control_plane_url="https://control.example.test",
        agent_id="agent-1",
        run_id="run-1",
        resource_identity="resource-1",
        enrollment_token="enrollment-secret",
        fixture_image=IMAGE,
    )
    save_config(config_path, config)
    client = FakeClient(_command())
    runtime = FakeRuntime()
    journal = CommandJournal(tmp_path / "journal.db")
    agent = HostAgent(
        config,
        config_path=config_path,
        token_path=tmp_path / "agent-token",
        journal=journal,
        client=client,  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )

    assert agent.run_once() == 0
    assert load_config(config_path).enrollment_token is None
    assert runtime.applies == 1
    assert journal.unreported()[0].value["state"] == "succeeded"

    assert agent.run_once() == 5
    assert client.polls[-1]["results"][0]["command_id"] == "command-1"  # type: ignore[index]
    assert journal.unreported() == []


def test_redelivered_completed_command_is_not_executed_again(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config = AgentConfig(
        control_plane_url="https://control.example.test",
        agent_id="agent-1",
        run_id="run-1",
        resource_identity="resource-1",
        enrollment_token=None,
        fixture_image=IMAGE,
    )
    save_config(config_path, config)
    runtime = FakeRuntime()
    client = FakeClient(_command())
    agent = HostAgent(
        config,
        config_path=config_path,
        token_path=tmp_path / "agent-token",
        journal=CommandJournal(tmp_path / "journal.db"),
        client=client,  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )
    agent.run_once()
    client.command = _command()
    agent.run_once()

    assert runtime.applies == 1


def test_agent_dispatches_closed_minecraft_commands(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    agent = HostAgent(
        AgentConfig(
            control_plane_url="https://control.example.test",
            agent_id="agent-1",
            run_id="run-1",
            resource_identity="resource-1",
            enrollment_token=None,
            fixture_image=IMAGE,
        ),
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "agent-token",
        journal=CommandJournal(tmp_path / "journal.db"),
        client=FakeClient(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )
    applied = agent._execute(
        "command-1",
        "apply_minecraft",
        {
            "server_unit_id": "survival",
            "image": "docker.io/itzg/minecraft-server@sha256:" + "b" * 64,
            "minecraft_version": "1.21.8",
            "paper_build": "42",
            "memory": "512M",
            "eula": True,
        },
        None,
    )
    lease = {"permission": "object-read-write"}
    snapshotted = agent._execute(
        "command-2",
        "snapshot_minecraft",
        {"server_unit_id": "survival"},
        lease,
    )

    assert applied == {"minecraft": "stopped"}
    assert runtime.minecraft_applies[0]["paper_build"] == "42"
    assert snapshotted == {"snapshot_id": "a" * 64}
    assert runtime.minecraft_snapshots == [("command-2", lease)]
