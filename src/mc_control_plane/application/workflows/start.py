"""Advance the start workflow by one externally observable action."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from mc_control_plane.application.host_protocol import (
    HOST_AGENT_VERSION,
    HOST_PROTOCOL_VERSION,
)
from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeOwnershipMismatch,
    ComputeProvider,
    ComputeProviderUnavailable,
    ComputeRequestRejected,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
    RuntimeObservation,
)
from mc_control_plane.application.ports.host import (
    HostBootstrapError,
    HostBootstrapProvider,
    HostObservation,
    HostObservationProvider,
)
from mc_control_plane.application.ports.persistence import UnitOfWorkFactory
from mc_control_plane.application.ports.support import Clock
from mc_control_plane.domain.errors import (
    OperationNotFound,
    ResourceOwnershipMismatch,
    RunNotFound,
    RuntimeInstanceNotFound,
)
from mc_control_plane.domain.models import (
    Operation,
    ResourceIdentity,
    Run,
    RuntimeInstance,
)
from mc_control_plane.domain.states import OperationKind, OperationState, StartStep


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    operation_id: str
    state: OperationState
    step: StartStep
    changed: bool


class StartWorkflow:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        compute: ComputeProvider,
        clock: Clock,
        system_id: str,
        host_bootstrap: HostBootstrapProvider,
        host_observations: HostObservationProvider,
        retry_delay: timedelta = timedelta(seconds=5),
        host_freshness: timedelta = timedelta(seconds=30),
    ) -> None:
        self._unit_of_work = unit_of_work
        self._compute = compute
        self._clock = clock
        self._system_id = system_id
        self._host_bootstrap = host_bootstrap
        self._host_observations = host_observations
        self._retry_delay = retry_delay
        self._host_freshness = host_freshness

    def reconcile(self, operation_id: str) -> ReconcileResult:
        operation, run = self._load(operation_id)
        step = StartStep(operation.step)
        now = self._clock.now()

        if operation.kind is not OperationKind.START:
            return self._block(operation, "wrong_operation_kind", str(operation.kind))
        if operation.state.is_terminal or operation.state is OperationState.BLOCKED:
            return self._result(operation, changed=False)
        if operation.next_attempt_at is not None and operation.next_attempt_at > now:
            return self._result(operation, changed=False)

        try:
            if step is StartStep.DISCOVER_RUNTIME:
                return self._discover(operation, run)
            if step is StartStep.CREATE_RUNTIME:
                return self._create(operation, run)
            if step is StartStep.WAIT_PROVIDER:
                return self._wait_provider(operation, run)
            if step is StartStep.WAIT_HOST:
                return self._wait_host(operation, run)
        except ComputeProviderUnavailable as error:
            latest, _ = self._load(operation_id)
            return self._retry(latest, "compute_provider_unavailable", str(error))
        except ComputeRequestRejected as error:
            latest, _ = self._load(operation_id)
            return self._block(latest, "compute_request_rejected", str(error))
        except ComputeResourceNotFound as error:
            latest, _ = self._load(operation_id)
            return self._block(latest, "compute_resource_not_found", str(error))
        except HostBootstrapError as error:
            latest, _ = self._load(operation_id)
            return self._block(latest, "host_bootstrap_failed", str(error))

        return self._result(operation, changed=False)

    def _load(self, operation_id: str) -> tuple[Operation, Run]:
        with self._unit_of_work() as work:
            operation = work.operations.get(operation_id)
            if operation is None:
                raise OperationNotFound(operation_id)
            if operation.run_id is None:
                raise RunNotFound(f"operation {operation_id} has no run")
            run = work.runs.get(operation.run_id)
            if run is None:
                raise RunNotFound(operation.run_id)
            return operation, run

    def _discover(self, operation: Operation, run: Run) -> ReconcileResult:
        observations = self._compute.find_by_server_unit(self._system_id, run.server_unit_id)
        if observations:
            return self._handle_discovered(operation, run, observations)

        updated = replace(
            operation,
            state=OperationState.RUNNING,
            step=StartStep.CREATE_RUNTIME,
            next_attempt_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=self._clock.now(),
        )
        self._save_operation(updated)
        return self._result(updated, changed=True)

    def _create(self, operation: Operation, run: Run) -> ReconcileResult:
        observations = self._compute.find_by_server_unit(self._system_id, run.server_unit_id)
        if observations:
            return self._handle_discovered(operation, run, observations)

        attempted = replace(
            operation,
            state=OperationState.RUNNING,
            attempt_count=operation.attempt_count + 1,
            next_attempt_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=self._clock.now(),
        )
        self._save_operation(attempted)
        identity = self._identity(run)
        metadata = self._host_bootstrap.metadata_for(run, identity, self._clock.now())

        try:
            observation = self._compute.create_runtime(
                RuntimeCreateRequest(
                    identity=identity,
                    spec=run.runtime_spec,
                    metadata_user_data=metadata,
                )
            )
        except ComputeActionUncertain as error:
            retry = replace(
                attempted,
                state=OperationState.RETRY_WAIT,
                step=StartStep.DISCOVER_RUNTIME,
                next_attempt_at=self._clock.now() + self._retry_delay,
                last_error_code="compute_action_uncertain",
                last_error_message=str(error)[:500],
                updated_at=self._clock.now(),
            )
            self._save_operation(retry)
            return self._result(retry, changed=True)

        return self._record_runtime(attempted, run, observation)

    def _wait_host(self, operation: Operation, run: Run) -> ReconcileResult:
        observation = self._host_observations.get_for_run(run.id)
        if observation is None or observation.observed_at is None:
            return self._retry(operation, "host_not_observed", "waiting for Host enrollment")
        if observation.protocol_version != HOST_PROTOCOL_VERSION:
            return self._block(
                operation,
                "host_protocol_incompatible",
                str(observation.protocol_version),
            )
        if observation.agent_version != HOST_AGENT_VERSION:
            return self._block(
                operation,
                "host_agent_incompatible",
                observation.agent_version,
            )
        if observation.status != "connected":
            if observation.status == "enrolled":
                return self._retry(operation, "host_not_connected", observation.status)
            return self._block(operation, "host_status_invalid", observation.status)
        age = self._clock.now() - observation.observed_at
        if age < timedelta(0):
            return self._block(operation, "host_clock_invalid", str(observation.observed_at))
        if age > self._host_freshness:
            return self._retry(operation, "host_observation_stale", str(age))
        readiness_error = _host_foundation_error(observation)
        if readiness_error is not None:
            return self._block(operation, "host_capability_invalid", readiness_error)

        completed = replace(
            operation,
            state=OperationState.SUCCEEDED,
            step=StartStep.COMPLETE,
            next_attempt_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=self._clock.now(),
        )
        self._save_operation(completed)
        return self._result(completed, changed=True)

    def _wait_provider(self, operation: Operation, run: Run) -> ReconcileResult:
        with self._unit_of_work() as work:
            runtime = work.runtime_instances.get_active_for_run(run.id)
        if runtime is None:
            raise RuntimeInstanceNotFound(run.id)

        observation = self._compute.observe_runtime(runtime.provider_resource_id)
        identity = self._identity(run)
        if not identity.owns(observation.tags):
            return self._block(
                operation,
                ResourceOwnershipMismatch.code,
                observation.provider_resource_id,
            )

        now = self._clock.now()
        updated_runtime = replace(
            runtime,
            provider_status=observation.raw_status,
            observed_at=now,
            tags=observation.tags,
        )
        if observation.lifecycle is ComputeLifecycle.RUNNING:
            updated_operation = replace(
                operation,
                state=OperationState.RUNNING,
                step=StartStep.WAIT_HOST,
                next_attempt_at=None,
                last_error_code=None,
                last_error_message=None,
                updated_at=now,
            )
        elif observation.lifecycle is ComputeLifecycle.PENDING:
            updated_operation = replace(
                operation,
                state=OperationState.RETRY_WAIT,
                next_attempt_at=now + self._retry_delay,
                updated_at=now,
            )
        else:
            with self._unit_of_work() as work:
                work.runtime_instances.save(updated_runtime)
                work.commit()
            return self._block(
                operation,
                "compute_not_startable",
                observation.raw_status,
            )

        with self._unit_of_work() as work:
            work.runtime_instances.save(updated_runtime)
            work.operations.save(updated_operation)
            work.commit()
        return self._result(updated_operation, changed=True)

    def _handle_discovered(
        self,
        operation: Operation,
        run: Run,
        observations: Sequence[RuntimeObservation],
    ) -> ReconcileResult:
        if len(observations) != 1:
            return self._block(
                operation,
                "ambiguous_runtime",
                f"found {len(observations)} resources",
            )
        return self._record_runtime(operation, run, observations[0])

    def _record_runtime(
        self,
        operation: Operation,
        run: Run,
        observation: RuntimeObservation,
    ) -> ReconcileResult:
        identity = self._identity(run)
        if not identity.owns(observation.tags):
            return self._block(
                operation,
                ResourceOwnershipMismatch.code,
                observation.provider_resource_id,
            )

        now = self._clock.now()
        runtime = RuntimeInstance(
            provider_resource_id=observation.provider_resource_id,
            run_id=run.id,
            server_unit_id=run.server_unit_id,
            provider=observation.provider,
            region=observation.region,
            tags=observation.tags,
            provider_status=observation.raw_status,
            observed_at=now,
            created_at=now,
        )
        updated = replace(
            operation,
            state=OperationState.RUNNING,
            step=StartStep.WAIT_PROVIDER,
            next_attempt_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=now,
        )

        conflict_resource_id: str | None = None
        with self._unit_of_work() as work:
            existing = work.runtime_instances.get_active_for_run(run.id)
            if existing is None:
                work.runtime_instances.add(runtime)
            elif existing.provider_resource_id != runtime.provider_resource_id:
                conflict_resource_id = runtime.provider_resource_id
            else:
                work.runtime_instances.save(
                    replace(
                        existing,
                        provider_status=runtime.provider_status,
                        observed_at=runtime.observed_at,
                        tags=runtime.tags,
                    )
                )
            if conflict_resource_id is None:
                work.operations.save(updated)
                work.commit()
        if conflict_resource_id is not None:
            return self._block(
                operation,
                "runtime_instance_conflict",
                conflict_resource_id,
            )
        return self._result(updated, changed=True)

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

    def _save_operation(self, operation: Operation) -> None:
        with self._unit_of_work() as work:
            work.operations.save(operation)
            work.commit()

    def _identity(self, run: Run) -> ResourceIdentity:
        return ResourceIdentity(
            system_id=self._system_id,
            server_unit_id=run.server_unit_id,
            run_id=run.id,
        )

    @staticmethod
    def _result(operation: Operation, *, changed: bool) -> ReconcileResult:
        return ReconcileResult(
            operation_id=operation.id,
            state=operation.state,
            step=StartStep(operation.step),
            changed=changed,
        )


def delete_owned_runtime(
    compute: ComputeProvider,
    identity: ResourceIdentity,
    provider_resource_id: str,
) -> None:
    try:
        compute.delete_runtime(provider_resource_id, identity)
    except ComputeOwnershipMismatch as error:
        raise ResourceOwnershipMismatch(provider_resource_id) from error


def _host_foundation_error(observation: HostObservation) -> str | None:
    capabilities = observation.capabilities or {}
    states = observation.service_states or {}
    if capabilities.get("os_id") != "debian" or str(capabilities.get("os_version")) != "13":
        return "expected Debian 13"
    if re.match(r"^Python 3\.13(?:\.|\s|$)", str(capabilities.get("python"))) is None:
        return "expected Python 3.13"
    if re.match(r"^podman version 5\.4(?:\.|\s|$)", str(capabilities.get("podman"))) is None:
        return "expected Podman 5.4"
    if re.match(r"^restic 0\.18(?:\.|\s|$)", str(capabilities.get("restic"))) is None:
        return "expected restic 0.18"
    if capabilities.get("quadlet") is not True:
        return "Quadlet generator unavailable"
    if states.get("agent") != "active":
        return "Host agent service is not active"
    return None
