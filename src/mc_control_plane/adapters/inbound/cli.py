"""Minimal CLI entrypoint for local control-plane administration."""

import argparse
import json
import os
import signal
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from mc_control_plane import __version__
from mc_control_plane.adapters.inbound.host_api import HostApiApplication, serve_host_api
from mc_control_plane.adapters.outbound.compute import (
    LinodeComputeProvider,
    LinodeComputeSettings,
)
from mc_control_plane.adapters.outbound.compute.linode_gate1 import (
    DEBIAN_13_IMAGE,
    LinodeGate1CheckError,
    cleanup_linode_gate1_resources,
    run_linode_gate1_check,
)
from mc_control_plane.adapters.outbound.compute.linode_gate2 import (
    LinodeGate2CheckError,
    cleanup_linode_gate2_resources,
    run_linode_gate2_check,
)
from mc_control_plane.adapters.outbound.host import (
    DurableHostManager,
    DurableHostSettings,
    HostBootstrapSpec,
    StoredHostObservations,
    artifact_sha256,
    create_bootstrap_key,
    load_bootstrap_key,
)
from mc_control_plane.adapters.outbound.persistence import (
    HostProtocolStore,
    SQLiteDatabase,
    SQLiteUnitOfWorkFactory,
)
from mc_control_plane.adapters.outbound.storage import (
    CloudflareTemporaryCredentialClient,
    R2ResticLeaseBroker,
    R2ResticSettings,
    load_root_secret,
)
from mc_control_plane.application.commands.server_unit import (
    CreateServerUnit,
    RequestServerUnitCreation,
)
from mc_control_plane.application.commands.start import RequestStart, StartServerUnit
from mc_control_plane.application.gate3_cleanup import cleanup_gate3_runtime
from mc_control_plane.application.gate4 import Gate4Error, run_gate4_data_lifecycle
from mc_control_plane.application.host_protocol import (
    HOST_AGENT_ARTIFACT_PATH,
    HOST_AGENT_VERSION,
)
from mc_control_plane.application.ports.compute import ComputeProviderError
from mc_control_plane.application.ports.persistence import (
    UnitOfWorkFactory as UnitOfWorkFactoryPort,
)
from mc_control_plane.application.queries.status import GetServerUnitStatus
from mc_control_plane.application.reconciler import OperationReconciler
from mc_control_plane.application.support import SystemClock, UuidGenerator
from mc_control_plane.application.workflows.start import StartWorkflow
from mc_control_plane.domain.errors import ControlPlaneError
from mc_control_plane.domain.models import RuntimeSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mc-control-plane")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init_database = commands.add_parser("init-db", help="initialize or migrate the database")
    init_database.add_argument("database", type=Path)

    bootstrap_key = commands.add_parser(
        "host-bootstrap-key-create",
        help="create a root-only Host enrollment derivation key",
    )
    bootstrap_key.add_argument("path", type=Path)

    data_key = commands.add_parser(
        "data-root-key-create",
        help="create a root-only key for per-Server-Unit restic password derivation",
    )
    data_key.add_argument("path", type=Path)

    unit_create = commands.add_parser("server-unit-create", help="register a Server Unit")
    unit_create.add_argument("--database", required=True, type=Path)
    unit_create.add_argument("--id", required=True)
    unit_create.add_argument("--name", required=True)
    _add_runtime_spec_arguments(unit_create)

    unit_start = commands.add_parser("server-unit-start", help="request a durable start")
    unit_start.add_argument("--database", required=True, type=Path)
    unit_start.add_argument("--server-unit-id", required=True)

    unit_status = commands.add_parser("server-unit-status", help="show layered status as JSON")
    unit_status.add_argument("--database", required=True, type=Path)
    unit_status.add_argument("--server-unit-id", required=True)

    reconciler = commands.add_parser(
        "reconciler-run",
        help="run the single-writer durable Operation reconciler",
    )
    reconciler.add_argument("--database", required=True, type=Path)
    reconciler.add_argument("--host-bootstrap-key", required=True, type=Path)
    reconciler.add_argument("--control-plane-url", required=True)
    reconciler.add_argument("--agent-wheel", required=True, type=Path)
    reconciler.add_argument("--fixture-image", required=True)
    reconciler.add_argument("--system-id", default="mc-control-plane")
    reconciler.add_argument("--interval-seconds", type=float, default=5.0)
    reconciler.add_argument("--limit", type=int, default=32)
    reconciler.add_argument("--once", action="store_true")
    _add_ssh_key_argument(reconciler)

    gate3_cleanup = commands.add_parser(
        "linode-gate3-cleanup",
        help="delete the exact active Run used by Gate 3 acceptance",
    )
    gate3_cleanup.add_argument("--database", required=True, type=Path)
    gate3_cleanup.add_argument("--server-unit-id", required=True)
    gate3_cleanup.add_argument("--system-id", default="mc-control-plane")
    gate3_cleanup.add_argument("--timeout-seconds", type=float, default=600.0)
    gate3_cleanup.add_argument("--poll-seconds", type=float, default=5.0)
    gate3_cleanup.add_argument("--confirm-owned-delete", action="store_true")
    _add_ssh_key_argument(gate3_cleanup)

    preflight = commands.add_parser(
        "linode-preflight",
        help="validate Gate 1 Linode configuration without creating a VM",
    )
    _add_linode_arguments(preflight)

    gate1 = commands.add_parser(
        "linode-gate1-check",
        help="run the billable Gate 1 create/observe/delete acceptance check",
    )
    _add_linode_arguments(gate1)
    gate1.add_argument(
        "--confirm-billable-create-delete",
        action="store_true",
        help="confirm that this command may create billable resources and will delete them",
    )
    gate1.add_argument("--timeout-seconds", type=float, default=600.0)
    gate1.add_argument("--poll-seconds", type=float, default=5.0)
    gate1.add_argument("--system-id", default="mc-control-plane")

    cleanup = commands.add_parser(
        "linode-gate1-cleanup",
        help="recover and delete resources matching one complete Gate 1 identity",
    )
    _add_ssh_key_argument(cleanup)
    cleanup.add_argument("--system-id", default="mc-control-plane")
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--timeout-seconds", type=float, default=600.0)
    cleanup.add_argument("--poll-seconds", type=float, default=5.0)
    cleanup.add_argument("--confirm-owned-delete", action="store_true")

    host_api = commands.add_parser(
        "host-api-serve",
        help="serve the authenticated Host agent API and pinned agent artifact",
    )
    host_api.add_argument("--database", required=True, type=Path)
    host_api.add_argument("--bind", default="127.0.0.1")
    host_api.add_argument("--port", default=8443, type=int)
    host_api.add_argument("--tls-certificate", type=Path)
    host_api.add_argument("--tls-private-key", type=Path)
    host_api.add_argument("--agent-wheel", required=True, type=Path)
    host_api.add_argument("--r2-account-id")
    host_api.add_argument("--r2-bucket")
    host_api.add_argument("--r2-parent-access-key-id")
    host_api.add_argument("--cloudflare-api-token-file", type=Path)
    host_api.add_argument("--data-root-key", type=Path)
    host_api.add_argument("--r2-lease-ttl-seconds", type=int, default=900)

    gate2 = commands.add_parser(
        "linode-gate2-check",
        help="run the billable Debian Host/agent/Quadlet acceptance check",
    )
    _add_linode_arguments(gate2)
    gate2.add_argument("--database", required=True, type=Path)
    gate2.add_argument("--control-plane-url", required=True)
    gate2.add_argument("--agent-wheel", required=True, type=Path)
    gate2.add_argument("--fixture-image", required=True)
    gate2.add_argument("--timeout-seconds", type=float, default=900.0)
    gate2.add_argument("--poll-seconds", type=float, default=5.0)
    gate2.add_argument("--system-id", default="mc-control-plane")
    gate2.add_argument("--confirm-billable-create-reboot-delete", action="store_true")

    gate2_cleanup = commands.add_parser(
        "linode-gate2-cleanup",
        help="recover and delete resources matching one complete Gate 2 identity",
    )
    _add_ssh_key_argument(gate2_cleanup)
    gate2_cleanup.add_argument("--system-id", default="mc-control-plane")
    gate2_cleanup.add_argument("--run-id", required=True)
    gate2_cleanup.add_argument("--timeout-seconds", type=float, default=600.0)
    gate2_cleanup.add_argument("--poll-seconds", type=float, default=5.0)
    gate2_cleanup.add_argument("--confirm-owned-delete", action="store_true")

    gate4 = commands.add_parser(
        "linode-gate4-check",
        help="run the three-Host restic/R2 data lifecycle acceptance check",
    )
    _add_linode_arguments(gate4)
    gate4.add_argument("--database", required=True, type=Path)
    gate4.add_argument("--server-unit-id", required=True)
    gate4.add_argument("--host-bootstrap-key", required=True, type=Path)
    gate4.add_argument("--control-plane-url", required=True)
    gate4.add_argument("--agent-wheel", required=True, type=Path)
    gate4.add_argument("--fixture-image", required=True)
    gate4.add_argument("--system-id", default="mc-control-plane")
    gate4.add_argument("--timeout-seconds", type=float, default=1800.0)
    gate4.add_argument("--poll-seconds", type=float, default=5.0)
    gate4.add_argument("--confirm-billable-three-host-check", action="store_true")
    return parser


def _add_linode_arguments(parser: argparse.ArgumentParser) -> None:
    _add_runtime_spec_arguments(parser)
    _add_ssh_key_argument(parser)


def _add_runtime_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-type", required=True)
    parser.add_argument("--firewall-id", required=True)
    parser.add_argument("--image", default=DEBIAN_13_IMAGE)


def _add_ssh_key_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ssh-public-key",
        action="append",
        required=True,
        type=Path,
        help="path to an SSH public key; may be specified more than once",
    )


def _runtime_spec(arguments: argparse.Namespace) -> RuntimeSpec:
    return RuntimeSpec(
        region=arguments.region,
        instance_type=arguments.instance_type,
        image=arguments.image,
        firewall_id=arguments.firewall_id,
    )


def _linode_provider(arguments: argparse.Namespace) -> LinodeComputeProvider:
    token = os.environ.get("LINODE_TOKEN", "")
    if not token.strip():
        raise ValueError("LINODE_TOKEN must be set in the environment")
    keys = tuple(path.read_text().strip() for path in arguments.ssh_public_key)
    return LinodeComputeProvider.from_token(
        token,
        LinodeComputeSettings(authorized_keys=keys),
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "init-db":
        database = SQLiteDatabase(arguments.database)
        database.migrate()
        print(f"initialized {arguments.database}")
        return 0
    if arguments.command in {"host-bootstrap-key-create", "data-root-key-create"}:
        try:
            create_bootstrap_key(arguments.path)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        label = (
            "Host bootstrap" if arguments.command == "host-bootstrap-key-create" else "data root"
        )
        print(f"created {label} key: {arguments.path}")
        return 0
    if arguments.command in {"server-unit-create", "server-unit-start", "server-unit-status"}:
        try:
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            unit_of_work = cast(UnitOfWorkFactoryPort, SQLiteUnitOfWorkFactory(database))
            clock = SystemClock()
            if arguments.command == "server-unit-create":
                unit = RequestServerUnitCreation(unit_of_work, clock)(
                    CreateServerUnit(arguments.id, arguments.name, _runtime_spec(arguments))
                )
                print(f"Server Unit created: id={unit.id} desired-state={unit.desired_state}")
                return 0
            if arguments.command == "server-unit-start":
                accepted = RequestStart(unit_of_work, clock, UuidGenerator())(
                    StartServerUnit(arguments.server_unit_id)
                )
                print(f"Start accepted: operation={accepted.operation_id} run={accepted.run_id}")
                return 0
            observations = StoredHostObservations(HostProtocolStore(database))
            status = GetServerUnitStatus(unit_of_work, observations, clock)(
                arguments.server_unit_id
            )
            print(json.dumps(status.as_dict(), indent=2, sort_keys=True))
            return 0
        except (ControlPlaneError, OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    if arguments.command == "host-api-serve":
        try:
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            print(
                f"Serving Host API on {arguments.bind}:{arguments.port} "
                f"artifact-path={HOST_AGENT_ARTIFACT_PATH}"
            )
            store = HostProtocolStore(database)
            serve_host_api(
                HostApiApplication(store, data_leases=_data_lease_broker(arguments, store)),
                bind=arguments.bind,
                port=arguments.port,
                tls_certificate=arguments.tls_certificate,
                tls_private_key=arguments.tls_private_key,
                agent_artifact=arguments.agent_wheel,
            )
        except KeyboardInterrupt:
            return 0
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0
    if arguments.command == "linode-gate1-check" and not (arguments.confirm_billable_create_delete):
        print(
            "refusing billable check without --confirm-billable-create-delete",
            file=sys.stderr,
        )
        return 2
    if arguments.command == "linode-gate1-cleanup" and not arguments.confirm_owned_delete:
        print(
            "refusing cleanup without --confirm-owned-delete",
            file=sys.stderr,
        )
        return 2
    if arguments.command == "linode-gate2-check" and not (
        arguments.confirm_billable_create_reboot_delete
    ):
        print(
            "refusing billable check without --confirm-billable-create-reboot-delete",
            file=sys.stderr,
        )
        return 2
    if arguments.command == "linode-gate2-cleanup" and not arguments.confirm_owned_delete:
        print(
            "refusing cleanup without --confirm-owned-delete",
            file=sys.stderr,
        )
        return 2
    if arguments.command == "linode-gate3-cleanup" and not arguments.confirm_owned_delete:
        print(
            "refusing cleanup without --confirm-owned-delete",
            file=sys.stderr,
        )
        return 2
    if arguments.command == "linode-gate4-check" and not (
        arguments.confirm_billable_three_host_check
    ):
        print(
            "refusing billable check without --confirm-billable-three-host-check",
            file=sys.stderr,
        )
        return 2
    try:
        if arguments.command == "reconciler-run":
            return _run_reconciler(arguments)
        provider = _linode_provider(arguments)
        if arguments.command == "linode-gate4-check":
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            store = HostProtocolStore(database)
            unit_of_work = cast(UnitOfWorkFactoryPort, SQLiteUnitOfWorkFactory(database))
            clock = SystemClock()
            host = DurableHostManager(
                store,
                DurableHostSettings(
                    control_plane_url=arguments.control_plane_url,
                    agent_wheel=arguments.agent_wheel,
                    fixture_image=arguments.fixture_image,
                ),
                load_bootstrap_key(arguments.host_bootstrap_key),
            )
            workflow = StartWorkflow(
                unit_of_work,
                provider,
                clock,
                system_id=arguments.system_id,
                host_bootstrap=host,
                host_observations=host,
            )
            print(
                "Starting billable Gate 4 check; up to three sequential Linodes will be "
                "created and deleted only after their data is safe."
            )
            gate4_result = run_gate4_data_lifecycle(
                unit_of_work,
                store,
                provider,
                OperationReconciler(unit_of_work, workflow, clock),
                clock,
                UuidGenerator(),
                server_unit_id=arguments.server_unit_id,
                system_id=arguments.system_id,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                progress=lambda message: print(f"Gate 4: {message}"),
            )
            print(
                "Gate 4 check passed: "
                f"initial-snapshot={gate4_result.initial_snapshot_id} "
                f"modified-snapshot={gate4_result.modified_snapshot_id} "
                "fresh-host-restore=passed snapshot-before-delete=passed cleanup=confirmed"
            )
            return 0
        if arguments.command == "linode-gate3-cleanup":
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            cleanup_result = cleanup_gate3_runtime(
                cast(UnitOfWorkFactoryPort, SQLiteUnitOfWorkFactory(database)),
                provider,
                SystemClock(),
                server_unit_id=arguments.server_unit_id,
                system_id=arguments.system_id,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                progress=lambda message: print(f"Gate 3: {message}"),
            )
            resources = ",".join(cleanup_result.deleted_resource_ids) or "none"
            print(
                "Gate 3 cleanup confirmed: "
                f"run={cleanup_result.run_id or 'none'} resources={resources} absent=yes"
            )
            return 0
        if arguments.command == "linode-gate1-cleanup":
            deleted = cleanup_linode_gate1_resources(
                provider,
                system_id=arguments.system_id,
                run_id=arguments.run_id,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                progress=lambda message: print(f"Gate 1: {message}"),
            )
            resources = ",".join(deleted) if deleted else "none"
            print(f"Gate 1 cleanup confirmed: resources={resources} absent=yes")
            return 0
        if arguments.command == "linode-gate2-cleanup":
            deleted = cleanup_linode_gate2_resources(
                provider,
                system_id=arguments.system_id,
                run_id=arguments.run_id,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                progress=lambda message: print(f"Gate 2: {message}"),
            )
            resources = ",".join(deleted) if deleted else "none"
            print(f"Gate 2 cleanup confirmed: resources={resources} absent=yes")
            return 0
        spec = _runtime_spec(arguments)
        if arguments.command == "linode-preflight":
            report = provider.validate_runtime_spec(spec)
            print(
                "Linode preflight passed: "
                f"region={report.region} type={report.instance_type} "
                f"image={report.image} firewall={report.firewall_id} "
                "metadata=yes interfaces=linode disk-encryption=disabled"
            )
            return 0
        if arguments.command == "linode-gate1-check":
            run_id = f"gate1-{uuid4().hex}"
            print(
                "Starting billable Gate 1 check; owned test resources will be deleted. "
                f"recovery-run-id={run_id}"
            )
            result = run_linode_gate1_check(
                provider,
                spec,
                system_id=arguments.system_id,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                run_id_factory=lambda: run_id,
                progress=lambda message: print(f"Gate 1: {message}"),
            )
            print(
                "Gate 1 check passed: "
                f"resource={result.provider_resource_id} status={result.final_provider_status} "
                "metadata=yes firewall=yes backups=disabled "
                "disk-encryption=disabled cleanup=confirmed"
            )
            return 0
        if arguments.command == "linode-gate2-check":
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            store = HostProtocolStore(database)
            now = datetime.now(UTC)
            run_id = f"gate2-{uuid4().hex}"
            agent_id = f"agent-{run_id}"
            issued = store.issue_enrollment(
                run_id=run_id,
                resource_identity=run_id,
                expires_at=now + timedelta(minutes=30),
                now=now,
            )
            wheel = arguments.agent_wheel.read_bytes()
            artifact_url = arguments.control_plane_url.rstrip("/") + HOST_AGENT_ARTIFACT_PATH
            bootstrap = HostBootstrapSpec(
                control_plane_url=arguments.control_plane_url,
                agent_id=agent_id,
                run_id=run_id,
                resource_identity=run_id,
                enrollment_token=issued.token,
                agent_wheel_url=artifact_url,
                agent_wheel_sha256=artifact_sha256(wheel),
                agent_version=HOST_AGENT_VERSION,
                fixture_image=arguments.fixture_image,
            )
            print(
                "Starting billable Gate 2 check; the owned Linode will be rebooted and deleted. "
                f"recovery-run-id={run_id} agent-id={agent_id} "
                f"agent-version={HOST_AGENT_VERSION}"
            )
            gate2_result = run_linode_gate2_check(
                provider,
                store,
                _runtime_spec(arguments),
                bootstrap,
                system_id=arguments.system_id,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                progress=lambda message: print(f"Gate 2: {message}"),
            )
            print(
                "Gate 2 check passed: "
                f"resource={gate2_result.provider_resource_id} agent={gate2_result.agent_id} "
                "enrollment=one-time commands=idempotent quadlet=passed reboot=passed "
                "fixture=stopped cleanup=confirmed"
            )
            return 0
    except (
        ComputeProviderError,
        LinodeGate1CheckError,
        LinodeGate2CheckError,
        Gate4Error,
        ControlPlaneError,
        OSError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {arguments.command}")


def _data_lease_broker(
    arguments: argparse.Namespace, store: HostProtocolStore
) -> R2ResticLeaseBroker | None:
    values = (
        arguments.r2_account_id,
        arguments.r2_bucket,
        arguments.r2_parent_access_key_id,
        arguments.cloudflare_api_token_file,
        arguments.data_root_key,
    )
    if not any(value is not None for value in values):
        return None
    if not all(value is not None for value in values):
        raise ValueError("all R2 data lease options must be provided together")
    token = load_root_secret(arguments.cloudflare_api_token_file).decode().strip()
    settings = R2ResticSettings(
        arguments.r2_account_id,
        arguments.r2_bucket,
        arguments.r2_parent_access_key_id,
        arguments.r2_lease_ttl_seconds,
    )
    client = CloudflareTemporaryCredentialClient(settings.account_id, token)
    return R2ResticLeaseBroker(
        store,
        client,
        settings,
        load_root_secret(arguments.data_root_key),
    )


def _run_reconciler(arguments: argparse.Namespace) -> int:
    if arguments.interval_seconds <= 0 or arguments.limit <= 0:
        raise ValueError("reconciler interval and limit must be positive")
    database = SQLiteDatabase(arguments.database)
    database.migrate()
    store = HostProtocolStore(database)
    unit_of_work = cast(UnitOfWorkFactoryPort, SQLiteUnitOfWorkFactory(database))
    clock = SystemClock()
    host = DurableHostManager(
        store,
        DurableHostSettings(
            control_plane_url=arguments.control_plane_url,
            agent_wheel=arguments.agent_wheel,
            fixture_image=arguments.fixture_image,
        ),
        load_bootstrap_key(arguments.host_bootstrap_key),
    )
    workflow = StartWorkflow(
        unit_of_work,
        _linode_provider(arguments),
        clock,
        system_id=arguments.system_id,
        host_bootstrap=host,
        host_observations=host,
    )
    reconciler = OperationReconciler(unit_of_work, workflow, clock)
    stopping = False

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    try:
        while not stopping:
            cycle = reconciler.run_once(arguments.limit)
            for result in cycle.results:
                print(
                    f"Reconciled: operation={result.operation_id} "
                    f"state={result.state.value} step={result.step.value} changed={result.changed}"
                )
            for failure in cycle.failures:
                print(
                    f"Reconcile error: operation={failure.operation_id} "
                    f"type={failure.error_type} message={failure.message}",
                    file=sys.stderr,
                )
            if arguments.once:
                break
            deadline = time.monotonic() + arguments.interval_seconds
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(0.2, deadline - time.monotonic()))
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    return 0
