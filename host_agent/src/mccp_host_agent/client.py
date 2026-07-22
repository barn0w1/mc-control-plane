"""HTTPS-only JSON client which rejects redirects and never logs credentials."""

import json
import ssl
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)


class HostApiError(Exception):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


class HostApiClient:
    def __init__(self, base_url: str, *, ca_file: str | None = None, timeout: float = 20) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Host API requires HTTPS")
        if timeout <= 0:
            raise ValueError("HTTP timeout must be positive")
        context = ssl.create_default_context(cafile=ca_file)
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = timeout
        self._opener = build_opener(_RejectRedirects(), HTTPSHandler(context=context))

    def enroll(self, value: dict[str, Any]) -> dict[str, Any]:
        return self._post("v1/host/enroll", value, None)

    def poll(self, agent_token: str, value: dict[str, Any]) -> dict[str, Any]:
        return self._post("v1/host/poll", value, agent_token)

    def _post(
        self,
        relative_path: str,
        value: dict[str, Any],
        agent_token: str | None,
    ) -> dict[str, Any]:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        request = Request(
            urljoin(self._base_url, relative_path),
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "mccp-host-agent",
            },
        )
        if agent_token is not None:
            request.add_header("Authorization", f"Bearer {agent_token}")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise HostApiError(f"Host API returned HTTP {response.status}")
                body = response.read(64 * 1024 + 1)
        except HTTPError as error:
            raise HostApiError(f"Host API returned HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise HostApiError("Host API request failed") from error
        if len(body) > 64 * 1024:
            raise HostApiError("Host API response exceeded size limit")
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HostApiError("Host API returned invalid JSON") from error
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise HostApiError("Host API response must be a JSON object")
        return cast(dict[str, Any], decoded)
