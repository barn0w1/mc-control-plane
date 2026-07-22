"""State values owned by the control plane domain."""

from enum import StrEnum


class DesiredState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class OperationKind(StrEnum):
    START = "start"
    STOP = "stop"
    SNAPSHOT = "snapshot"
    MAINTENANCE = "maintenance"


class OperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {OperationState.SUCCEEDED, OperationState.CANCELLED}


class StartStep(StrEnum):
    DISCOVER_RUNTIME = "discover_runtime"
    CREATE_RUNTIME = "create_runtime"
    WAIT_PROVIDER = "wait_provider"
    WAIT_HOST = "wait_host"
    RESTORE_SNAPSHOT = "restore_snapshot"
    START_WORKLOAD = "start_workload"
    WAIT_WORKLOAD = "wait_workload"
    COMPLETE = "complete"


class SnapshotKind(StrEnum):
    STOP = "stop"
    PERIODIC = "periodic"
    MANUAL = "manual"
