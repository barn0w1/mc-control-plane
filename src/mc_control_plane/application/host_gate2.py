"""Credential-free coordinator for the Gate 2 Host acceptance sequence."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from time import sleep
from uuid import uuid4

from mc_control_plane.adapters.outbound.persistence import HostProtocolStore
from mc_control_plane.application.host_protocol import (
    HostAgentObservation,
    HostCommandKind,
    HostCommandState,
)


class HostGate2Error(Exception):
    pass


def run_host_gate2_sequence(
    store: HostProtocolStore,
    *,
    agent_id: str,
    now: Callable[[], datetime],
    timeout_seconds: float = 600,
    poll_seconds: float = 2,
    sleeper: Callable[[float], None] = sleep,
    progress: Callable[[str], None] | None = None,
) -> None:
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Gate 2 timeout and poll interval must be positive")
    report = progress or (lambda _message: None)
    attempts = max(1, int(timeout_seconds / poll_seconds))
    agent = _wait_for_agent(store, agent_id, attempts, poll_seconds, sleeper, report)
    _validate_capabilities(agent.capabilities or {}, agent.service_states or {})

    sequence = (
        HostCommandKind.INSPECT_HOST,
        HostCommandKind.APPLY_FIXTURE,
        HostCommandKind.START_FIXTURE,
        HostCommandKind.OBSERVE_FIXTURE,
        HostCommandKind.STOP_FIXTURE,
        HostCommandKind.APPLY_FIXTURE,
    )
    for kind in sequence:
        command_id = f"gate2-{uuid4().hex}"
        store.queue_command(
            command_id=command_id,
            agent_id=agent_id,
            operation_id="gate2-host-foundation",
            step=kind.value,
            kind=kind,
            deadline=now() + timedelta(seconds=timeout_seconds),
            now=now(),
        )
        report(f"queued {kind.value}: command={command_id}")
        _wait_for_command(
            store,
            command_id,
            attempts,
            poll_seconds,
            sleeper,
            report,
        )

    final = store.get_agent(agent_id)
    if final is None or final.service_states is None:
        raise HostGate2Error("final Host observation is missing")
    if final.service_states.get("fixture") not in ("inactive", "not-found"):
        raise HostGate2Error("fixture was not confirmed stopped")
    report("fixture apply/start/observe/stop/reapply sequence passed")


def _wait_for_agent(
    store: HostProtocolStore,
    agent_id: str,
    attempts: int,
    poll_seconds: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> HostAgentObservation:
    for attempt in range(attempts):
        agent = store.get_agent(agent_id)
        status = "absent" if agent is None else agent.status
        report(f"agent poll {attempt + 1}/{attempts}: status={status}")
        if agent is not None and agent.status == "connected" and agent.observed_at is not None:
            return agent
        if attempt + 1 < attempts:
            sleeper(poll_seconds)
    raise HostGate2Error("Host agent did not connect before timeout")


def _wait_for_command(
    store: HostProtocolStore,
    command_id: str,
    attempts: int,
    poll_seconds: float,
    sleeper: Callable[[float], None],
    report: Callable[[str], None],
) -> None:
    for attempt in range(attempts):
        command = store.get_command(command_id)
        state = "absent" if command is None else command.state.value
        report(f"command poll {attempt + 1}/{attempts}: command={command_id} state={state}")
        if command is not None and command.state is HostCommandState.SUCCEEDED:
            report(f"command result: command={command_id} {_result_summary(command.result)}")
            return
        if command is not None and command.state is HostCommandState.FAILED:
            raise HostGate2Error(
                f"Host command failed: {command.kind.value}; {_result_summary(command.result)}"
            )
        if attempt + 1 < attempts:
            sleeper(poll_seconds)
    raise HostGate2Error(f"Host command did not finish before timeout: {command_id}")


def _result_summary(result: dict[str, object] | None) -> str:
    if result is None:
        return "result=missing"
    encoded = json.dumps(result, separators=(",", ":"), sort_keys=True)
    return f"result={encoded[:2000]}"


def _validate_capabilities(capabilities: dict[str, object], states: dict[str, object]) -> None:
    if capabilities.get("os_id") != "debian" or str(capabilities.get("os_version")) != "13":
        raise HostGate2Error("Host did not report Debian 13")
    if "3.13" not in str(capabilities.get("python")):
        raise HostGate2Error("Host did not report Python 3.13")
    if "5.4" not in str(capabilities.get("podman")):
        raise HostGate2Error("Host did not report compatible Podman 5.4")
    if "0.18" not in str(capabilities.get("restic")):
        raise HostGate2Error("Host did not report compatible restic 0.18")
    if capabilities.get("quadlet") is not True:
        raise HostGate2Error("Host did not report Quadlet support")
    if states.get("agent") != "active":
        raise HostGate2Error("Host agent systemd service was not active")
