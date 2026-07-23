"""Shared durable-workflow mechanics."""

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from mc_control_plane.application.host_protocol import (
    HostCommand,
    HostCommandKind,
    HostCommandState,
)
from mc_control_plane.application.ports.host import HostCommandGateway
from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.domain.models import Operation, Run
from mc_control_plane.domain.states import OperationState, StartStep


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    operation_id: str
    state: OperationState
    step: StartStep | str
    changed: bool


class WorkflowSupport:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        clock: Clock,
        *,
        retry_delay: timedelta = timedelta(seconds=5),
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._retry_delay = retry_delay

    def _save_operation(self, operation: Operation) -> None:
        with self._unit_of_work() as work:
            work.operations.save(operation)
            work.commit()

    def _advance(self, operation: Operation, step: str) -> ReconcileResult:
        updated = replace(
            operation,
            state=OperationState.RUNNING,
            step=step,
            next_attempt_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=self._clock.now(),
        )
        self._save_operation(updated)
        return self._result(updated, changed=True)

    def _complete(self, operation: Operation, step: str = "complete") -> ReconcileResult:
        completed = replace(
            operation,
            state=OperationState.SUCCEEDED,
            step=step,
            next_attempt_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=self._clock.now(),
        )
        self._save_operation(completed)
        return self._result(completed, changed=True)

    def _block(self, operation: Operation, code: str, message: str) -> ReconcileResult:
        blocked = replace(
            operation,
            state=OperationState.BLOCKED,
            next_attempt_at=None,
            last_error_code=code,
            last_error_message=message[:500],
            updated_at=self._clock.now(),
        )
        self._save_operation(blocked)
        return self._result(blocked, changed=True)

    def _retry(self, operation: Operation, code: str, message: str) -> ReconcileResult:
        now = self._clock.now()
        retry = replace(
            operation,
            state=OperationState.RETRY_WAIT,
            next_attempt_at=now + self._retry_delay,
            last_error_code=code,
            last_error_message=message[:500],
            updated_at=now,
        )
        self._save_operation(retry)
        return self._result(retry, changed=True)

    @staticmethod
    def _result(operation: Operation, *, changed: bool) -> ReconcileResult:
        return ReconcileResult(
            operation_id=operation.id,
            state=operation.state,
            step=str(operation.step),
            changed=changed,
        )


def command_id(operation: Operation, action_step: str) -> str:
    return f"operation-{operation.id}-{action_step}-attempt-{operation.attempt_count}"


def ensure_host_command(
    gateway: HostCommandGateway,
    clock: Clock,
    operation: Operation,
    run: Run,
    *,
    action_step: str,
    kind: HostCommandKind,
    payload: dict[str, Any],
    timeout: timedelta = timedelta(minutes=40),
) -> HostCommand | None:
    durable_id = command_id(operation, action_step)
    existing = gateway.get_command(durable_id)
    if existing is not None:
        return existing
    agent = gateway.get_agent_for_run(run.id)
    if agent is None or agent.status != "connected":
        return None
    now = clock.now()
    return gateway.queue_command(
        command_id=durable_id,
        agent_id=agent.agent_id,
        operation_id=operation.id,
        step=action_step,
        kind=kind,
        payload=payload,
        deadline=now + timeout,
        now=now,
    )


def successful_observation(command: HostCommand) -> dict[str, Any] | None:
    if command.state is not HostCommandState.SUCCEEDED or command.result is None:
        return None
    value = command.result.get("observation")
    return value if isinstance(value, dict) else None


def failed_command_message(command: HostCommand) -> str:
    result = command.result or {}
    code = result.get("error_code") or "host_command_failed"
    message = result.get("message") or "Host command failed"
    return f"{code}: {message}"
