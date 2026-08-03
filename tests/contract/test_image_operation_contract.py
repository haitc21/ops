"""Pinned image command contract keeps CPS' no-bytes boundary."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ops.contracts.messages.image_operations import ImageOperationRequest


def test_pinned_image_import_contract_rejects_query_credentials_and_bytes() -> None:
    with pytest.raises(ValidationError):
        ImageOperationRequest(
            operation_id=uuid4(),
            provider_connection_id=uuid4(),
            operation="import_url",
            name="cmp-image",
            disk_format="qcow2",
            source_url="https://images.example.test/cmp.qcow2?signature=unsafe",
        )
    with pytest.raises(ValidationError):
        ImageOperationRequest(
            operation_id=uuid4(),
            provider_connection_id=uuid4(),
            operation="patch_metadata",
            provider_resource_id="image-1",
            metadata={"bytes": "AAAA"},
        )
