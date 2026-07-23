"""Single-writer scheduler for durable one-step Operations."""

from dataclasses import dataclass, replace
from datetime import timedelta

from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.application.workflows.common import ReconcileResult
from mc_control_plane.application.workflows.snapshot import SnapshotWorkflow
from mc_control_plane.application.workflows.start import StartWorkflow
from mc_control_plane.application.workflows.stop import StopWorkflow
from mc_control_plane.domain.models import Operation
from mc_control_plane.domain.states import OperationKind, OperationState


@dataclass(frozen=True, slots=True)
class ReconcileFailure:
    operation_id: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class ReconcileCycle:
    due_count: int
    results: tuple[ReconcileResult, ...]
    failures: tuple[ReconcileFailure, ...]


class OperationReconciler:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        start_workflow: StartWorkflow,
        clock: Clock,
        snapshot_workflow: SnapshotWorkflow | None = None,
        stop_workflow: StopWorkflow | None = None,
        unexpected_retry_delay: timedelta = timedelta(seconds=10),
    ) -> None:
        self._unit_of_work = unit_of_work
        self._start_workflow = start_workflow
        self._clock = clock
        self._snapshot_workflow = snapshot_workflow
        self._stop_workflow = stop_workflow
        self._unexpected_retry_delay = unexpected_retry_delay

    def run_once(self, limit: int = 32) -> ReconcileCycle:
        with self._unit_of_work() as work:
            due = work.operations.list_due(self._clock.now(), limit)
        results: list[ReconcileResult] = []
        failures: list[ReconcileFailure] = []
        for operation in due:
            workflow = self._workflow_for(operation.kind)
            if workflow is None:
                self._block_unsupported(operation)
                failures.append(
                    ReconcileFailure(operation.id, "unsupported_operation", operation.kind.value)
                )
                continue
            try:
                results.append(workflow.reconcile(operation.id))
            except Exception as error:
                self._defer_unexpected(operation, error)
                failures.append(
                    ReconcileFailure(operation.id, type(error).__name__, str(error)[:500])
                )
        return ReconcileCycle(len(due), tuple(results), tuple(failures))

    def _workflow_for(
        self, kind: OperationKind
    ) -> StartWorkflow | SnapshotWorkflow | StopWorkflow | None:
        if kind is OperationKind.START:
            return self._start_workflow
        if kind is OperationKind.SNAPSHOT:
            return self._snapshot_workflow
        if kind is OperationKind.STOP:
            return self._stop_workflow
        return None

    def _defer_unexpected(self, operation: Operation, error: Exception) -> None:
        now = self._clock.now()
        with self._unit_of_work() as work:
            latest = work.operations.get(operation.id)
            if latest is None or latest.state.is_terminal:
                return
            work.operations.save(
                replace(
                    latest,
                    state=OperationState.RETRY_WAIT,
                    next_attempt_at=now + self._unexpected_retry_delay,
                    last_error_code="reconcile_unexpected_error",
                    last_error_message=f"{type(error).__name__}: {error}"[:500],
                    updated_at=now,
                )
            )
            work.commit()

    def _block_unsupported(self, operation: Operation) -> None:
        blocked = replace(
            operation,
            state=OperationState.BLOCKED,
            next_attempt_at=None,
            last_error_code="unsupported_operation",
            last_error_message=operation.kind.value,
            updated_at=self._clock.now(),
        )
        self._save(blocked)

    def _save(self, operation: Operation) -> None:
        with self._unit_of_work() as work:
            work.operations.save(operation)
            work.commit()
