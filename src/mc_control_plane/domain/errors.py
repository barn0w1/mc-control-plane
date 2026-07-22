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


class OperationNotFound(ControlPlaneError):
    code = "operation_not_found"


class RunNotFound(ControlPlaneError):
    code = "run_not_found"


class RuntimeInstanceNotFound(ControlPlaneError):
    code = "runtime_instance_not_found"


class ResourceOwnershipMismatch(ControlPlaneError):
    code = "resource_ownership_mismatch"


class PersistenceConflict(ControlPlaneError):
    code = "persistence_conflict"
