"""Ephemeral data-plane credentials attached only while a Host command is delivered."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mc_control_plane.application.host_protocol import HostCommand


class DataLeaseUnavailable(Exception):
    """A short-lived repository lease could not be issued."""


@dataclass(frozen=True, slots=True)
class ResticDataLease:
    repository: str
    access_key_id: str
    secret_access_key: str
    session_token: str
    restic_password: str
    permission: str
    expires_at: datetime

    def wire_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "repository": self.repository,
            "access_key_id": self.access_key_id,
            "secret_access_key": self.secret_access_key,
            "session_token": self.session_token,
            "restic_password": self.restic_password,
            "permission": self.permission,
            "expires_at": self.expires_at.isoformat(),
        }


class DataLeaseProvider(Protocol):
    def issue_for(self, command: HostCommand, now: datetime) -> ResticDataLease: ...
