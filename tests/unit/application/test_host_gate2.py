from datetime import timedelta

from mc_control_plane.adapters.outbound.persistence import HostProtocolStore, SQLiteDatabase
from mc_control_plane.application.host_gate2 import run_host_gate2_sequence
from tests.fakes import MutableClock


def test_gate2_sequence_waits_for_typed_commands_and_final_stopped_observation(
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    store = HostProtocolStore(database)
    enrollment = store.issue_enrollment(
        run_id="run-1",
        resource_identity="resource-1",
        expires_at=clock.now() + timedelta(minutes=10),
        now=clock.now(),
    )
    token = "agent-secret"
    store.enroll(
        {
            "protocol_version": 1,
            "agent_id": "agent-1",
            "run_id": "run-1",
            "resource_identity": "resource-1",
            "agent_version": "0.1.0",
            "enrollment_token": enrollment.token,
            "agent_token": token,
        },
        now=clock.now(),
    )
    fixture = "inactive"

    def poll(results: list[dict[str, object]] | None = None):
        return store.poll(
            token,
            {
                "protocol_version": 1,
                "agent_id": "agent-1",
                "run_id": "run-1",
                "agent_version": "0.1.0",
                "boot_id": "boot-1",
                "capabilities": {
                    "os_id": "debian",
                    "os_version": "13",
                    "python": "Python 3.13.5",
                    "podman": "podman version 5.4.2",
                    "restic": "restic 0.18.0",
                    "quadlet": True,
                },
                "service_states": {"agent": "active", "fixture": fixture},
                "results": [] if results is None else results,
            },
            now=clock.now(),
        )

    poll()

    def advance(_seconds: float) -> None:
        nonlocal fixture
        command = poll()
        if command is None:
            return
        if command.kind.value == "start_fixture":
            fixture = "active"
        elif command.kind.value == "stop_fixture":
            fixture = "inactive"
        poll(
            [
                {
                    "command_id": command.command_id,
                    "state": "succeeded",
                    "error_code": None,
                    "message": None,
                    "observation": {"fixture": fixture},
                }
            ]
        )

    run_host_gate2_sequence(
        store,
        agent_id="agent-1",
        now=clock.now,
        timeout_seconds=10,
        poll_seconds=1,
        sleeper=advance,
    )

    assert store.get_agent("agent-1").service_states["fixture"] == "inactive"  # type: ignore[union-attr,index]
