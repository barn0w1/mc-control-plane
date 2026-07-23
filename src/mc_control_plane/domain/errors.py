"""Domain and application errors with stable machine-readable codes."""


class ControlPlaneError(Exception):
    code = "control_plane_error"


class InvalidModel(ControlPlaneError, ValueError):
    code = "invalid_model"


class ServerUnitNotFound(ControlPlaneError):
    code = "server_unit_not_found"


class ActiveRunExists(ControlPlaneError):
    code = "active_run_exists"


class OperationConflict(ControlPlaneError):
    code = "operation_conflict"

    def __init__(
        self,
        server_unit_id: str,
        *,
        operation_id: str | None = None,
        kind: str | None = None,
        state: str | None = None,
        step: str | None = None,
    ) -> None:
        details = [f"Server Unit {server_unit_id} already has an active Operation"]
        if operation_id is not None:
            details.append(
                f"id={operation_id} kind={kind or 'unknown'} "
                f"state={state or 'unknown'} step={step or 'unknown'}"
            )
        super().__init__("; ".join(details))
        self.server_unit_id = server_unit_id
        self.operation_id = operation_id
        self.kind = kind
        self.state = state
        self.step = step


class ActiveRunNotFound(ControlPlaneError):
    code = "active_run_not_found"


class MinecraftSpecNotConfigured(ControlPlaneError):
    code = "minecraft_spec_not_configured"


class SnapshotOwnershipMismatch(ControlPlaneError):
    code = "snapshot_ownership_mismatch"


class OperationNotFound(ControlPlaneError):
    code = "operation_not_found"


class OperationNotBlocked(ControlPlaneError):
    code = "operation_not_blocked"


class RunNotFound(ControlPlaneError):
    code = "run_not_found"


class RuntimeInstanceNotFound(ControlPlaneError):
    code = "runtime_instance_not_found"


class ResourceOwnershipMismatch(ControlPlaneError):
    code = "resource_ownership_mismatch"


class PersistenceConflict(ControlPlaneError):
    code = "persistence_conflict"
