"""Application identifier generation boundaries."""

from __future__ import annotations

import uuid

from uuid6 import uuid7


def new_uuid7() -> uuid.UUID:
    """Return an OPS-owned RFC 9562 UUIDv7."""
    return uuid7()
