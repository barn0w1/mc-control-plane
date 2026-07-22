"""Cloudflare R2 temporary credentials for per-Server-Unit restic repositories."""

import base64
import hashlib
import hmac
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    ttl_seconds: int = 900

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
            response.raise_for_status()
            document = response.json()
        except (requests.RequestException, ValueError) as error:
            raise DataLeaseUnavailable("Cloudflare temporary credential request failed") from error
        if not isinstance(document, dict) or document.get("success") is not True:
            raise DataLeaseUnavailable("Cloudflare rejected temporary credential request")
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
        root_secret: bytes,
    ) -> None:
        if len(root_secret) < 32:
            raise ValueError("restic root secret must contain at least 32 bytes")
        self._store = store
        self._client = client
        self._settings = settings
        self._root_secret = root_secret

    def issue_for(self, command: HostCommand, now: datetime) -> ResticDataLease:
        if not command.kind.requires_data_lease:
            raise DataLeaseUnavailable("command does not require a data lease")
        server_unit_id = self._store.server_unit_for_command(command.command_id)
        if command.payload.get("server_unit_id") != server_unit_id:
            raise DataLeaseUnavailable("data command ownership context does not match")
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
        password = base64.urlsafe_b64encode(
            hmac.new(
                self._root_secret,
                b"mccp-restic-password-v1\0" + server_unit_id.encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        repository = (
            f"s3:https://{self._settings.account_id}.r2.cloudflarestorage.com/"
            f"{self._settings.bucket}/{prefix}"
        )
        return ResticDataLease(
            repository=repository,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=session_token,
            restic_password=password,
            permission=permission,
            expires_at=now + timedelta(seconds=self._settings.ttl_seconds),
        )


def repository_prefix(server_unit_id: str) -> str:
    identity = hashlib.sha256(server_unit_id.encode()).hexdigest()
    return f"mc-control-plane/server-units/{identity}/repository"


def load_root_secret(path: Path) -> bytes:
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
