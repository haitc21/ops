from __future__ import annotations

import pytest
from pydantic import ValidationError

from ops.config import Settings


def test_catalog_enrichment_call_budget_defaults_and_bounds() -> None:
    assert Settings(_env_file=None).catalog_enrichment_max_calls == 256
    for value in (0, 4097):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, catalog_enrichment_max_calls=value)
