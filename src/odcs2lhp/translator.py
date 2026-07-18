"""Translate a parsed ODCS contract into LHP sidecar artifacts.

Each schema object in a contract produces five :class:`Artifact` sidecars,
laid out under ``<stem>/<version>/<action_type>/<sidecar_type>/`` — grouped by
the LHP pipeline stage (action) that consumes each one:

- a **load** schema (cloudFiles read schema; columns named by ``physicalName``),
- a **transform** schema (``column_mapping`` + ``type_casting`` for a
  ``transform_type: schema`` action; ``_transform.yaml`` suffix),
- an **expectations** file, applied in the transform stage
  (``logicalTypeOptions`` predicates plus a NOT NULL check per ``required``
  property),
- a **write** schema (table_schema carrying per-column UC ``tags``),
- a **tags** file, applied in the write stage (table-level UC tags).

Load and transform schemas exclude the operational-metadata and SCD2 columns
(``exclude``): those are not sourced from the input data. The write schema
keeps every column (they are part of the written table).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional

from .mapper import (
    odcs_property_to_constraints,
    odcs_tags_to_uc,
    odcs_type_to_spark,
    slug,
)

_ARTIFACT_VERSION = "1.0"


@dataclass(frozen=True)
class Artifact:
    """A single sidecar file to write.

    :param relative_path: POSIX path relative to the output dir (``.lhp/odcs``),
        e.g. ``sales/1.0.0/load/schemas/customer_schema.yaml``.
    :param data: the YAML-serializable mapping to write.
    """

    relative_path: str
    data: Dict[str, Any]


def translate_contract(
    contract: Dict[str, Any],
    *,
    stem: str,
    exclude: FrozenSet[str] = frozenset(),
) -> List[Artifact]:
    """Translate every schema object in ``contract`` into its sidecar artifacts.

    :param contract: a parsed (and ODCS-valid) contract dict.
    :param stem: the source contract filename stem (collision-safe prefix).
    :param exclude: column names to omit from the load + transform schemas
        (operational-metadata + SCD2 columns).
    :raises Odcs2LhpError: for unmappable column types.
    """
    version = contract.get("version")
    artifacts: List[Artifact] = []
    for obj in contract.get("schema", []) or []:
        artifacts.extend(
            _translate_object(obj, stem=stem, version=version, exclude=exclude)
        )
    return artifacts


def _translate_object(
    obj: Dict[str, Any],
    *,
    stem: str,
    version: Optional[str],
    exclude: FrozenSet[str],
) -> List[Artifact]:
    object_name = obj["name"]
    properties = obj.get("properties", []) or []
    base = f"{stem}/{version or 'unversioned'}"
    name = slug(object_name)

    return [
        Artifact(
            f"{base}/load/schemas/{name}_schema.yaml",
            _load_schema(obj, object_name, version, properties, exclude),
        ),
        Artifact(
            f"{base}/transform/schemas/{name}_transform.yaml",
            _transform_schema(properties, exclude),
        ),
        Artifact(
            f"{base}/transform/expectations/{name}_expectations.yaml",
            _expectations_file(object_name, properties),
        ),
        Artifact(
            f"{base}/write/schemas/{name}_schema.yaml",
            _write_schema(obj, object_name, version, properties),
        ),
        Artifact(
            f"{base}/write/tags/{name}_tags.yaml",
            _tags_file(obj, object_name),
        ),
    ]


def _load_schema(
    obj: Dict[str, Any],
    object_name: str,
    version: Optional[str],
    properties: List[Dict[str, Any]],
    exclude: FrozenSet[str],
) -> Dict[str, Any]:
    """Cloud​Files read schema: columns named by ``physicalName``, OM/SCD2 dropped.

    Read from the raw source files, so each column uses its source (physical)
    name where declared. No UC tags here (those ride on the write schema).
    """
    columns: List[Dict[str, Any]] = []
    for prop in properties:
        if prop["name"] in exclude:
            continue
        source_name = prop.get("physicalName") or prop["name"]
        column: Dict[str, Any] = {
            "name": source_name,
            "type": odcs_type_to_spark(prop),
            "nullable": not prop.get("required", False),
        }
        if "description" in prop:
            column["comment"] = prop["description"]
        columns.append(column)

    schema: Dict[str, Any] = {"name": object_name, "version": version}
    if "description" in obj:
        schema["description"] = obj["description"]
    schema["columns"] = columns
    return schema


def _transform_schema(
    properties: List[Dict[str, Any]],
    exclude: FrozenSet[str],
) -> Dict[str, Any]:
    """Rename + cast mapping for a ``transform_type: schema`` action.

    ``column_mapping`` renames a source (physical) name to the contract name only
    when they differ; ``type_casting`` casts every kept column to its contract
    type. OM/SCD2 columns are skipped (they flow through untouched).
    """
    column_mapping: Dict[str, str] = {}
    type_casting: Dict[str, str] = {}
    for prop in properties:
        name = prop["name"]
        if name in exclude:
            continue
        source_name = prop.get("physicalName")
        if source_name and source_name != name:
            column_mapping[source_name] = name
        type_casting[name] = odcs_type_to_spark(prop)

    schema: Dict[str, Any] = {}
    if column_mapping:
        schema["column_mapping"] = column_mapping
    schema["type_casting"] = type_casting
    return schema


def _write_schema(
    obj: Dict[str, Any],
    object_name: str,
    version: Optional[str],
    properties: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Table schema for a write ``table_schema``: all columns, logical names, tags.

    Data has already been renamed/cast by the transform, so columns use their
    contract (logical) names. Per-column UC tags ride along (only when non-empty)
    and reach the UC tagging hook. ``primary_key`` is ordered by
    ``primaryKeyPosition``.
    """
    columns: List[Dict[str, Any]] = []
    for prop in properties:
        column: Dict[str, Any] = {
            "name": prop["name"],
            "type": odcs_type_to_spark(prop),
            "nullable": not prop.get("required", False),
        }
        if "description" in prop:
            column["comment"] = prop["description"]
        tags = odcs_tags_to_uc(prop)
        if tags:
            column["tags"] = tags
        columns.append(column)

    schema: Dict[str, Any] = {"name": object_name, "version": version}
    if "description" in obj:
        schema["description"] = obj["description"]
    schema["columns"] = columns

    pk_props = [p for p in properties if p.get("primaryKey") is True]
    if pk_props:
        pk_props.sort(key=lambda p: p.get("primaryKeyPosition", 0))
        schema["primary_key"] = [p["name"] for p in pk_props]

    return schema


def _tags_file(obj: Dict[str, Any], object_name: str) -> Dict[str, Any]:
    """Table-level UC tags file. Always written; ``tags: {}`` when none declared."""
    return {
        "version": _ARTIFACT_VERSION,
        "table": object_name,
        "tags": odcs_tags_to_uc(obj),
    }


def _expectations_file(
    object_name: str,
    properties: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Data-quality expectations: NOT NULL per ``required`` + ``logicalTypeOptions``.

    Each property contributes, in order, an ``<col> IS NOT NULL`` check when
    ``required: true`` followed by its ``logicalTypeOptions``-derived predicates.
    ``failureAction`` is ``fail`` for a ``criticalDataElement`` property, else
    ``warn``.
    """
    expectations: List[Dict[str, str]] = []
    for prop in properties:
        name = prop["name"]
        failure_action = "fail" if prop.get("criticalDataElement") else "warn"
        if prop.get("required"):
            expectations.append(
                {
                    "name": f"{name}_not_null",
                    "expression": f"{name} IS NOT NULL",
                    "failureAction": failure_action,
                }
            )
        for predicate, constraint_name in odcs_property_to_constraints(prop):
            expectations.append(
                {
                    "name": constraint_name,
                    "expression": predicate,
                    "failureAction": failure_action,
                }
            )

    return {
        "version": _ARTIFACT_VERSION,
        "table": object_name,
        "expectations": expectations,
    }
