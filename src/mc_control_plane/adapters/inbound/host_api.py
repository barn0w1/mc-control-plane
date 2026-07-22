"""Small HTTPS adapter for the outbound-polling Host protocol."""

import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any, cast

from mc_control_plane.adapters.outbound.persistence.host_protocol import HostProtocolStore
from mc_control_plane.application.host_protocol import (
    HOST_PROTOCOL_VERSION,
    HostAuthenticationError,
    HostEnrollmentError,
    HostProtocolError,
    HostProtocolIncompatible,
)

MAX_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class HostApiResponse:
    status: int
    body: dict[str, Any]


class HostApiApplication:
    """Transport-independent protocol endpoint used by HTTP and deterministic tests."""

    def __init__(
        self,
        store: HostProtocolStore,
        *,
        now: Callable[[], datetime] | None = None,
        poll_after_seconds: int = 5,
    ) -> None:
        if poll_after_seconds <= 0:
            raise ValueError("poll interval must be positive")
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))
        self._poll_after_seconds = poll_after_seconds

    def handle(
        self,
        path: str,
        body: dict[str, Any],
        authorization: str | None = None,
    ) -> HostApiResponse:
        try:
            if path == "/v1/host/enroll":
                agent = self._store.enroll(body, now=self._now())
                return HostApiResponse(
                    HTTPStatus.OK,
                    {
                        "protocol_version": HOST_PROTOCOL_VERSION,
                        "agent_id": agent.agent_id,
                        "status": "enrolled",
                    },
                )
            if path == "/v1/host/poll":
                token = self._bearer_token(authorization)
                command = self._store.poll(token, body, now=self._now())
                return HostApiResponse(
                    HTTPStatus.OK,
                    {
                        "protocol_version": HOST_PROTOCOL_VERSION,
                        "command": None if command is None else command.wire_value(),
                        "poll_after_seconds": self._poll_after_seconds,
                    },
                )
            return HostApiResponse(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except HostAuthenticationError:
            return HostApiResponse(
                HTTPStatus.UNAUTHORIZED,
                {"error": HostAuthenticationError.code},
            )
        except HostProtocolIncompatible:
            return HostApiResponse(
                HTTPStatus.CONFLICT,
                {"error": HostProtocolIncompatible.code},
            )
        except HostEnrollmentError:
            return HostApiResponse(
                HTTPStatus.UNAUTHORIZED,
                {"error": HostEnrollmentError.code},
            )
        except HostProtocolError as error:
            return HostApiResponse(
                HTTPStatus.BAD_REQUEST,
                {"error": error.code},
            )

    @staticmethod
    def _bearer_token(value: str | None) -> str:
        if value is None or not value.startswith("Bearer "):
            raise HostAuthenticationError("missing host credential")
        token = value.removeprefix("Bearer ")
        if not token or token.strip() != token:
            raise HostAuthenticationError("invalid host credential")
        return token


def serve_host_api(
    application: HostApiApplication,
    *,
    bind: str,
    port: int,
    tls_certificate: Path | None,
    tls_private_key: Path | None,
) -> None:
    """Serve the API, requiring TLS whenever it is reachable beyond loopback."""

    if (tls_certificate is None) != (tls_private_key is None):
        raise ValueError("TLS certificate and private key must be provided together")
    if tls_certificate is None and not ip_address(bind).is_loopback:
        raise ValueError("non-loopback Host API binding requires TLS")

    class Handler(BaseHTTPRequestHandler):
        server_version = "mc-control-plane-host-api"

        def do_POST(self) -> None:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            length_text = self.headers.get("Content-Length", "")
            if content_type != "application/json" or not length_text.isdigit():
                self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
                return
            length = int(length_text)
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._write(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_size"})
                return
            try:
                value = json.loads(self.rfile.read(length))
            except UnicodeDecodeError, json.JSONDecodeError:
                self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return
            if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
                self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return
            response = application.handle(
                self.path,
                cast(dict[str, Any], value),
                self.headers.get("Authorization"),
            )
            self._write(response.status, response.body)

        def _write(self, status: int, body: dict[str, Any]) -> None:
            encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            # The standard access log contains no request body or credentials.
            super().log_message(format, *args)

    server = ThreadingHTTPServer((bind, port), Handler)
    if tls_certificate is not None and tls_private_key is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(tls_certificate, tls_private_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
