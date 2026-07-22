"""Host agent service entrypoint."""

import argparse
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from time import sleep

from mccp_host_agent.agent import HostAgent
from mccp_host_agent.client import HostApiClient, HostApiError
from mccp_host_agent.config import load_config
from mccp_host_agent.journal import CommandJournal
from mccp_host_agent.runtime import HostRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mccp-host-agent")
    parser.add_argument(
        "--config", type=Path, default=Path("/etc/mc-control-plane-agent/config.json")
    )
    parser.add_argument(
        "--state-directory",
        type=Path,
        default=Path("/var/lib/mc-control-plane-agent"),
    )
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        config = load_config(arguments.config)
        state = arguments.state_directory
        agent = HostAgent(
            config,
            config_path=arguments.config,
            token_path=state / "agent-token",
            journal=CommandJournal(state / "journal.db"),
            client=HostApiClient(config.control_plane_url, ca_file=config.ca_file),
            runtime=HostRuntime(config.fixture_image, run_id=config.run_id),
        )
        if arguments.once:
            agent.run_once()
            return 0
        stopping = False

        def stop(_signal: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopping:
            try:
                delay = agent.run_once()
            except (HostApiError, OSError) as error:
                print(f"temporary agent error: {error}", file=sys.stderr)
                delay = config.poll_seconds
            if delay > 0 and not stopping:
                sleep(delay)
        return 0
    except (ValueError, OSError) as error:
        print(f"agent configuration error: {error}", file=sys.stderr)
        return 1
