"""Minimal CLI entrypoint for local control-plane administration."""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from mc_control_plane.adapters.outbound.host import HostBootstrapSpec, artifact_sha256
from mc_control_plane.adapters.outbound.persistence import HostProtocolStore, SQLiteDatabase
from mc_control_plane.application.host_protocol import (
    HOST_AGENT_ARTIFACT_PATH,
    HOST_AGENT_VERSION,
)
from mc_control_plane.application.ports.compute import ComputeProviderError
from mc_control_plane.domain.models import RuntimeSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mc-control-plane")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init_database = commands.add_parser("init-db", help="initialize or migrate the database")
    init_database.add_argument("database", type=Path)

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
    return parser


def _add_linode_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-type", required=True)
    parser.add_argument("--firewall-id", required=True)
    parser.add_argument("--image", default=DEBIAN_13_IMAGE)
    _add_ssh_key_argument(parser)


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
        container_image="not-used-in-gate1",
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
    if arguments.command == "host-api-serve":
        try:
            database = SQLiteDatabase(arguments.database)
            database.migrate()
            print(
                f"Serving Host API on {arguments.bind}:{arguments.port} "
                f"artifact-path={HOST_AGENT_ARTIFACT_PATH}"
            )
            serve_host_api(
                HostApiApplication(HostProtocolStore(database)),
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
    try:
        provider = _linode_provider(arguments)
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
        OSError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {arguments.command}")
