"""Strict local configuration; Control Plane commands cannot change these boundaries."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

_DIGEST_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AgentConfig:
    control_plane_url: str
    agent_id: str
    run_id: str
    resource_identity: str
    enrollment_token: str | None
    fixture_image: str
    poll_seconds: int = 5
    ca_file: str | None = None

    def __post_init__(self) -> None:
        endpoint = urlsplit(self.control_plane_url)
        if endpoint.scheme != "https" or not endpoint.netloc or endpoint.query or endpoint.fragment:
            raise ValueError("control_plane_url must be an HTTPS origin or path without query")
        for field in ("agent_id", "run_id", "resource_identity"):
            value = getattr(self, field)
            if not value or not value.strip():
                raise ValueError(f"{field} must not be empty")
        if self.enrollment_token is not None and not self.enrollment_token.strip():
            raise ValueError("enrollment_token must be null or non-empty")
        if not _DIGEST_IMAGE.fullmatch(self.fixture_image):
            raise ValueError("fixture_image must be fully qualified and pinned by sha256 digest")
        if self.poll_seconds <= 0 or self.poll_seconds > 300:
            raise ValueError("poll_seconds must be between 1 and 300")
        if self.ca_file is not None and not self.ca_file.strip():
            raise ValueError("ca_file must be null or non-empty")

    def without_enrollment_token(self) -> AgentConfig:
        return replace(self, enrollment_token=None)


def load_config(path: Path) -> AgentConfig:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError("agent config must be a JSON object")
    values = cast(dict[str, Any], raw)
    required = {
        "control_plane_url",
        "agent_id",
        "run_id",
        "resource_identity",
        "enrollment_token",
        "fixture_image",
        "poll_seconds",
        "ca_file",
    }
    if set(values) != required:
        raise ValueError("agent config fields do not match version 1")
    if not isinstance(values["poll_seconds"], int):
        raise ValueError("poll_seconds must be an integer")
    for field in ("control_plane_url", "agent_id", "run_id", "resource_identity", "fixture_image"):
        if not isinstance(values[field], str):
            raise ValueError(f"{field} must be a string")
    for field in ("enrollment_token", "ca_file"):
        if values[field] is not None and not isinstance(values[field], str):
            raise ValueError(f"{field} must be a string or null")
    return AgentConfig(**values)


def save_config(path: Path, config: AgentConfig) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(asdict(config), stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
