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
    INIT_DATA_REPOSITORY = "init_data_repository"
    WAIT_DATA_REPOSITORY = "wait_data_repository"
    RESTORE_SNAPSHOT = "restore_snapshot"
    WAIT_RESTORE = "wait_restore"
    APPLY_WORKLOAD = "apply_workload"
    WAIT_APPLY = "wait_apply"
    START_WORKLOAD = "start_workload"
    WAIT_WORKLOAD = "wait_workload"
    COMPLETE = "complete"


class SnapshotStep(StrEnum):
    CREATE_SNAPSHOT = "create_snapshot"
    WAIT_SNAPSHOT = "wait_snapshot"
    COMPLETE = "complete"


class StopStep(StrEnum):
    STOP_WORKLOAD = "stop_workload"
    WAIT_WORKLOAD = "wait_workload"
    CREATE_SNAPSHOT = "create_snapshot"
    WAIT_SNAPSHOT = "wait_snapshot"
    DELETE_RUNTIME = "delete_runtime"
    WAIT_RUNTIME_ABSENT = "wait_runtime_absent"
    COMPLETE = "complete"


class SnapshotKind(StrEnum):
    STOP = "stop"
    PERIODIC = "periodic"
    MANUAL = "manual"
