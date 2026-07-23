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
    load_secret_file,
)
from mc_control_plane.application.commands.lifecycle import (
    RequestOperationRetry,
    RequestSnapshot,
    RequestStop,
)
from mc_control_plane.application.commands.server_unit import (
    CreateServerUnit,
    RequestServerUnitCreation,
)
from mc_control_plane.application.commands.start import RequestStart, StartServerUnit
from mc_control_plane.application.data_lease import DataLeaseUnavailable
from mc_control_plane.application.gate3_cleanup import cleanup_gate3_runtime
from mc_control_plane.application.gate4 import Gate4Error, run_gate4_data_lifecycle
from mc_control_plane.application.gate5 import Gate5Error, run_gate5_minecraft_lifecycle
from mc_control_plane.application.host_protocol import (
    HOST_AGENT_ARTIFACT_PATH,
    HOST_AGENT_VERSION,
)
from mc_control_plane.application.ports.compute import ComputeProviderError
from mc_control_plane.application.ports.persistence import (
    UnitOfWorkFactory as UnitOfWorkFactoryPort,
)
from mc_control_plane.application.queries.snapshots import ListServerUnitSnapshots
from mc_control_plane.application.queries.status import GetServerUnitStatus
from mc_control_plane.application.reconciler import OperationReconciler
from mc_control_plane.application.support import SystemClock, UuidGenerator
from mc_control_plane.application.workflows.snapshot import SnapshotWorkflow
from mc_control_plane.application.workflows.start import StartWorkflow
from mc_control_plane.application.workflows.stop import StopWorkflow
from mc_control_plane.config import DEFAULT_CONFIG_PATH, NodeConfig, load_node_config
from mc_control_plane.domain.errors import ControlPlaneError
from mc_control_plane.domain.models import MinecraftSpec, RuntimeSpec
from mc_control_plane.domain.states import OperationState


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

    unit_create = commands.add_parser("server-unit-create", help="register a Server Unit")
    unit_create.add_argument("--database", required=True, type=Path)
    unit_create.add_argument("--id", required=True)
    unit_create.add_argument("--name", required=True)
    _add_runtime_spec_arguments(unit_create)
    _add_minecraft_spec_arguments(unit_create)

    unit_start = commands.add_parser("server-unit-start", help="request a durable start")
    unit_start.add_argument("--database", required=True, type=Path)
    unit_start.add_argument("--server-unit-id", required=True)
    unit_start.add_argument(
        "--source-snapshot-id",
        help="restore this owned snapshot instead of selecting the latest snapshot",
    )
    _add_wait_arguments(unit_start)

    unit_snapshot = commands.add_parser(
        "server-unit-snapshot", help="request a durable live Minecraft snapshot"
    )
    unit_snapshot.add_argument("--database", required=True, type=Path)
    unit_snapshot.add_argument("--server-unit-id", required=True)
    _add_wait_arguments(unit_snapshot)

    unit_stop = commands.add_parser(
        "server-unit-stop",
        help="request graceful stop, snapshot, and runtime deletion",
    )
    unit_stop.add_argument("--database", required=True, type=Path)
    unit_stop.add_argument("--server-unit-id", required=True)
    _add_wait_arguments(unit_stop)

    unit_status = commands.add_parser("server-unit-status", help="show layered status as JSON")
    unit_status.add_argument("--database", required=True, type=Path)
    unit_status.add_argument("--server-unit-id", required=True)

    unit_snapshots = commands.add_parser(
        "server-unit-snapshots", help="list committed snapshots as JSON"
    )
    unit_snapshots.add_argument("--database", required=True, type=Path)
    unit_snapshots.add_argument("--server-unit-id", required=True)

    operation_retry = commands.add_parser(
        "operation-retry",
        help="resume one blocked Operation after correcting its cause",
    )
    operation_retry.add_argument("--database", required=True, type=Path)
    operation_retry.add_argument("--operation-id", required=True)

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

    node_host_api = commands.add_parser(
        "node-host-api",
        help="run the configured resident Host API process",
    )
    node_host_api.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    node_reconciler = commands.add_parser(
        "node-reconciler",
        help="run the configured resident asynchronous reconciler",
    )
    node_reconciler.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    node_check = commands.add_parser(
        "node-check",
        help="validate resident-node configuration and local files",
    )
    node_check.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    short_start = commands.add_parser("start", help="request start using node configuration")
    short_start.add_argument("server_unit_id")
    short_start.add_argument("--snapshot", dest="source_snapshot_id")
    short_start.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    _add_wait_arguments(short_start)

    short_snapshot = commands.add_parser(
        "snapshot", help="request a live snapshot using node configuration"
    )
    short_snapshot.add_argument("server_unit_id")
    short_snapshot.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    _add_wait_arguments(short_snapshot)

    short_stop = commands.add_parser("stop", help="request stop using node configuration")
    short_stop.add_argument("server_unit_id")
    short_stop.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    _add_wait_arguments(short_stop)

    short_status = commands.add_parser("status", help="show status using node configuration")
    short_status.add_argument("server_unit_id")
    short_status.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    short_snapshots = commands.add_parser(
        "snapshots", help="list snapshots using node configuration"
    )
    short_snapshots.add_argument("server_unit_id")
    short_snapshots.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    short_retry = commands.add_parser(
        "retry", help="retry a blocked Operation using node configuration"
    )
    short_retry.add_argument("operation_id")
    short_retry.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

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
    host_api.add_argument("--r2-lease-ttl-seconds", type=int, default=3600)

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

    gate5 = commands.add_parser(
        "linode-gate5-check",
        help="run the two-Host Paper Minecraft lifecycle acceptance check",
    )
    _add_linode_arguments(gate5)
    gate5.add_argument("--database", required=True, type=Path)
    gate5.add_argument("--server-unit-id", required=True)
    gate5.add_argument("--host-bootstrap-key", required=True, type=Path)
    gate5.add_argument("--control-plane-url", required=True)
    gate5.add_argument("--agent-wheel", required=True, type=Path)
    gate5.add_argument("--fixture-image", required=True)
    gate5.add_argument("--minecraft-image", required=True)
    gate5.add_argument("--minecraft-version", required=True)
    gate5.add_argument("--paper-build", required=True)
    gate5.add_argument("--minecraft-memory", default="512M")
    gate5.add_argument("--system-id", default="mc-control-plane")
    gate5.add_argument("--timeout-seconds", type=float, default=2400.0)
    gate5.add_argument("--poll-seconds", type=float, default=5.0)
    gate5.add_argument("--accept-minecraft-eula", action="store_true")
    gate5.add_argument("--confirm-billable-two-host-check", action="store_true")
    return parser


def _add_linode_arguments(parser: argparse.ArgumentParser) -> None:
    _add_runtime_spec_arguments(parser)
    _add_ssh_key_argument(parser)


def _add_runtime_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-type", required=True)
    parser.add_argument("--firewall-id", required=True)
    parser.add_argument("--image", default=DEBIAN_13_IMAGE)


def _add_minecraft_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--minecraft-image")
    parser.add_argument("--minecraft-version")
    parser.add_argument("--paper-build")
    parser.add_argument("--minecraft-memory", default="512M")
    parser.add_argument("--accept-minecraft-eula", action="store_true")


def _add_ssh_key_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ssh-public-key",
        action="append",
        required=True,
        type=Path,
        help="path to an SSH public key; may be specified more than once",
    )


def _add_wait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait",
        action="store_true",
        help="wait in this CLI process while resident services continue independently",
    )
    parser.add_argument("--wait-timeout-seconds", type=float, default=3600.0)


def _runtime_spec(arguments: argparse.Namespace) -> RuntimeSpec:
    return RuntimeSpec(
        region=arguments.region,
        instance_type=arguments.instance_type,
        image=arguments.image,
        firewall_id=arguments.firewall_id,
    )


def _minecraft_spec(arguments: argparse.Namespace) -> MinecraftSpec | None:
    values = (
        arguments.minecraft_image,
        arguments.minecraft_version,
        arguments.paper_build,
    )
    if not any(value is not None for value in values):
        if arguments.accept_minecraft_eula:
            raise ValueError("Minecraft EULA acceptance requires a complete Minecraft spec")
        return None
    if not all(value is not None for value in values):
        raise ValueError("Minecraft image, version, and Paper build must be provided together")
    return MinecraftSpec(
        image=arguments.minecraft_image,
        minecraft_version=arguments.minecraft_version,
        paper_build=arguments.paper_build,
        memory=arguments.minecraft_memory,
        eula_accepted=arguments.accept_minecraft_eula,
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
    try:
        arguments, node_config = _resolve_operational_arguments(arguments)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if arguments.command == "node-check":
        assert node_config is not None
        try:
            _check_node(node_config)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(
            "Node configuration passed: "
            f"database={node_config.control_plane.database} "
            f"host-api={node_config.host_api.bind}:{node_config.host_api.port} "
            f"artifact={HOST_AGENT_ARTIFACT_PATH}"
        )
        return 0
    if arguments.command == "init-db":
        database = SQLiteDatabase(arguments.database)
        database.migrate()
        print(f"initialized {arguments.database}")
        return 0
    if arguments.command == "host-bootstrap-key-create":
        try:
            create_bootstrap_key(arguments.path)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"created Host bootstrap key: {arguments.path}")
        return 0
    if arguments.command in {
        "server-unit-create",
        "server-unit-start",
        "server-unit-snapshot",
        "server-unit-stop",
        "server-unit-status",
        "server-unit-snapshots",
        "operation-retry",
    }:
        try:
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            unit_of_work = cast(UnitOfWorkFactoryPort, SQLiteUnitOfWorkFactory(database))
            clock = SystemClock()
            if arguments.command == "server-unit-create":
                unit = RequestServerUnitCreation(unit_of_work, clock)(
                    CreateServerUnit(
                        arguments.id,
                        arguments.name,
                        _runtime_spec(arguments),
                        _minecraft_spec(arguments),
                    )
                )
                print(f"Server Unit created: id={unit.id} desired-state={unit.desired_state}")
                return 0
            if arguments.command == "server-unit-start":
                accepted = RequestStart(unit_of_work, clock, UuidGenerator())(
                    StartServerUnit(
                        arguments.server_unit_id,
                        source_snapshot_id=arguments.source_snapshot_id,
                        use_latest_snapshot=arguments.source_snapshot_id is None,
                        require_minecraft_spec=True,
                    )
                )
                print(f"Start accepted: operation={accepted.operation_id} run={accepted.run_id}")
                return _maybe_wait(arguments, unit_of_work, accepted.operation_id)
            if arguments.command == "server-unit-snapshot":
                lifecycle_accepted = RequestSnapshot(unit_of_work, clock, UuidGenerator())(
                    arguments.server_unit_id
                )
                print(
                    f"Snapshot accepted: operation={lifecycle_accepted.operation_id} "
                    f"run={lifecycle_accepted.run_id}"
                )
                return _maybe_wait(arguments, unit_of_work, lifecycle_accepted.operation_id)
            if arguments.command == "server-unit-stop":
                lifecycle_accepted = RequestStop(unit_of_work, clock, UuidGenerator())(
                    arguments.server_unit_id
                )
                print(
                    f"Stop accepted: operation={lifecycle_accepted.operation_id} "
                    f"run={lifecycle_accepted.run_id}"
                )
                return _maybe_wait(arguments, unit_of_work, lifecycle_accepted.operation_id)
            if arguments.command == "operation-retry":
                operation = RequestOperationRetry(unit_of_work, clock)(arguments.operation_id)
                print(
                    f"Operation retry accepted: operation={operation.id} "
                    f"kind={operation.kind.value} step={operation.step}"
                )
                return 0
            if arguments.command == "server-unit-snapshots":
                snapshots = ListServerUnitSnapshots(unit_of_work)(arguments.server_unit_id)
                print(
                    json.dumps(
                        [snapshot.as_dict() for snapshot in snapshots],
                        indent=2,
                        sort_keys=True,
                    )
                )
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
            _run_host_api(arguments)
        except KeyboardInterrupt:
            return 0
        except (DataLeaseUnavailable, OSError, ValueError) as error:
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
    if arguments.command == "linode-gate5-check" and not (
        arguments.confirm_billable_two_host_check
    ):
        print(
            "refusing billable check without --confirm-billable-two-host-check",
            file=sys.stderr,
        )
        return 2
    if arguments.command == "linode-gate5-check" and not arguments.accept_minecraft_eula:
        print(
            "refusing Minecraft start without --accept-minecraft-eula",
            file=sys.stderr,
        )
        return 2
    try:
        if arguments.command == "reconciler-run":
            return _run_reconciler(arguments)
        provider = _linode_provider(arguments)
        if arguments.command == "linode-gate5-check":
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
                "Starting billable Gate 5 check; two sequential Linodes will be created "
                "and deleted only after a stopped Minecraft snapshot is committed."
            )
            gate5_result = run_gate5_minecraft_lifecycle(
                unit_of_work,
                store,
                provider,
                OperationReconciler(unit_of_work, workflow, clock),
                clock,
                UuidGenerator(),
                server_unit_id=arguments.server_unit_id,
                system_id=arguments.system_id,
                minecraft_image=arguments.minecraft_image,
                minecraft_version=arguments.minecraft_version,
                paper_build=arguments.paper_build,
                memory=arguments.minecraft_memory,
                eula_accepted=arguments.accept_minecraft_eula,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
                progress=lambda message: print(f"Gate 5: {message}"),
            )
            print(
                "Gate 5 check passed: "
                f"manual-snapshot={gate5_result.manual_snapshot_id} "
                f"stop-snapshot={gate5_result.stop_snapshot_id} "
                f"restored-stop-snapshot={gate5_result.restored_stop_snapshot_id} "
                "paper=ready live-snapshot=quiesced graceful-stop=passed "
                "fresh-host-restore=passed restart=passed cleanup=confirmed"
            )
            return 0
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
                "fresh-host-restore=passed snapshots=verified "
                "snapshot-before-delete=passed cleanup=confirmed"
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
        Gate5Error,
        ControlPlaneError,
        OSError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {arguments.command}")


def _resolve_operational_arguments(
    arguments: argparse.Namespace,
) -> tuple[argparse.Namespace, NodeConfig | None]:
    config_commands = {
        "node-host-api",
        "node-reconciler",
        "node-check",
        "start",
        "snapshot",
        "stop",
        "status",
        "snapshots",
        "retry",
    }
    if arguments.command not in config_commands:
        return arguments, None

    config = load_node_config(arguments.config)
    control = config.control_plane
    if arguments.command == "node-check":
        return arguments, config
    if arguments.command == "node-host-api":
        host_api = config.host_api
        r2 = config.r2
        return (
            argparse.Namespace(
                command="host-api-serve",
                database=control.database,
                bind=host_api.bind,
                port=host_api.port,
                tls_certificate=host_api.tls_certificate,
                tls_private_key=host_api.tls_private_key,
                agent_wheel=control.agent_wheel,
                r2_account_id=r2.account_id,
                r2_bucket=r2.bucket,
                r2_parent_access_key_id=r2.parent_access_key_id,
                cloudflare_api_token_file=r2.cloudflare_api_token_file,
                r2_lease_ttl_seconds=r2.lease_ttl_seconds,
            ),
            config,
        )
    if arguments.command == "node-reconciler":
        return (
            argparse.Namespace(
                command="reconciler-run",
                database=control.database,
                host_bootstrap_key=control.host_bootstrap_key,
                control_plane_url=control.control_plane_url,
                agent_wheel=control.agent_wheel,
                fixture_image=control.fixture_image,
                system_id=control.system_id,
                interval_seconds=control.interval_seconds,
                limit=control.operation_limit,
                once=False,
                ssh_public_key=list(control.ssh_public_keys),
            ),
            config,
        )

    aliases = {
        "start": "server-unit-start",
        "snapshot": "server-unit-snapshot",
        "stop": "server-unit-stop",
        "status": "server-unit-status",
        "snapshots": "server-unit-snapshots",
        "retry": "operation-retry",
    }
    arguments.command = aliases[arguments.command]
    arguments.database = control.database
    return arguments, config


def _check_node(config: NodeConfig) -> None:
    control = config.control_plane
    if not control.database.parent.is_dir():
        raise ValueError(f"database parent directory does not exist: {control.database.parent}")
    load_bootstrap_key(control.host_bootstrap_key)
    artifact_sha256(control.agent_wheel.read_bytes())
    for key in control.ssh_public_keys:
        if not key.read_text().strip():
            raise ValueError(f"SSH public key is empty: {key}")
    load_secret_file(config.r2.cloudflare_api_token_file)
    if not os.environ.get("LINODE_TOKEN", "").strip():
        raise ValueError("LINODE_TOKEN must be set in the environment")


def _maybe_wait(
    arguments: argparse.Namespace,
    unit_of_work: UnitOfWorkFactoryPort,
    operation_id: str,
) -> int:
    if not getattr(arguments, "wait", False):
        return 0
    timeout = arguments.wait_timeout_seconds
    if timeout <= 0:
        raise ValueError("wait timeout must be positive")
    deadline = time.monotonic() + timeout
    previous: tuple[str, str, str | None] | None = None
    while True:
        with unit_of_work() as work:
            operation = work.operations.get(operation_id)
        if operation is None:
            raise ValueError(f"operation disappeared while waiting: {operation_id}")
        current = (
            operation.state.value,
            str(operation.step),
            operation.last_error_code,
        )
        if current != previous:
            print(
                f"Operation: id={operation.id} state={current[0]} step={current[1]}"
                + ("" if current[2] is None else f" error={current[2]}"),
                flush=True,
            )
            previous = current
        if operation.state is OperationState.SUCCEEDED:
            return 0
        if operation.state in {OperationState.BLOCKED, OperationState.CANCELLED}:
            print(
                f"error: operation ended in {operation.state.value}: "
                f"{operation.last_error_message or operation.last_error_code or 'no detail'}",
                file=sys.stderr,
            )
            return 1
        if time.monotonic() >= deadline:
            print(
                f"error: timed out waiting for operation {operation_id}; it continues in background",
                file=sys.stderr,
            )
            return 2
        time.sleep(2)


def _run_host_api(arguments: argparse.Namespace) -> None:
    database = SQLiteDatabase(arguments.database)
    database.migrate()
    store = HostProtocolStore(database)
    data_leases = _data_lease_broker(arguments, store)
    if data_leases is not None:
        r2_report = data_leases.preflight()
        print(
            "R2 data lease preflight passed: "
            f"bucket={r2_report.bucket} permission={r2_report.permission} "
            f"ttl={r2_report.ttl_seconds}s",
            flush=True,
        )
    print(
        f"Serving Host API on {arguments.bind}:{arguments.port} "
        f"artifact-path={HOST_AGENT_ARTIFACT_PATH}",
        flush=True,
    )
    serve_host_api(
        HostApiApplication(
            store,
            data_leases=data_leases,
            report_error=lambda message: print(
                f"Host API temporary error: {message}", file=sys.stderr
            ),
        ),
        bind=arguments.bind,
        port=arguments.port,
        tls_certificate=arguments.tls_certificate,
        tls_private_key=arguments.tls_private_key,
        agent_artifact=arguments.agent_wheel,
    )


def _data_lease_broker(
    arguments: argparse.Namespace, store: HostProtocolStore
) -> R2ResticLeaseBroker | None:
    values = (
        arguments.r2_account_id,
        arguments.r2_bucket,
        arguments.r2_parent_access_key_id,
        arguments.cloudflare_api_token_file,
    )
    if not any(value is not None for value in values):
        return None
    if not all(value is not None for value in values):
        raise ValueError("all R2 data lease options must be provided together")
    token = load_secret_file(arguments.cloudflare_api_token_file).decode().strip()
    settings = R2ResticSettings(
        arguments.r2_account_id,
        arguments.r2_bucket,
        arguments.r2_parent_access_key_id,
        arguments.r2_lease_ttl_seconds,
    )
    client = CloudflareTemporaryCredentialClient(settings.account_id, token)
    return R2ResticLeaseBroker(store, client, settings)


def _run_reconciler(arguments: argparse.Namespace) -> int:
    if arguments.interval_seconds <= 0 or arguments.limit <= 0:
        raise ValueError("reconciler interval and limit must be positive")
    database = SQLiteDatabase(arguments.database)
    database.migrate()
    store = HostProtocolStore(database)
    unit_of_work = cast(UnitOfWorkFactoryPort, SQLiteUnitOfWorkFactory(database))
    clock = SystemClock()
    provider = _linode_provider(arguments)
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
        host_commands=store,
    )
    reconciler = OperationReconciler(
        unit_of_work,
        workflow,
        clock,
        SnapshotWorkflow(unit_of_work, store, clock),
        StopWorkflow(
            unit_of_work,
            store,
            provider,
            clock,
            system_id=arguments.system_id,
        ),
    )
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
                    f"state={result.state.value} step={result.step} changed={result.changed}"
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
