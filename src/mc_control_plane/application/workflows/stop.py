"""Durable graceful-stop, snapshot-before-delete, and runtime cleanup workflow."""

from dataclasses import replace

from mc_control_plane.application.host_protocol import (
    HostCommand,
    HostCommandKind,
    HostCommandState,
)
from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeOwnershipMismatch,
    ComputeProvider,
    ComputeProviderUnavailable,
    ComputeRequestRejected,
    ComputeResourceNotFound,
)
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
from mc_control_plane.domain.models import Operation, ResourceIdentity, Run, Snapshot
from mc_control_plane.domain.states import (
    DesiredState,
    OperationKind,
    OperationState,
    SnapshotKind,
    StopStep,
)


class StopWorkflow(WorkflowSupport):
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        host_commands: HostCommandGateway,
        compute: ComputeProvider,
        clock: Clock,
        *,
        system_id: str,
    ) -> None:
        super().__init__(unit_of_work, clock)
        self._host_commands = host_commands
        self._compute = compute
        self._system_id = system_id

    def reconcile(self, operation_id: str) -> ReconcileResult:
        operation, run = self._load(operation_id)
        if operation.kind is not OperationKind.STOP:
            return self._block(operation, "wrong_operation_kind", operation.kind.value)
        if operation.state.is_terminal or operation.state is OperationState.BLOCKED:
            return self._result(operation, changed=False)
        if operation.next_attempt_at is not None and operation.next_attempt_at > self._clock.now():
            return self._result(operation, changed=False)
        step = StopStep(operation.step)
        if step is StopStep.STOP_WORKLOAD:
            return self._queue_stop(operation, run)
        if step is StopStep.WAIT_WORKLOAD:
            return self._wait_stop(operation, run)
        if step is StopStep.CREATE_SNAPSHOT:
            return self._queue_snapshot(operation, run)
        if step is StopStep.WAIT_SNAPSHOT:
            return self._wait_snapshot(operation, run)
        if step is StopStep.DELETE_RUNTIME:
            return self._delete_runtime(operation, run)
        if step is StopStep.WAIT_RUNTIME_ABSENT:
            return self._wait_runtime_absent(operation, run)
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

    def _queue_stop(self, operation: Operation, run: Run) -> ReconcileResult:
        command = ensure_host_command(
            self._host_commands,
            self._clock,
            operation,
            run,
            action_step=StopStep.STOP_WORKLOAD.value,
            kind=HostCommandKind.STOP_MINECRAFT,
            payload={"server_unit_id": run.server_unit_id},
        )
        if command is None:
            return self._retry(operation, "host_not_connected", "waiting for Host agent")
        return self._advance(operation, StopStep.WAIT_WORKLOAD.value)

    def _wait_stop(self, operation: Operation, run: Run) -> ReconcileResult:
        command = self._host_commands.get_command(
            command_id(operation, StopStep.STOP_WORKLOAD.value)
        )
        terminal = self._command_observation(operation, command)
        if isinstance(terminal, ReconcileResult):
            return terminal
        if terminal.get("minecraft") != "stopped":
            return self._block(operation, "minecraft_not_stopped", str(terminal.get("minecraft")))
        return self._advance(operation, StopStep.CREATE_SNAPSHOT.value)

    def _queue_snapshot(self, operation: Operation, run: Run) -> ReconcileResult:
        command = ensure_host_command(
            self._host_commands,
            self._clock,
            operation,
            run,
            action_step=StopStep.CREATE_SNAPSHOT.value,
            kind=HostCommandKind.SNAPSHOT_DATA,
            payload={"server_unit_id": run.server_unit_id},
        )
        if command is None:
            return self._retry(operation, "host_not_connected", "waiting for Host agent")
        return self._advance(operation, StopStep.WAIT_SNAPSHOT.value)

    def _wait_snapshot(self, operation: Operation, run: Run) -> ReconcileResult:
        command = self._host_commands.get_command(
            command_id(operation, StopStep.CREATE_SNAPSHOT.value)
        )
        terminal = self._command_observation(operation, command)
        if isinstance(terminal, ReconcileResult):
            return terminal
        snapshot_id = terminal.get("snapshot_id")
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
                        SnapshotKind.STOP,
                        now,
                    )
                )
            latest = work.operations.get(operation.id)
            if latest is None:
                raise OperationNotFound(operation.id)
            advanced = replace(
                latest,
                state=OperationState.RUNNING,
                step=StopStep.DELETE_RUNTIME,
                next_attempt_at=None,
                last_error_code=None,
                last_error_message=None,
                updated_at=now,
            )
            work.operations.save(advanced)
            work.commit()
        return self._result(advanced, changed=True)

    def _delete_runtime(self, operation: Operation, run: Run) -> ReconcileResult:
        with self._unit_of_work() as work:
            runtime = work.runtime_instances.get_active_for_run(run.id)
        if runtime is None:
            return self._finish(operation, run)
        identity = self._identity(run)
        if not identity.owns(runtime.tags):
            return self._block(
                operation, "resource_ownership_mismatch", runtime.provider_resource_id
            )
        try:
            self._compute.delete_runtime(runtime.provider_resource_id, identity)
        except ComputeResourceNotFound:
            return self._finish(operation, run)
        except ComputeActionUncertain:
            pass
        except ComputeProviderUnavailable as error:
            return self._retry(operation, "compute_provider_unavailable", str(error))
        except ComputeRequestRejected as error:
            return self._block(operation, "compute_request_rejected", str(error))
        except ComputeOwnershipMismatch as error:
            return self._block(operation, "resource_ownership_mismatch", str(error))
        return self._advance(operation, StopStep.WAIT_RUNTIME_ABSENT.value)

    def _wait_runtime_absent(self, operation: Operation, run: Run) -> ReconcileResult:
        with self._unit_of_work() as work:
            runtime = work.runtime_instances.get_active_for_run(run.id)
        if runtime is None:
            return self._finish(operation, run)
        try:
            observed = self._compute.observe_runtime(runtime.provider_resource_id)
        except ComputeResourceNotFound:
            return self._finish(operation, run)
        except ComputeProviderUnavailable as error:
            return self._retry(operation, "compute_provider_unavailable", str(error))
        if not self._identity(run).owns(observed.tags):
            return self._block(
                operation, "resource_ownership_mismatch", runtime.provider_resource_id
            )
        return self._retry(operation, "runtime_delete_in_progress", observed.raw_status)

    def _finish(self, operation: Operation, run: Run) -> ReconcileResult:
        now = self._clock.now()
        with self._unit_of_work() as work:
            latest = work.operations.get(operation.id)
            current_run = work.runs.get(run.id)
            unit = work.server_units.get(run.server_unit_id)
            runtime = work.runtime_instances.get_active_for_run(run.id)
            if latest is None:
                raise OperationNotFound(operation.id)
            if current_run is None:
                raise RunNotFound(run.id)
            if runtime is not None:
                work.runtime_instances.save(
                    replace(
                        runtime,
                        provider_status="deleted",
                        observed_at=now,
                        deleted_at=now,
                    )
                )
            work.runs.save(replace(current_run, ended_at=now))
            if unit is not None:
                work.server_units.save(
                    replace(unit, desired_state=DesiredState.STOPPED, updated_at=now)
                )
            completed = replace(
                latest,
                state=OperationState.SUCCEEDED,
                step=StopStep.COMPLETE,
                next_attempt_at=None,
                last_error_code=None,
                last_error_message=None,
                updated_at=now,
            )
            work.operations.save(completed)
            work.commit()
        return self._result(completed, changed=True)

    def _command_observation(
        self, operation: Operation, command: HostCommand | None
    ) -> dict[str, object] | ReconcileResult:
        if command is None:
            return self._retry(operation, "host_command_missing", str(operation.step))
        if command.state in {HostCommandState.PENDING, HostCommandState.DELIVERED}:
            return self._retry(operation, "host_command_in_progress", command.state.value)
        if command.state is HostCommandState.FAILED:
            return self._block(operation, "host_command_failed", failed_command_message(command))
        observation = successful_observation(command)
        if observation is None:
            return self._block(operation, "host_command_result_invalid", str(operation.step))
        return observation

    def _identity(self, run: Run) -> ResourceIdentity:
        return ResourceIdentity(self._system_id, run.server_unit_id, run.id)
