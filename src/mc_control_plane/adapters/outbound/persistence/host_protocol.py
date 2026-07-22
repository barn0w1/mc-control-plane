"""SQLite persistence for enrollment, authenticated observations, and commands."""

import json
import secrets
import sqlite3
from datetime import datetime
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

from mc_control_plane.adapters.outbound.persistence.sqlite import SQLiteDatabase
from mc_control_plane.application.host_protocol import (
    HOST_PROTOCOL_VERSION,
    HostAgentObservation,
    HostAuthenticationError,
    HostCommand,
    HostCommandKind,
    HostCommandState,
    HostEnrollmentError,
    HostProtocolError,
    HostProtocolIncompatible,
    IssuedEnrollment,
)


def _hash_secret(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HostProtocolError(f"{field} must be a non-empty string")
    return value


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise HostProtocolError(f"{field} must be an object")
    return cast(dict[str, Any], value)


class HostProtocolStore:
    """Own host protocol transactions without leaking SQL into the HTTP adapter."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def issue_enrollment(
        self,
        *,
        run_id: str,
        resource_identity: str,
        expires_at: datetime,
        now: datetime,
    ) -> IssuedEnrollment:
        _aware(expires_at, "expires_at")
        _aware(now, "now")
        if expires_at <= now:
            raise ValueError("enrollment expiry must be in the future")
        run_id = _text(run_id, "run_id")
        resource_identity = _text(resource_identity, "resource_identity")
        token = secrets.token_urlsafe(32)
        enrollment_id = f"enrollment-{uuid4().hex}"
        connection = self._database.connect()
        try:
            connection.execute(
                """
                INSERT INTO host_enrollments(
                    id, token_hash, run_id, resource_identity, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    enrollment_id,
                    _hash_secret(token),
                    run_id,
                    resource_identity,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return IssuedEnrollment(
            enrollment_id=enrollment_id,
            token=token,
            run_id=run_id,
            resource_identity=resource_identity,
            expires_at=expires_at,
        )

    def enroll(self, request: dict[str, Any], *, now: datetime) -> HostAgentObservation:
        _aware(now, "now")
        required = {
            "protocol_version",
            "agent_id",
            "run_id",
            "resource_identity",
            "agent_version",
            "enrollment_token",
            "agent_token",
        }
        if set(request) != required:
            raise HostEnrollmentError("enrollment request fields do not match protocol v1")
        if request["protocol_version"] != HOST_PROTOCOL_VERSION:
            raise HostProtocolIncompatible("unsupported host protocol version")
        agent_id = _text(request["agent_id"], "agent_id")
        run_id = _text(request["run_id"], "run_id")
        resource_identity = _text(request["resource_identity"], "resource_identity")
        agent_version = _text(request["agent_version"], "agent_version")
        enrollment_hash = _hash_secret(_text(request["enrollment_token"], "enrollment_token"))
        agent_token_hash = _hash_secret(_text(request["agent_token"], "agent_token"))

        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM host_enrollments WHERE token_hash = ?",
                (enrollment_hash,),
            ).fetchone()
            if row is None:
                raise HostEnrollmentError("unknown enrollment credential")
            if row["run_id"] != run_id or row["resource_identity"] != resource_identity:
                raise HostEnrollmentError("enrollment identity does not match")
            if datetime.fromisoformat(cast(str, row["expires_at"])) <= now:
                raise HostEnrollmentError("enrollment credential has expired")

            if row["consumed_at"] is not None:
                if row["agent_id"] != agent_id or not secrets.compare_digest(
                    cast(str, row["agent_token_hash"]), agent_token_hash
                ):
                    raise HostEnrollmentError("enrollment credential was already consumed")
                existing_row = connection.execute(
                    "SELECT * FROM host_agents WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                connection.commit()
                return self._agent_from_row(existing_row)

            cursor = connection.execute(
                """
                UPDATE host_enrollments
                SET consumed_at = ?, agent_id = ?, agent_token_hash = ?
                WHERE id = ? AND consumed_at IS NULL
                """,
                (now.isoformat(), agent_id, agent_token_hash, row["id"]),
            )
            if cursor.rowcount != 1:
                raise HostEnrollmentError("enrollment credential was consumed concurrently")
            connection.execute(
                """
                INSERT INTO host_agents(
                    agent_id, run_id, resource_identity, token_hash, protocol_version,
                    agent_version, status, enrolled_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'enrolled', ?)
                """,
                (
                    agent_id,
                    run_id,
                    resource_identity,
                    agent_token_hash,
                    HOST_PROTOCOL_VERSION,
                    agent_version,
                    now.isoformat(),
                ),
            )
            agent_row = connection.execute(
                "SELECT * FROM host_agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            connection.commit()
            return self._agent_from_row(agent_row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def poll(
        self,
        agent_token: str,
        request: dict[str, Any],
        *,
        now: datetime,
    ) -> HostCommand | None:
        _aware(now, "now")
        required = {
            "protocol_version",
            "agent_id",
            "run_id",
            "agent_version",
            "boot_id",
            "capabilities",
            "service_states",
            "results",
        }
        if set(request) != required:
            raise HostProtocolError("poll request fields do not match protocol v1")
        if request["protocol_version"] != HOST_PROTOCOL_VERSION:
            raise HostProtocolIncompatible("unsupported host protocol version")
        agent_id = _text(request["agent_id"], "agent_id")
        run_id = _text(request["run_id"], "run_id")
        agent_version = _text(request["agent_version"], "agent_version")
        boot_id = _text(request["boot_id"], "boot_id")
        capabilities = _object(request["capabilities"], "capabilities")
        service_states = _object(request["service_states"], "service_states")
        results = request["results"]
        if not isinstance(results, list):
            raise HostProtocolError("results must be an array")

        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM host_agents WHERE token_hash = ?",
                (_hash_secret(agent_token),),
            ).fetchone()
            if row is None or row["status"] == "revoked":
                raise HostAuthenticationError("invalid host credential")
            if row["agent_id"] != agent_id or row["run_id"] != run_id:
                raise HostAuthenticationError("host credential identity does not match")
            status = (
                "connected" if row["protocol_version"] == HOST_PROTOCOL_VERSION else "incompatible"
            )
            connection.execute(
                """
                UPDATE host_agents
                SET agent_version = ?, status = ?, boot_id = ?, capabilities_json = ?,
                    service_states_json = ?, observed_at = ?
                WHERE agent_id = ?
                """,
                (
                    agent_version,
                    status,
                    boot_id,
                    _json(capabilities),
                    _json(service_states),
                    now.isoformat(),
                    agent_id,
                ),
            )
            for value in results:
                self._record_result(connection, agent_id, _object(value, "result"), now)

            command_row = connection.execute(
                """
                SELECT * FROM host_commands
                WHERE agent_id = ? AND state IN ('pending', 'delivered')
                ORDER BY created_at, command_id
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            command: HostCommand | None = None
            if command_row is not None:
                deadline = datetime.fromisoformat(cast(str, command_row["deadline"]))
                if deadline <= now:
                    connection.execute(
                        """
                        UPDATE host_commands
                        SET state = 'failed', result_json = ?, updated_at = ?
                        WHERE command_id = ?
                        """,
                        (
                            _json({"error_code": "command_expired", "message": "deadline passed"}),
                            now.isoformat(),
                            command_row["command_id"],
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE host_commands
                        SET state = 'delivered', delivery_count = delivery_count + 1,
                            updated_at = ?
                        WHERE command_id = ?
                        """,
                        (now.isoformat(), command_row["command_id"]),
                    )
                    command = self._command_from_row(command_row)
            connection.commit()
            return command
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def queue_command(
        self,
        *,
        command_id: str,
        agent_id: str,
        operation_id: str,
        step: str,
        kind: HostCommandKind,
        deadline: datetime,
        now: datetime,
    ) -> HostCommand:
        _aware(deadline, "deadline")
        _aware(now, "now")
        if deadline <= now:
            raise ValueError("command deadline must be in the future")
        connection = self._database.connect()
        try:
            agent = connection.execute(
                "SELECT run_id FROM host_agents WHERE agent_id = ? AND status != 'revoked'",
                (agent_id,),
            ).fetchone()
            if agent is None:
                raise HostProtocolError("cannot queue a command for an unknown agent")
            connection.execute(
                """
                INSERT INTO host_commands(
                    command_id, agent_id, run_id, operation_id, step, kind,
                    payload_version, payload_json, deadline, state,
                    delivery_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, '{}', ?, 'pending', 0, ?, ?)
                """,
                (
                    _text(command_id, "command_id"),
                    agent_id,
                    agent["run_id"],
                    _text(operation_id, "operation_id"),
                    _text(step, "step"),
                    kind.value,
                    deadline.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM host_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            connection.commit()
            return self._command_from_row(row)
        finally:
            connection.close()

    def get_agent(self, agent_id: str) -> HostAgentObservation | None:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM host_agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            connection.commit()
            return None if row is None else self._agent_from_row(row)
        finally:
            connection.close()

    def get_command(self, command_id: str) -> HostCommand | None:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM host_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            connection.commit()
            return None if row is None else self._command_from_row(row)
        finally:
            connection.close()

    @staticmethod
    def _record_result(
        connection: sqlite3.Connection,
        agent_id: str,
        result: dict[str, Any],
        now: datetime,
    ) -> None:
        required = {"command_id", "state", "error_code", "message", "observation"}
        if set(result) != required:
            raise HostProtocolError("command result fields do not match protocol v1")
        command_id = _text(result["command_id"], "command_id")
        try:
            state = HostCommandState(_text(result["state"], "state"))
        except ValueError as error:
            raise HostProtocolError("unknown command result state") from error
        if not state.is_terminal:
            raise HostProtocolError("command result must be terminal")
        row = connection.execute(
            "SELECT state FROM host_commands WHERE command_id = ? AND agent_id = ?",
            (command_id, agent_id),
        ).fetchone()
        if row is None:
            raise HostProtocolError("result refers to an unknown command")
        if row["state"] in ("succeeded", "failed"):
            return
        error_code = result["error_code"]
        message = result["message"]
        if error_code is not None and not isinstance(error_code, str):
            raise HostProtocolError("error_code must be a string or null")
        if message is not None and not isinstance(message, str):
            raise HostProtocolError("message must be a string or null")
        observation = _object(result["observation"], "observation")
        connection.execute(
            """
            UPDATE host_commands
            SET state = ?, result_json = ?, updated_at = ?
            WHERE command_id = ?
            """,
            (
                state.value,
                _json(
                    {
                        "error_code": error_code,
                        "message": None if message is None else message[:500],
                        "observation": observation,
                    }
                ),
                now.isoformat(),
                command_id,
            ),
        )

    @staticmethod
    def _agent_from_row(row: sqlite3.Row | None) -> HostAgentObservation:
        if row is None:
            raise HostEnrollmentError("enrolled agent record is missing")
        capabilities = cast(str | None, row["capabilities_json"])
        service_states = cast(str | None, row["service_states_json"])
        observed_at = cast(str | None, row["observed_at"])
        return HostAgentObservation(
            agent_id=cast(str, row["agent_id"]),
            run_id=cast(str, row["run_id"]),
            resource_identity=cast(str, row["resource_identity"]),
            protocol_version=cast(int, row["protocol_version"]),
            agent_version=cast(str, row["agent_version"]),
            status=cast(str, row["status"]),
            boot_id=cast(str | None, row["boot_id"]),
            capabilities=(
                None if capabilities is None else cast(dict[str, Any], json.loads(capabilities))
            ),
            service_states=(
                None if service_states is None else cast(dict[str, Any], json.loads(service_states))
            ),
            enrolled_at=datetime.fromisoformat(cast(str, row["enrolled_at"])),
            observed_at=None if observed_at is None else datetime.fromisoformat(observed_at),
        )

    @staticmethod
    def _command_from_row(row: sqlite3.Row | None) -> HostCommand:
        if row is None:
            raise HostProtocolError("command record is missing")
        return HostCommand(
            command_id=cast(str, row["command_id"]),
            agent_id=cast(str, row["agent_id"]),
            run_id=cast(str, row["run_id"]),
            operation_id=cast(str, row["operation_id"]),
            step=cast(str, row["step"]),
            kind=HostCommandKind(cast(str, row["kind"])),
            payload_version=cast(int, row["payload_version"]),
            payload=cast(dict[str, Any], json.loads(cast(str, row["payload_json"]))),
            deadline=datetime.fromisoformat(cast(str, row["deadline"])),
            state=HostCommandState(cast(str, row["state"])),
            delivery_count=cast(int, row["delivery_count"]),
        )
