"""Durable manual Minecraft snapshot workflow."""

from dataclasses import replace

from mc_control_plane.application.host_protocol import HostCommandKind, HostCommandState
from mc_control_plane.application.ports.host import HostCommandGateway
from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.application.workflows.common import (
    ReconcileResult,
    WorkflowSupport,
    command_id,
    ensure_host_command,
    failed_command_message,
    successful_observation,
)
from mc_control_plane.domain.errors import OperationNotFound, RunNotFound
from mc_control_plane.domain.models import Operation, Run, Snapshot
from mc_control_plane.domain.states import (
    OperationKind,
    OperationState,
    SnapshotKind,
    SnapshotStep,
)


class SnapshotWorkflow(WorkflowSupport):
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        host_commands: HostCommandGateway,
        clock: Clock,
    ) -> None:
        super().__init__(unit_of_work, clock)
        self._host_commands = host_commands

    def reconcile(self, operation_id: str) -> ReconcileResult:
        operation, run = self._load(operation_id)
        if operation.kind is not OperationKind.SNAPSHOT:
            return self._block(operation, "wrong_operation_kind", operation.kind.value)
        if operation.state.is_terminal or operation.state is OperationState.BLOCKED:
            return self._result(operation, changed=False)
        if operation.next_attempt_at is not None and operation.next_attempt_at > self._clock.now():
            return self._result(operation, changed=False)
        step = SnapshotStep(operation.step)
        if step is SnapshotStep.CREATE_SNAPSHOT:
            return self._create(operation, run)
        if step is SnapshotStep.WAIT_SNAPSHOT:
            return self._wait(operation, run)
        return self._result(operation, changed=False)

    def _load(self, operation_id: str) -> tuple[Operation, Run]:
        with self._unit_of_work() as work:
            operation = work.operations.get(operation_id)
            if operation is None:
                raise OperationNotFound(operation_id)
            run = None if operation.run_id is None else work.runs.get(operation.run_id)
            if run is None:
                raise RunNotFound(operation.run_id or operation_id)
            return operation, run

    def _create(self, operation: Operation, run: Run) -> ReconcileResult:
        command = ensure_host_command(
            self._host_commands,
            self._clock,
            operation,
            run,
            action_step=SnapshotStep.CREATE_SNAPSHOT.value,
            kind=HostCommandKind.SNAPSHOT_MINECRAFT,
            payload={"server_unit_id": run.server_unit_id},
        )
        if command is None:
            return self._retry(operation, "host_not_connected", "waiting for Host agent")
        return self._advance(operation, SnapshotStep.WAIT_SNAPSHOT.value)

    def _wait(self, operation: Operation, run: Run) -> ReconcileResult:
        command = self._host_commands.get_command(
            command_id(operation, SnapshotStep.CREATE_SNAPSHOT.value)
        )
        if command is None:
            return self._retry(operation, "host_command_missing", "snapshot_minecraft")
        if command.state in {HostCommandState.PENDING, HostCommandState.DELIVERED}:
            return self._retry(operation, "host_command_in_progress", command.state.value)
        if command.state is HostCommandState.FAILED:
            return self._block(operation, "host_command_failed", failed_command_message(command))
        observation = successful_observation(command)
        snapshot_id = None if observation is None else observation.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return self._block(operation, "snapshot_result_invalid", "snapshot ID was not returned")
        now = self._clock.now()
        with self._unit_of_work() as work:
            existing = work.snapshots.get(snapshot_id)
        if existing is not None and existing.server_unit_id != run.server_unit_id:
            return self._block(operation, "snapshot_ownership_mismatch", snapshot_id)
        with self._unit_of_work() as work:
            existing = work.snapshots.get(snapshot_id)
            if existing is None:
                work.snapshots.add(
                    Snapshot(
                        snapshot_id,
                        run.server_unit_id,
                        run.id,
                        SnapshotKind.MANUAL,
                        now,
                    )
                )
            latest = work.operations.get(operation.id)
            if latest is None:
                raise OperationNotFound(operation.id)
            completed = replace(
                latest,
                state=OperationState.SUCCEEDED,
                step=SnapshotStep.COMPLETE,
                next_attempt_at=None,
                last_error_code=None,
                last_error_message=None,
                updated_at=now,
            )
            work.operations.save(completed)
            work.commit()
        return self._result(completed, changed=True)
