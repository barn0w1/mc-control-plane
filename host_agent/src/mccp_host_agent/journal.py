"""Local command journal for safe at-least-once delivery and process restarts."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class SavedResult:
    command_id: str
    value: dict[str, Any]


class CommandJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path = path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('received', 'succeeded', 'failed')),
                    result_json TEXT,
                    reported INTEGER NOT NULL DEFAULT 0 CHECK (reported IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        self._path.chmod(0o600)

    def receive(self, command_id: str, request_digest: str) -> SavedResult | None:
        now = datetime.now(UTC).isoformat()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO commands(
                        command_id, request_digest, state, created_at, updated_at
                    ) VALUES (?, ?, 'received', ?, ?)
                    """,
                    (command_id, request_digest, now, now),
                )
                connection.commit()
                return None
            if row["request_digest"] != request_digest:
                raise ValueError("a command ID was reused with different content")
            result = cast(str | None, row["result_json"])
            connection.commit()
            return (
                None
                if result is None
                else SavedResult(command_id, cast(dict[str, Any], json.loads(result)))
            )
        finally:
            connection.close()

    def complete(self, command_id: str, value: dict[str, Any]) -> None:
        state = value.get("state")
        if state not in ("succeeded", "failed"):
            raise ValueError("journal result must be terminal")
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE commands
                SET state = ?, result_json = ?, reported = 0, updated_at = ?
                WHERE command_id = ?
                """,
                (
                    state,
                    json.dumps(value, separators=(",", ":"), sort_keys=True),
                    datetime.now(UTC).isoformat(),
                    command_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(command_id)
            connection.commit()
        finally:
            connection.close()

    def unreported(self) -> list[SavedResult]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT command_id, result_json FROM commands
                WHERE result_json IS NOT NULL AND reported = 0
                ORDER BY created_at, command_id
                """
            ).fetchall()
            connection.commit()
            return [
                SavedResult(
                    cast(str, row["command_id"]),
                    cast(dict[str, Any], json.loads(cast(str, row["result_json"]))),
                )
                for row in rows
            ]
        finally:
            connection.close()

    def mark_reported(self, command_ids: list[str]) -> None:
        if not command_ids:
            return
        connection = self._connect()
        try:
            connection.executemany(
                "UPDATE commands SET reported = 1 WHERE command_id = ?",
                [(value,) for value in command_ids],
            )
            connection.commit()
        finally:
            connection.close()
