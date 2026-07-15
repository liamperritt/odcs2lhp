"""Discover the project layout that odcs2lhp reads.

odcs2lhp inspects only two things in a project: the ``lhp.yaml`` project config
(for the operational-metadata column names) and the ODCS contract files. It never
reads pipeline YAMLs or any other files.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

# SCD2 bookkeeping columns are injected by LHP at write time, never sourced from
# the input data, so they must not appear in the cloudFiles read schema or the
# schema transform. Always excluded, regardless of lhp.yaml.
SCD2_COLUMNS = frozenset({"__START_AT", "__END_AT"})


def find_project_root(start: Path) -> Optional[Path]:
    """Return the nearest ancestor of ``start`` containing ``lhp.yaml``.

    Walks ``start`` (resolved) and all its parents. Returns ``None`` when no
    ``lhp.yaml`` marker is found, so callers can still run with an empty
    operational-metadata set.
    """
    start = Path(start).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "lhp.yaml").is_file():
            return candidate
    return None


def read_operational_metadata_columns(project_root: Optional[Path]) -> frozenset[str]:
    """Read ``operational_metadata.columns`` names from ``<root>/lhp.yaml``.

    Returns an empty set when ``project_root`` is ``None``, ``lhp.yaml`` is
    absent/empty, or no operational-metadata columns are declared. Only the
    column *names* (the mapping keys) are needed.
    """
    if project_root is None:
        return frozenset()

    config_file = project_root / "lhp.yaml"
    if not config_file.is_file():
        return frozenset()

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return frozenset()

    operational_metadata = data.get("operational_metadata")
    if not isinstance(operational_metadata, dict):
        return frozenset()

    columns = operational_metadata.get("columns")
    if not isinstance(columns, dict):
        return frozenset()

    return frozenset(str(name) for name in columns.keys())


def exclusion_columns(project_root: Optional[Path]) -> frozenset[str]:
    """The full set of columns excluded from load + transform schemas.

    Operational-metadata columns declared in ``lhp.yaml`` plus the always-excluded
    SCD2 columns (:data:`SCD2_COLUMNS`).
    """
    return read_operational_metadata_columns(project_root) | SCD2_COLUMNS


def discover_contracts(contracts_dir: Path) -> List[Path]:
    """Return the sorted ``.yaml``/``.yml`` contract files under ``contracts_dir``.

    Recurses into subdirectories. Returns an empty list when the directory does
    not exist.
    """
    contracts_dir = Path(contracts_dir)
    if not contracts_dir.is_dir():
        return []

    found = {
        path
        for pattern in ("*.yaml", "*.yml")
        for path in contracts_dir.rglob(pattern)
        if path.is_file()
    }
    return sorted(found)


def contract_stem(contract_path: Path) -> str:
    """Derive the sidecar filename stem from a contract filename.

    Splits the filename on the **first** dot so multi-suffix names collapse to
    their leading segment: ``sales.contract.yaml`` -> ``sales``,
    ``orders.odcs.yaml`` -> ``orders``, ``customer.yaml`` -> ``customer``.
    """
    return Path(contract_path).name.split(".", 1)[0]
