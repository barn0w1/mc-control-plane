import json
from pathlib import Path

import pytest
from mccp_host_agent.config import AgentConfig, load_config, save_config

IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64


def _config(token: str | None = "enrollment-secret") -> AgentConfig:
    return AgentConfig(
        control_plane_url="https://control.example.test",
        agent_id="agent-1",
        run_id="run-1",
        resource_identity="resource-1",
        enrollment_token=token,
        fixture_image=IMAGE,
    )


def test_config_requires_https_and_digest_pinned_image() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        AgentConfig(
            control_plane_url="http://control.example.test",
            agent_id="agent-1",
            run_id="run-1",
            resource_identity="resource-1",
            enrollment_token="token",
            fixture_image=IMAGE,
        )
    with pytest.raises(ValueError, match="sha256"):
        AgentConfig(
            control_plane_url="https://control.example.test",
            agent_id="agent-1",
            run_id="run-1",
            resource_identity="resource-1",
            enrollment_token="token",
            fixture_image="docker.io/library/alpine:latest",
        )


def test_save_is_root_only_and_token_can_be_removed(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(path, _config())
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_config(path).enrollment_token == "enrollment-secret"

    save_config(path, _config().without_enrollment_token())
    assert load_config(path).enrollment_token is None
    assert "enrollment-secret" not in path.read_text()


def test_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(path, _config())
    value = json.loads(path.read_text())
    value["arbitrary_command"] = "no"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="fields"):
        load_config(path)
