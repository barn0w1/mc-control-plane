from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from linode_api4.errors import ApiError
from requests.exceptions import Timeout

import mc_control_plane.adapters.outbound.compute.linode as linode_module
from mc_control_plane.adapters.outbound.compute import (
    LinodeComputeProvider,
    LinodeComputeSettings,
    map_linode_status,
)
from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeOwnershipMismatch,
    ComputeProviderUnavailable,
    ComputeRequestRejected,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
)
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec


@dataclass
class StubRegion:
    id: str


class StubInstance:
    def __init__(
        self,
        resource_id: int,
        *,
        status: str = "running",
        region: str = "us-ord",
        tags: set[str] | None = None,
        has_user_data: bool | None = None,
        backups_enabled: bool | None = None,
        firewall_ids: tuple[int, ...] = (),
    ) -> None:
        self.id = resource_id
        self.status = status
        self.region = StubRegion(region)
        self.tags = list(tags or set())
        self.has_user_data = has_user_data
        self.backups = type("Backups", (), {"enabled": backups_enabled})()
        self._firewalls = [type("FirewallRef", (), {"id": item})() for item in firewall_ids]
        self.deleted = False

    def delete(self) -> bool:
        self.deleted = True
        return True

    def firewalls(self) -> list[object]:
        return self._firewalls


class StubLinodeGroup:
    def __init__(self) -> None:
        self.items: list[StubInstance] = []
        self.create_result: StubInstance | Exception | None = None
        self.filters: tuple[Any, ...] = ()
        self.create_args: tuple[Any, ...] = ()
        self.create_kwargs: dict[str, Any] = {}

    def instances(self, *filters: Any) -> list[StubInstance]:
        self.filters = filters
        return self.items

    def instance_create(self, *args: Any, **kwargs: Any) -> StubInstance:
        self.create_args = args
        self.create_kwargs = kwargs
        if isinstance(self.create_result, Exception):
            raise self.create_result
        assert self.create_result is not None
        return self.create_result


class StubClient:
    def __init__(self) -> None:
        self.linode = StubLinodeGroup()
        self.loaded: StubInstance | Exception | None = None
        self.loaded_by_target: dict[str, object | Exception] = {}
        self.load_id: int | None = None

    def load(self, target: object, resource_id: int | str) -> object:
        self.load_id = resource_id if isinstance(resource_id, int) else None
        target_name = getattr(target, "__name__", str(target))
        if target_name in self.loaded_by_target:
            result = self.loaded_by_target[target_name]
            if isinstance(result, Exception):
                raise result
            return result
        if isinstance(self.loaded, Exception):
            raise self.loaded
        assert self.loaded is not None
        return self.loaded


@pytest.fixture
def identity() -> ResourceIdentity:
    return ResourceIdentity(system_id="main", server_unit_id="survival", run_id="run-1")


@pytest.fixture
def spec() -> RuntimeSpec:
    return RuntimeSpec(
        region="us-ord",
        instance_type="g6-standard-2",
        image="linode/ubuntu24.04",
        container_image="itzg/minecraft-server:latest",
        firewall_id="12345",
    )


@pytest.fixture
def client() -> StubClient:
    return StubClient()


@pytest.fixture
def provider(client: StubClient) -> LinodeComputeProvider:
    return LinodeComputeProvider(
        client,
        LinodeComputeSettings(authorized_keys=("ssh-ed25519 AAAA test",)),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("running", ComputeLifecycle.RUNNING),
        ("offline", ComputeLifecycle.STOPPED),
        ("stopped", ComputeLifecycle.STOPPED),
        ("booting", ComputeLifecycle.PENDING),
        ("busy", ComputeLifecycle.PENDING),
        ("rebooting", ComputeLifecycle.PENDING),
        ("shutting_down", ComputeLifecycle.PENDING),
        ("provisioning", ComputeLifecycle.PENDING),
        ("migrating", ComputeLifecycle.PENDING),
        ("rebuilding", ComputeLifecycle.PENDING),
        ("cloning", ComputeLifecycle.PENDING),
        ("restoring", ComputeLifecycle.PENDING),
        ("deleting", ComputeLifecycle.DELETING),
        ("billing_suspension", ComputeLifecycle.BLOCKED),
        ("future_status", ComputeLifecycle.UNKNOWN),
    ],
)
def test_status_mapping_covers_documented_values(raw: str, expected: ComputeLifecycle) -> None:
    assert map_linode_status(raw) is expected


def test_find_uses_scope_filters_and_defensively_checks_tags(
    provider: LinodeComputeProvider,
    client: StubClient,
    identity: ResourceIdentity,
) -> None:
    matching = StubInstance(1, tags=set(identity.tags))
    incomplete = StubInstance(2, tags={next(iter(identity.tags))})
    client.linode.items = [matching, incomplete]

    found = provider.find_by_server_unit(identity.system_id, identity.server_unit_id)

    assert [item.provider_resource_id for item in found] == ["1"]
    assert len(client.linode.filters) == 2
    assert all("+contains" in next(iter(item.dct.values())) for item in client.linode.filters)


def test_create_sends_owned_tags_authentication_and_existing_firewall(
    provider: LinodeComputeProvider,
    client: StubClient,
    identity: ResourceIdentity,
    spec: RuntimeSpec,
) -> None:
    client.linode.create_result = StubInstance(
        42,
        status="provisioning",
        tags=set(identity.tags),
    )

    observation = provider.create_runtime(
        RuntimeCreateRequest(
            identity=identity,
            spec=spec,
            metadata_user_data="#cloud-config\n",
            expires_at=datetime(2026, 7, 22, 12, 34, 56, tzinfo=UTC),
        )
    )

    assert observation.provider_resource_id == "42"
    assert observation.lifecycle is ComputeLifecycle.PENDING
    assert client.linode.create_args == ("g6-standard-2", "us-ord")
    assert client.linode.create_kwargs["image"] == "linode/ubuntu24.04"
    assert client.linode.create_kwargs["authorized_keys"] == ["ssh-ed25519 AAAA test"]
    assert client.linode.create_kwargs["interface_generation"] == "linode"
    assert client.linode.create_kwargs["interfaces"] == [
        {
            "firewall_id": 12345,
            "default_route": {"ipv4": True, "ipv6": True},
            "public": {},
        }
    ]
    assert "firewall" not in client.linode.create_kwargs
    assert set(client.linode.create_kwargs["tags"]) == set(identity.tags) | {
        "mccp:expires=20260722T123456Z"
    }
    assert client.linode.create_kwargs["metadata"] == {"user_data": "I2Nsb3VkLWNvbmZpZwo="}
    assert client.linode.create_kwargs["booted"] is True
    assert client.linode.create_kwargs["backups_enabled"] is False
    assert len(client.linode.create_kwargs["label"]) <= 64


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ApiError("invalid image", status=400), ComputeRequestRejected),
        (ApiError("provider failed", status=503), ComputeActionUncertain),
        (ApiError("request timed out", status=408), ComputeActionUncertain),
        (Timeout("response lost"), ComputeActionUncertain),
    ],
)
def test_create_classifies_provider_failures(
    provider: LinodeComputeProvider,
    client: StubClient,
    identity: ResourceIdentity,
    spec: RuntimeSpec,
    error: Exception,
    expected: type[Exception],
) -> None:
    client.linode.create_result = error

    with pytest.raises(expected):
        provider.create_runtime(RuntimeCreateRequest(identity=identity, spec=spec))


def test_observe_normalizes_not_found(
    provider: LinodeComputeProvider,
    client: StubClient,
) -> None:
    client.loaded = ApiError("missing", status=404)

    with pytest.raises(ComputeResourceNotFound):
        provider.observe_runtime("42")

    assert client.load_id == 42


def test_read_timeout_is_retryable(
    provider: LinodeComputeProvider,
    client: StubClient,
) -> None:
    client.loaded = Timeout("temporarily unavailable")

    with pytest.raises(ComputeProviderUnavailable):
        provider.observe_runtime("42")


def test_delete_of_already_absent_instance_is_idempotent(
    provider: LinodeComputeProvider,
    client: StubClient,
) -> None:
    client.loaded = ApiError("missing", status=404)

    identity = ResourceIdentity("main", "survival", "run-1")
    provider.delete_runtime("42", identity)


def test_delete_checks_exact_ownership_inside_adapter(
    provider: LinodeComputeProvider,
    client: StubClient,
    identity: ResourceIdentity,
) -> None:
    instance = StubInstance(42, tags={"unmanaged"})
    client.loaded = instance

    with pytest.raises(ComputeOwnershipMismatch):
        provider.delete_runtime("42", identity)

    assert instance.deleted is False


def test_delete_owned_instance(
    provider: LinodeComputeProvider,
    client: StubClient,
    identity: ResourceIdentity,
) -> None:
    instance = StubInstance(42, tags=set(identity.tags))
    client.loaded = instance

    provider.delete_runtime("42", identity)

    assert instance.deleted is True


def test_reads_firewall_ids_actually_attached_to_instance(
    provider: LinodeComputeProvider,
    client: StubClient,
) -> None:
    client.loaded = StubInstance(42, firewall_ids=(12345, 67890))

    attached = provider.attached_firewall_ids("42")

    assert attached == frozenset({"12345", "67890"})


@dataclass
class StubProviderRegion:
    id: str = "us-ord"
    status: str = "ok"
    capabilities: tuple[str, ...] = ("Linodes", "Metadata")


@dataclass
class StubProviderType:
    id: str = "g6-standard-2"


@dataclass
class StubProviderImage:
    id: str = "linode/debian13"
    status: str = "available"
    capabilities: tuple[str, ...] = ("cloud-init",)
    deprecated: bool = False


@dataclass
class StubProviderFirewall:
    id: int = 12345
    status: str = "enabled"


def _preflight_resources(client: StubClient) -> None:
    client.loaded_by_target = {
        "Region": StubProviderRegion(),
        "Type": StubProviderType(),
        "Image": StubProviderImage(),
        "Firewall": StubProviderFirewall(),
    }


def test_preflight_validates_region_image_type_and_firewall(
    provider: LinodeComputeProvider,
    client: StubClient,
    spec: RuntimeSpec,
) -> None:
    _preflight_resources(client)

    report = provider.validate_runtime_spec(spec)

    assert report.region == "us-ord"
    assert report.instance_type == "g6-standard-2"
    assert report.firewall_id == "12345"
    assert report.metadata_supported is True


def test_preflight_rejects_region_without_metadata(
    provider: LinodeComputeProvider,
    client: StubClient,
    spec: RuntimeSpec,
) -> None:
    _preflight_resources(client)
    client.loaded_by_target["Region"] = StubProviderRegion(capabilities=("Linodes",))

    with pytest.raises(ComputeRequestRejected, match="Metadata"):
        provider.validate_runtime_spec(spec)


def test_preflight_requires_explicit_positive_firewall(
    provider: LinodeComputeProvider,
    spec: RuntimeSpec,
) -> None:
    missing = RuntimeSpec(
        region=spec.region,
        instance_type=spec.instance_type,
        image=spec.image,
        container_image=spec.container_image,
        firewall_id=None,
    )
    invalid = RuntimeSpec(
        region=spec.region,
        instance_type=spec.instance_type,
        image=spec.image,
        container_image=spec.container_image,
        firewall_id="-1",
    )

    with pytest.raises(ComputeRequestRejected, match="explicit existing firewall"):
        provider.validate_runtime_spec(missing)
    with pytest.raises(ComputeRequestRejected, match="positive integer"):
        provider.validate_runtime_spec(invalid)


def test_settings_require_an_ssh_key() -> None:
    with pytest.raises(ValueError, match="SSH key"):
        LinodeComputeSettings(authorized_keys=())


def test_factory_disables_sdk_retry_and_installs_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FactoryClient:
        session: Any = None

    factory_client = FactoryClient()

    def client_factory(token: str, **kwargs: Any) -> FactoryClient:
        captured["token"] = token
        captured.update(kwargs)
        return factory_client

    monkeypatch.setattr(linode_module, "LinodeClient", client_factory)
    settings = LinodeComputeSettings(
        authorized_keys=("ssh-ed25519 AAAA test",),
        connect_timeout_seconds=2.0,
        read_timeout_seconds=7.0,
    )

    created = LinodeComputeProvider.from_token("secret", settings)

    assert isinstance(created, LinodeComputeProvider)
    assert captured == {
        "token": "secret",
        "retry": False,
        "user_agent": "mc-control-plane",
    }
    assert factory_client.session._default_timeout == (2.0, 7.0)
