"""Minimal CLI entrypoint for local control-plane administration."""

import argparse
from pathlib import Path

from mc_control_plane import __version__
from mc_control_plane.adapters.outbound.persistence import SQLiteDatabase


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mc-control-plane")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init_database = commands.add_parser("init-db", help="initialize or migrate the database")
    init_database.add_argument("database", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "init-db":
        database = SQLiteDatabase(arguments.database)
        database.migrate()
        print(f"initialized {arguments.database}")
        return 0
    raise AssertionError(f"unhandled command: {arguments.command}")
