import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest

from mc_control_plane.adapters.outbound.host import (
    DurableHostManager,
    DurableHostSettings,
    create_bootstrap_key,
    load_bootstrap_key,
)
from mc_control_plane.adapters.outbound.persistence import HostProtocolStore, SQLiteDatabase
from mc_control_plane.application.ports.host import HostBootstrapError
from mc_control_plane.domain.models import ResourceIdentity, Run, RuntimeSpec
from tests.fakes import MutableClock

IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64


def _wheel(tmp_path: Path) -> Path:
    wheel = tmp_path / "mccp_host_agent-0.1.1-py3-none-any.whl"
    wheel.write_bytes(b"test-wheel")
    return wheel


def test_bootstrap_is_reproducible_and_database_keeps_only_token_hash(
    tmp_path: Path,
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    store = HostProtocolStore(database)
    manager = DurableHostManager(
        store,
        DurableHostSettings("https://control.example.test", _wheel(tmp_path), IMAGE),
        b"k" * 32,
    )
    run = Run("run-1", "unit-1", RuntimeSpec("r", "t", "linode/debian13"), None, clock.now())
    identity = ResourceIdentity("main", "unit-1", "run-1")

    first = manager.metadata_for(run, identity, clock.now())
    second = manager.metadata_for(run, identity, clock.now())

    assert first == second
    encoded = next(
        line.split(": ", 1)[1] for line in first.splitlines() if line.startswith("    content: ")
    )
    config = json.loads(base64.b64decode(encoded))
    token = config["enrollment_token"]
    assert len(token) >= 32
    connection = database.connect()
    try:
        row = connection.execute("SELECT token_hash FROM host_enrollments").fetchone()
        assert row is not None
        assert row["token_hash"] != token
        assert connection.execute("SELECT COUNT(*) FROM host_enrollments").fetchone()[0] == 1
    finally:
        connection.close()


def test_bootstrap_key_is_exclusive_root_only_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "bootstrap.key"

    create_bootstrap_key(path)

    assert path.stat().st_mode & 0o777 == 0o600
    assert len(load_bootstrap_key(path)) == 32
    with pytest.raises(FileExistsError):
        create_bootstrap_key(path)
    path.chmod(0o640)
    with pytest.raises(ValueError, match="group or others"):
        load_bootstrap_key(path)


def test_unconsumed_derived_enrollment_expiry_can_be_extended(
    tmp_path: Path,
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    manager = DurableHostManager(
        HostProtocolStore(database),
        DurableHostSettings(
            "https://control.example.test",
            _wheel(tmp_path),
            IMAGE,
            enrollment_ttl=timedelta(minutes=10),
        ),
        b"z" * 32,
    )
    run = Run("run-2", "unit-1", RuntimeSpec("r", "t", "linode/debian13"), None, clock.now())
    identity = ResourceIdentity("main", "unit-1", "run-2")
    manager.metadata_for(run, identity, clock.now())
    clock.advance(timedelta(minutes=5))

    manager.metadata_for(run, identity, clock.now())

    connection = database.connect()
    try:
        expiry = connection.execute("SELECT expires_at FROM host_enrollments").fetchone()[0]
        assert expiry == (clock.now() + timedelta(minutes=10)).isoformat()
    finally:
        connection.close()


def test_bootstrap_key_cannot_change_during_an_active_run(
    tmp_path: Path,
    database: SQLiteDatabase,
    clock: MutableClock,
) -> None:
    wheel = _wheel(tmp_path)
    settings = DurableHostSettings("https://control.example.test", wheel, IMAGE)
    store = HostProtocolStore(database)
    run = Run("run-3", "unit-1", RuntimeSpec("r", "t", "linode/debian13"), None, clock.now())
    identity = ResourceIdentity("main", "unit-1", "run-3")
    DurableHostManager(store, settings, b"a" * 32).metadata_for(run, identity, clock.now())

    with pytest.raises(HostBootstrapError, match="bootstrap key changed"):
        DurableHostManager(store, settings, b"b" * 32).metadata_for(run, identity, clock.now())
