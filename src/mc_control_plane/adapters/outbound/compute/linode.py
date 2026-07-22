"""Akamai Cloud Compute adapter backed by the official Linode Python SDK."""

from base64 import b64encode
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC
from hashlib import blake2s
from typing import Any, TypeVar, cast

from linode_api4 import (  # type: ignore[import-untyped]
    Firewall,
    Image,
    Instance,
    LinodeClient,
    Region,
    Type,
)
from linode_api4.errors import (  # type: ignore[import-untyped]
    ApiError,
    UnexpectedResponseError,
)
from requests import Response, Session
from requests.exceptions import RequestException

from mc_control_plane.application.ports.compute import (
    ComputeActionUncertain,
    ComputeLifecycle,
    ComputeOwnershipMismatch,
    ComputeProviderUnavailable,
    ComputeRequestRejected,
    ComputeResourceNotFound,
    RuntimeCreateRequest,
    RuntimeObservation,
)
from mc_control_plane.domain.models import ResourceIdentity, RuntimeSpec, resource_scope_tags

_T = TypeVar("_T")
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429})

_PENDING_STATUSES = frozenset(
    {
        "booting",
        "busy",
        "rebooting",
        "shutting_down",
        "provisioning",
        "migrating",
        "rebuilding",
        "cloning",
        "restoring",
    }
)


def map_linode_status(status: str) -> ComputeLifecycle:
    """Normalize every status currently documented by the Linode API."""
    if status == "running":
        return ComputeLifecycle.RUNNING
    if status in {"offline", "stopped"}:
        return ComputeLifecycle.STOPPED
    if status in _PENDING_STATUSES:
        return ComputeLifecycle.PENDING
    if status == "deleting":
        return ComputeLifecycle.DELETING
    if status == "billing_suspension":
        return ComputeLifecycle.BLOCKED
    return ComputeLifecycle.UNKNOWN


@dataclass(frozen=True, slots=True)
class LinodeComputeSettings:
    """Account-level, non-domain inputs required to create a usable VM."""

    authorized_keys: tuple[str, ...]
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.authorized_keys:
            raise ValueError("at least one authorized SSH key is required")
        if any(not key.strip() for key in self.authorized_keys):
            raise ValueError("authorized SSH keys must not be empty")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("Linode API timeouts must be positive")


@dataclass(frozen=True, slots=True)
class LinodeRuntimePreflight:
    """Provider facts checked before a billable create request."""

    region: str
    instance_type: str
    image: str
    firewall_id: str | None
    metadata_supported: bool


class _TimeoutSession(Session):
    def __init__(self, connect_timeout: float, read_timeout: float) -> None:
        super().__init__()
        self._default_timeout = (connect_timeout, read_timeout)

    # requests exposes every keyword explicitly in its type signature. Keeping
    # **kwargs preserves compatibility as that public signature evolves.
    def request(self, method: str, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(method, url, **kwargs)


class LinodeComputeProvider:
    """Translate the provider-independent compute port to Linode API calls."""

    provider_name = "akamai-linode"

    def __init__(self, client: object, settings: LinodeComputeSettings) -> None:
        self._client = client
        self._settings = settings

    @classmethod
    def from_token(
        cls,
        token: str,
        settings: LinodeComputeSettings,
    ) -> LinodeComputeProvider:
        if not token.strip():
            raise ValueError("Linode API token must not be empty")
        # SDK retries include POST by default. Mutations must instead return to
        # the durable discovery step so a lost response cannot create two VMs.
        client = LinodeClient(token, retry=False, user_agent="mc-control-plane")
        client.session = _TimeoutSession(
            settings.connect_timeout_seconds,
            settings.read_timeout_seconds,
        )
        return cls(client, settings)

    def find_by_server_unit(
        self,
        system_id: str,
        server_unit_id: str,
    ) -> Sequence[RuntimeObservation]:
        required_tags = resource_scope_tags(system_id, server_unit_id)

        def find() -> list[RuntimeObservation]:
            client = cast(Any, self._client)
            filters = tuple(Instance.tags.contains(tag) for tag in sorted(required_tags))
            instances: Iterable[object] = client.linode.instances(*filters)
            observations = [self._observation(instance) for instance in instances]
            # Keep ownership matching correct even if provider filter semantics change.
            return [item for item in observations if required_tags.issubset(item.tags)]

        return self._read(find)

    def create_runtime(self, request: RuntimeCreateRequest) -> RuntimeObservation:
        firewall = self._firewall_id(request.spec.firewall_id)

        def create() -> RuntimeObservation:
            client = cast(Any, self._client)
            kwargs: dict[str, object] = {
                "image": request.spec.image,
                "authorized_keys": list(self._settings.authorized_keys),
                "label": self._label(request),
                "tags": sorted(self._create_tags(request)),
                "booted": True,
                "backups_enabled": False,
            }
            if firewall is not None:
                kwargs["interface_generation"] = "linode"
                kwargs["interfaces"] = [
                    {
                        "firewall_id": firewall,
                        "default_route": {"ipv4": True, "ipv6": True},
                        "public": {},
                    }
                ]
            if request.metadata_user_data is not None:
                kwargs["metadata"] = {
                    "user_data": b64encode(request.metadata_user_data.encode()).decode()
                }
            instance = client.linode.instance_create(
                request.spec.instance_type,
                request.spec.region,
                **kwargs,
            )
            return self._observation(instance)

        try:
            return create()
        except ApiError as error:
            if self._is_transient(error.status):
                raise ComputeActionUncertain(self._error_message(error)) from error
            raise ComputeRequestRejected(self._error_message(error)) from error
        except (UnexpectedResponseError, RequestException) as error:
            raise ComputeActionUncertain(self._error_message(error)) from error

    def observe_runtime(self, provider_resource_id: str) -> RuntimeObservation:
        resource_id = self._resource_id(provider_resource_id)

        def observe() -> RuntimeObservation:
            client = cast(Any, self._client)
            return self._observation(client.load(Instance, resource_id))

        return self._read(observe, resource_id=provider_resource_id)

    def delete_runtime(
        self,
        provider_resource_id: str,
        identity: ResourceIdentity,
    ) -> None:
        resource_id = self._resource_id(provider_resource_id)

        try:
            client = cast(Any, self._client)
            instance = client.load(Instance, resource_id)
            observation = self._observation(instance)
            if not identity.owns(observation.tags):
                raise ComputeOwnershipMismatch(provider_resource_id)
            instance.delete()
        except ApiError as error:
            if error.status == 404:
                return
            if self._is_transient(error.status):
                raise ComputeActionUncertain(self._error_message(error)) from error
            raise ComputeRequestRejected(self._error_message(error)) from error
        except (UnexpectedResponseError, RequestException) as error:
            raise ComputeActionUncertain(self._error_message(error)) from error

    def validate_runtime_spec(
        self,
        spec: RuntimeSpec,
        *,
        require_metadata: bool = True,
        require_firewall: bool = True,
    ) -> LinodeRuntimePreflight:
        """Validate stable provider configuration without creating resources.

        This intentionally does not promise capacity. Capacity can change after
        the check, so the create response remains authoritative.
        """

        firewall_id = self._firewall_id(spec.firewall_id)
        if require_firewall and firewall_id is None:
            raise ComputeRequestRejected("an explicit existing firewall is required")

        def validate() -> LinodeRuntimePreflight:
            client = cast(Any, self._client)
            region = client.load(Region, spec.region)
            region_status = self._string_value(region.status)
            region_capabilities = self._string_set(region.capabilities)
            if region_status != "ok":
                raise ComputeRequestRejected(
                    f"Linode region {spec.region!r} is not operational: {region_status!r}"
                )
            if "Linodes" not in region_capabilities:
                raise ComputeRequestRejected(
                    f"Linode region {spec.region!r} does not support Linodes"
                )
            if require_metadata and "Metadata" not in region_capabilities:
                raise ComputeRequestRejected(
                    f"Linode region {spec.region!r} does not support Metadata"
                )

            instance_type = client.load(Type, spec.instance_type)
            if str(instance_type.id) != spec.instance_type:
                raise ComputeRequestRejected(
                    f"Linode type resolved unexpectedly: {instance_type.id!r}"
                )

            image = client.load(Image, spec.image)
            image_status = self._string_value(image.status)
            image_capabilities = self._string_set(image.capabilities)
            if image_status != "available":
                raise ComputeRequestRejected(
                    f"Linode image {spec.image!r} is not available: {image_status!r}"
                )
            if bool(image.deprecated):
                raise ComputeRequestRejected(f"Linode image {spec.image!r} is deprecated")
            if require_metadata and "cloud-init" not in image_capabilities:
                raise ComputeRequestRejected(
                    f"Linode image {spec.image!r} does not support cloud-init"
                )

            if firewall_id is not None:
                firewall = client.load(Firewall, firewall_id)
                firewall_status = self._string_value(firewall.status)
                if firewall_status != "enabled":
                    raise ComputeRequestRejected(
                        f"Linode firewall {firewall_id} is not enabled: {firewall_status!r}"
                    )

            return LinodeRuntimePreflight(
                region=spec.region,
                instance_type=spec.instance_type,
                image=spec.image,
                firewall_id=str(firewall_id) if firewall_id is not None else None,
                metadata_supported=require_metadata,
            )

        return self._read(validate)

    def attached_firewall_ids(self, provider_resource_id: str) -> frozenset[str]:
        """Return firewall IDs actually attached to one Linode."""

        resource_id = self._resource_id(provider_resource_id)

        def read_firewalls() -> frozenset[str]:
            client = cast(Any, self._client)
            instance = client.load(Instance, resource_id)
            return frozenset(str(firewall.id) for firewall in instance.firewalls())

        return self._read(read_firewalls, resource_id=provider_resource_id)

    def _read(
        self,
        action: Callable[[], _T],
        *,
        resource_id: str | None = None,
    ) -> _T:
        try:
            return action()
        except ApiError as error:
            if error.status == 404 and resource_id is not None:
                raise ComputeResourceNotFound(resource_id) from error
            if not self._is_transient(error.status):
                raise ComputeRequestRejected(self._error_message(error)) from error
            raise ComputeProviderUnavailable(self._error_message(error)) from error
        except (UnexpectedResponseError, RequestException) as error:
            raise ComputeProviderUnavailable(self._error_message(error)) from error

    def _observation(self, instance: object) -> RuntimeObservation:
        raw = cast(Any, instance)
        status_value = getattr(raw.status, "value", raw.status)
        region_value = getattr(raw.region, "id", raw.region)
        backups = getattr(raw, "backups", None)
        backups_enabled = getattr(backups, "enabled", None)
        return RuntimeObservation(
            provider_resource_id=str(raw.id),
            provider=self.provider_name,
            region=str(region_value),
            raw_status=str(status_value),
            lifecycle=map_linode_status(str(status_value)),
            tags=frozenset(str(tag) for tag in raw.tags),
            has_user_data=(
                bool(raw.has_user_data) if getattr(raw, "has_user_data", None) is not None else None
            ),
            backups_enabled=(bool(backups_enabled) if backups_enabled is not None else None),
        )

    @staticmethod
    def _resource_id(provider_resource_id: str) -> int:
        try:
            return int(provider_resource_id)
        except ValueError as error:
            raise ComputeRequestRejected(
                f"invalid Linode resource id: {provider_resource_id!r}"
            ) from error

    @staticmethod
    def _firewall_id(firewall_id: str | None) -> int | None:
        if firewall_id is None:
            return None
        try:
            parsed = int(firewall_id)
        except ValueError as error:
            raise ComputeRequestRejected(f"invalid Linode firewall id: {firewall_id!r}") from error
        if parsed <= 0:
            raise ComputeRequestRejected(
                f"Linode firewall id must be a positive integer: {firewall_id!r}"
            )
        return parsed

    @staticmethod
    def _create_tags(request: RuntimeCreateRequest) -> frozenset[str]:
        if request.expires_at is None:
            return request.identity.tags
        expiration = request.expires_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return request.identity.tags | {f"mccp:expires={expiration}"}

    @staticmethod
    def _string_value(value: object) -> str:
        return str(getattr(value, "value", value))

    @classmethod
    def _string_set(cls, values: Iterable[object]) -> frozenset[str]:
        return frozenset(cls._string_value(value) for value in values)

    @staticmethod
    def _label(request: RuntimeCreateRequest) -> str:
        unit = blake2s(request.identity.server_unit_id.encode(), digest_size=6).hexdigest()
        run = blake2s(request.identity.run_id.encode(), digest_size=6).hexdigest()
        return f"mccp-{unit}-{run}"

    @staticmethod
    def _error_message(error: Exception) -> str:
        return str(error)[:500] or type(error).__name__

    @staticmethod
    def _is_transient(status: int) -> bool:
        return status in _TRANSIENT_HTTP_STATUSES or status >= 500
