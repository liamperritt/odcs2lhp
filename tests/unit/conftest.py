"""Shared fixtures and helpers for the odcs2lhp test suite.

Tests use only the public API of odcs2lhp; these helpers just locate fixture
files and parse emitted YAML back into dicts for structural assertions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory containing the sample ODCS contracts and lhp.yaml."""
    return FIXTURES


@pytest.fixture
def sales_contract_path() -> Path:
    """Single-object contract with physicalName, tags, options, OM + SCD2 cols."""
    return FIXTURES / "sales.contract.yaml"


@pytest.fixture
def multi_contract_path() -> Path:
    """Two-object contract (orders + products)."""
    return FIXTURES / "multi.odcs.yaml"


@pytest.fixture
def broken_contract_path() -> Path:
    """A file that fails ODCS schema validation."""
    return FIXTURES / "broken.contract.yaml"


def load_yaml(path: Path) -> Dict[str, Any]:
    """Parse a written sidecar file back into a dict for assertions."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
