"""Default implementations for deterministic application capabilities."""

from datetime import UTC, datetime
from uuid import uuid4


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    def new(self) -> str:
        return str(uuid4())
