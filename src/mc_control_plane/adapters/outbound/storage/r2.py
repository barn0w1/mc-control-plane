"""Cloudflare R2 temporary credentials for per-Server-Unit restic repositories."""

import hashlib
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from pathlib import Path
from typing import Any, Protocol, cast

import requests

from mc_control_plane.adapters.outbound.persistence import HostProtocolStore
from mc_control_plane.application.data_lease import DataLeaseUnavailable, ResticDataLease
from mc_control_plane.application.host_protocol import HostCommand, HostCommandKind


class TemporaryCredentialClient(Protocol):
    def create(
        self,
        *,
        bucket: str,
        parent_access_key_id: str,
        permission: str,
        prefix: str,
        ttl_seconds: int,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class R2ResticSettings:
    account_id: str
    bucket: str
    parent_access_key_id: str
    ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        for value, name in (
            (self.account_id, "account_id"),
            (self.bucket, "bucket"),
            (self.parent_access_key_id, "parent_access_key_id"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.ttl_seconds < 60 or self.ttl_seconds > 604800:
            raise ValueError("R2 temporary credential TTL must be between 60 and 604800 seconds")


class CloudflareTemporaryCredentialClient:
    def __init__(self, account_id: str, api_token: str, *, timeout_seconds: float = 20) -> None:
        if not account_id.strip() or not api_token.strip():
            raise ValueError("Cloudflare account ID and API token are required")
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/temp-access-credentials"
        )
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._timeout = timeout_seconds

    def create(
        self,
        *,
        bucket: str,
        parent_access_key_id: str,
        permission: str,
        prefix: str,
        ttl_seconds: int,
    ) -> Mapping[str, Any]:
        try:
            response = requests.post(
                self._url,
                headers=self._headers,
                json={
                    "bucket": bucket,
                    "parentAccessKeyId": parent_access_key_id,
                    "permission": permission,
                    "prefixes": [prefix],
                    "ttlSeconds": ttl_seconds,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise DataLeaseUnavailable("Cloudflare temporary credential request failed") from error
        try:
            document = response.json()
        except ValueError as error:
            raise DataLeaseUnavailable(
                f"Cloudflare returned invalid JSON: status={response.status_code}"
            ) from error
        if not isinstance(document, dict):
            raise DataLeaseUnavailable(
                f"Cloudflare returned an invalid document: status={response.status_code}"
            )
        if not response.ok or document.get("success") is not True:
            details = _cloudflare_error(document)
            for sensitive in (bucket, parent_access_key_id):
                if len(sensitive) >= 3:
                    details = details.replace(sensitive, "[redacted]")
            raise DataLeaseUnavailable(
                f"Cloudflare rejected temporary credential request: status={response.status_code} "
                f"{details}"
            )
        result = document.get("result")
        if not isinstance(result, dict):
            raise DataLeaseUnavailable("Cloudflare temporary credential response was invalid")
        return cast(Mapping[str, Any], result)


class R2ResticLeaseBroker:
    """Mint a lease after resolving ownership from durable database relations."""

    def __init__(
        self,
        store: HostProtocolStore,
        client: TemporaryCredentialClient,
        settings: R2ResticSettings,
    ) -> None:
        self._store = store
        self._client = client
        self._settings = settings

    def issue_for(self, command: HostCommand, now: datetime) -> ResticDataLease:
        if not command.kind.requires_data_lease:
            raise DataLeaseUnavailable("command does not require a data lease")
        server_unit_id = self._store.server_unit_for_command(command.command_id)
        if command.payload.get("server_unit_id") != server_unit_id:
            raise DataLeaseUnavailable("data command ownership context does not match")
        remaining = ceil((command.deadline - now).total_seconds())
        if remaining <= 0:
            raise DataLeaseUnavailable("data command deadline has passed")
        if self._settings.ttl_seconds < remaining + 60:
            raise DataLeaseUnavailable(
                "R2 temporary credential TTL must exceed the command deadline by 60 seconds"
            )
        prefix = repository_prefix(server_unit_id)
        permission = (
            "object-read-only"
            if command.kind is HostCommandKind.RESTORE_DATA
            else "object-read-write"
        )
        raw = self._client.create(
            bucket=self._settings.bucket,
            parent_access_key_id=self._settings.parent_access_key_id,
            permission=permission,
            prefix=prefix,
            ttl_seconds=self._settings.ttl_seconds,
        )
        access_key = _credential(raw, "accessKeyId")
        secret_key = _credential(raw, "secretAccessKey")
        session_token = _credential(raw, "sessionToken")
        repository = (
            f"s3:https://{self._settings.account_id}.r2.cloudflarestorage.com/"
            f"{self._settings.bucket}/{prefix}"
        )
        return ResticDataLease(
            repository=repository,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=session_token,
            permission=permission,
            expires_at=now + timedelta(seconds=self._settings.ttl_seconds),
        )

    def preflight(self) -> R2PreflightReport:
        prefix = "mc-control-plane/preflight/temporary-credentials/"
        raw = self._client.create(
            bucket=self._settings.bucket,
            parent_access_key_id=self._settings.parent_access_key_id,
            permission="object-read-write",
            prefix=prefix,
            ttl_seconds=self._settings.ttl_seconds,
        )
        _credential(raw, "accessKeyId")
        _credential(raw, "secretAccessKey")
        _credential(raw, "sessionToken")
        return R2PreflightReport(
            bucket=self._settings.bucket,
            prefix=prefix,
            permission="object-read-write",
            ttl_seconds=self._settings.ttl_seconds,
        )


@dataclass(frozen=True, slots=True)
class R2PreflightReport:
    bucket: str
    prefix: str
    permission: str
    ttl_seconds: int


def repository_prefix(server_unit_id: str) -> str:
    identity = hashlib.sha256(server_unit_id.encode()).hexdigest()
    return f"mc-control-plane/server-units/{identity}/repository"


def load_secret_file(path: Path) -> bytes:
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError(f"secret file must not be accessible by group or others: {path}")
    value = path.read_bytes()
    if len(value) < 32:
        raise ValueError(f"secret file must contain at least 32 bytes: {path}")
    return value


def _credential(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise DataLeaseUnavailable(f"Cloudflare response omitted {name}")
    return item


def _cloudflare_error(document: Mapping[str, Any]) -> str:
    errors = document.get("errors")
    if not isinstance(errors, list):
        return "error=unspecified"
    details: list[str] = []
    for item in errors[:3]:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        message = item.get("message")
        safe_message = " ".join(str(message).split())[:200]
        details.append(f"code={code} message={safe_message or 'unspecified'}")
    return "; ".join(details) if details else "error=unspecified"
