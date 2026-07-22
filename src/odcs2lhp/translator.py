"""Translate a parsed ODCS contract into LHP sidecar artifacts.

Each schema object in a contract produces six :class:`Artifact` sidecars,
laid out under ``<contract-path-prefix>/<action_type>/<sidecar_type>/`` (the prefix
mirrors the contract file's location + name, e.g. ``marketing/sales.contract``) —
grouped by the LHP pipeline stage (action) that consumes each one:

- a **load** schema (cloudFiles read schema; columns named by ``physicalName``),
- a **transform** schema (``column_mapping`` + ``type_casting`` for a
  ``transform_type: schema`` action; ``_transform.yaml`` suffix),
- a **type-convert** Python module (``transform/python/<obj>_convert.py``), applied
  by a ``transform_type: python`` action, that parses string-encoded values a plain
  cast cannot: ``to_date``/``to_timestamp`` (format-aware, timezone-aware),
  ``from_json`` (string→struct/array), ``parse_json`` (string→variant), and
  ``unbase64`` (base64 string→binary). Always emitted; a passthrough (``return df``)
  when the object has no such columns,
- an **expectations** file, applied in the transform stage
  (``logicalTypeOptions`` predicates plus a NOT NULL check per ``required``
  property),
- a **write** schema (table_schema; columns carry no UC tags),
- a **uc_tags** file, applied in the write stage, carrying both table-level UC tags
  (contract-level tags form the base for every table, with an object-level tag of the
  same key overriding the contract value) and a per-column ``columns`` list of
  ``{name, tags}`` entries.

Load and transform schemas exclude the operational-metadata and SCD2 columns
(``exclude``): those are not sourced from the input data. The write schema
keeps every column (they are part of the written table).

A converted column's type is split across the sidecars: the **load** schema keeps
its raw string type (``from_json``/``unbase64`` consume a string), the type-convert
module produces the parsed value, and the **write** schema records the final parsed
type. Such columns are dropped from the transform schema's ``type_casting`` so a
plain cast never fights the runtime parse. The type-convert module is meant to run
on the raw load (before the schema transform renames columns), so it references
each column by its source ``physicalName``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional

from .errors import Odcs2LhpError
from .mapper import (
    odcs_property_to_constraints,
    odcs_tags_to_uc,
    odcs_type_to_spark,
    path_segment,
    quote_identifier,
    sanitize_name,
    slug,
    string_conversion,
)
from .template_renderer import TYPE_CONVERT_FUNCTION, TemplateRenderer

_ARTIFACT_VERSION = "1.0"

# Shared across all objects so the Jinja environment/template compile once.
_RENDERER = TemplateRenderer()


@dataclass(frozen=True)
class Artifact:
    """A single sidecar file to write.

    :param relative_path: POSIX path relative to the output dir (``.lhp/odcs``),
        e.g. ``marketing/sales.contract/load/schemas/customer_schema.yaml``.
    :param data: the YAML-serializable mapping to write (for YAML sidecars).
    :param text: pre-rendered file contents written verbatim (for non-YAML
        sidecars such as generated ``.py`` transform modules). When set, the
        writer emits ``text`` as-is and ignores ``data``.
    """

    relative_path: str
    data: Dict[str, Any]
    text: Optional[str] = None


def assert_unique_relative_paths(artifacts: List[Artifact]) -> None:
    """Raise if two artifacts target the same output path (would silently overwrite).

    Guards against schema objects (within or across contracts) whose sanitized
    names collide onto one path.
    """
    seen: set[str] = set()
    for artifact in artifacts:
        if artifact.relative_path in seen:
            raise Odcs2LhpError(
                "ODCS-PATH-002",
                f"Two artifacts map to the same output path "
                f"{artifact.relative_path!r}; outputs would overwrite each other.",
                suggestions=[
                    "Rename the colliding schema object(s), or the contract "
                    "file(s), so their sanitized names differ.",
                ],
            )
        seen.add(artifact.relative_path)


def translate_contract(
    contract: Dict[str, Any],
    *,
    prefix: str,
    exclude: FrozenSet[str] = frozenset(),
) -> List[Artifact]:
    """Translate every schema object in ``contract`` into its sidecar artifacts.

    :param contract: a parsed (and ODCS-valid) contract dict.
    :param prefix: the output-path prefix for this contract (its location under the
        contracts dir plus its filename without extension, e.g.
        ``marketing/sales.contract``); each ``/``-segment is sanitized.
    :param exclude: column names to omit from the load + transform schemas
        (operational-metadata + SCD2 columns).
    :raises Odcs2LhpError: on a duplicate schema-object name (``ODCS-OBJ-001``), an
        unsafe path segment (``ODCS-PATH-001``), a column without ``physicalType``
        (``ODCS-TYPE-001``), or colliding artifact paths (``ODCS-PATH-002``).
    """
    version = contract.get("version")
    # Contract-level tags are the base for every table's tags file; an
    # object-level tag of the same key overrides the contract value.
    contract_tags = odcs_tags_to_uc(contract)
    base = "/".join(
        path_segment(part, field="contract path") for part in prefix.split("/")
    )
    artifacts: List[Artifact] = []
    seen_objects: set[str] = set()
    for obj in contract.get("schema", []) or []:
        name = obj["name"]
        if name in seen_objects:
            raise Odcs2LhpError(
                "ODCS-OBJ-001",
                f"Duplicate schema object name {name!r} in contract; names must "
                "be unique.",
                suggestions=["Give each schema object a unique 'name'."],
            )
        seen_objects.add(name)
        artifacts.extend(
            _translate_object(
                obj,
                base=base,
                version=version,
                exclude=exclude,
                contract_tags=contract_tags,
            )
        )
    assert_unique_relative_paths(artifacts)
    return artifacts


def _translate_object(
    obj: Dict[str, Any],
    *,
    base: str,
    version: Optional[str],
    exclude: FrozenSet[str],
    contract_tags: Dict[str, str],
) -> List[Artifact]:
    object_name = obj["name"]
    properties = obj.get("properties", []) or []
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
            f"{base}/transform/python/{name}_convert.py",
            {},
            text=_type_convert_module(obj, object_name, properties, exclude),
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
            f"{base}/write/uc_tags/{name}_tags.yaml",
            _tags_file(obj, object_name, contract_tags, properties),
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
    name where declared. No UC tags here (those ride on the write schema). A
    column that the type-convert module parses at runtime keeps its raw *string*
    source type here — the parse (``from_json``/``unbase64``/format-aware temporal)
    consumes a string, so the read must not pre-declare the parsed type.
    """
    columns: List[Dict[str, Any]] = []
    for prop in properties:
        if prop["name"] in exclude:
            continue
        source_name = prop.get("physicalName") or prop["name"]
        conversion = string_conversion(prop)
        column: Dict[str, Any] = {
            "name": source_name,
            "type": conversion.source_type if conversion else odcs_type_to_spark(prop),
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
    type. OM/SCD2 columns are skipped (they flow through untouched). Columns the
    type-convert module handles are still renamed but omitted from
    ``type_casting`` — that module owns their (non-cast) typing, so a plain cast
    here would fight the runtime parse.
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
        if string_conversion(prop) is None:
            type_casting[name] = odcs_type_to_spark(prop)

    schema: Dict[str, Any] = {}
    if column_mapping:
        schema["column_mapping"] = column_mapping
    schema["type_casting"] = type_casting
    return schema


def _type_convert_module(
    obj: Dict[str, Any],
    object_name: str,
    properties: List[Dict[str, Any]],
    exclude: FrozenSet[str],
) -> str:
    """Render the ``transform_type: python`` type-conversion module for an object.

    Collects the string→typed-value conversions for every kept column (OM/SCD2
    excluded) and renders them into a per-object module. Always returns a module;
    with no conversions it is a passthrough (``return df``).
    """
    conversions = [
        conversion
        for prop in properties
        if prop["name"] not in exclude
        and (conversion := string_conversion(prop)) is not None
    ]
    description = obj.get("description") or f"Type conversions for {object_name}"
    return _RENDERER.render_type_convert(
        object_name=object_name,
        description=description,
        conversions=conversions,
        function_name=TYPE_CONVERT_FUNCTION,
    )


def _write_schema(
    obj: Dict[str, Any],
    object_name: str,
    version: Optional[str],
    properties: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Table schema for a write ``table_schema``: all columns, logical names.

    Data has already been renamed/cast by the transform, so columns use their
    contract (logical) names. UC tags no longer ride on the schema columns — they
    live in the ``uc_tags`` file (see :func:`_tags_file`). ``primary_key`` is
    ordered by ``primaryKeyPosition``. A converted column records its parsed
    target type (e.g. ``TIMESTAMP``/``VARIANT``/``BINARY``) so the written table
    matches the type-convert module's output rather than the raw string source.
    """
    columns: List[Dict[str, Any]] = []
    for prop in properties:
        conversion = string_conversion(prop)
        column: Dict[str, Any] = {
            "name": prop["name"],
            "type": conversion.target_type if conversion else odcs_type_to_spark(prop),
            "nullable": not (
                prop.get("required", False) or prop.get("primaryKey") is True
            ),
        }
        if "description" in prop:
            column["comment"] = prop["description"]
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


def _tags_file(
    obj: Dict[str, Any],
    object_name: str,
    contract_tags: Dict[str, str],
    properties: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """UC tags file: table-level ``tags`` plus a per-column ``columns`` list.

    Always written. ``contract_tags`` are the base applied to every table; an
    object-level tag of the same key overrides the contract value (object wins).
    ``columns`` carries one ``{name, tags}`` entry per property (in declaration
    order, using the contract/logical name), with ``tags: {}`` when the property
    declares none — mirroring the write schema's column set.
    """
    return {
        "version": _ARTIFACT_VERSION,
        "table": object_name,
        "tags": {**contract_tags, **odcs_tags_to_uc(obj)},
        "columns": [
            {"name": prop["name"], "tags": odcs_tags_to_uc(prop)}
            for prop in properties
        ],
    }


def _expectations_file(
    object_name: str,
    properties: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Data-quality expectations: NOT NULL per ``required`` + ``logicalTypeOptions``.

    Each property contributes, in order, a ``<col> IS NOT NULL`` check when
    ``required: true`` followed by its ``logicalTypeOptions``-derived predicates.
    The column name is backtick-quoted inside the expression (names may contain
    spaces/special characters); the expectation ``name`` uses the column name
    sanitized to an identifier-safe form (:func:`odcs2lhp.mapper.sanitize_name`).
    Because sanitizing is not injective, names that would collide are made unique
    by appending ``_<n>`` (n>=2). ``failureAction`` is ``fail`` for a
    ``criticalDataElement`` property, else ``warn``.
    """
    used: set[str] = set()

    def _unique(base_name: str) -> str:
        candidate, n = base_name, 2
        while candidate in used:
            candidate, n = f"{base_name}_{n}", n + 1
        used.add(candidate)
        return candidate

    expectations: List[Dict[str, str]] = []
    for prop in properties:
        name = prop["name"]
        failure_action = "fail" if prop.get("criticalDataElement") else "warn"
        if prop.get("required"):
            expectations.append(
                {
                    "name": _unique(f"{sanitize_name(name)}_not_null"),
                    "expression": f"{quote_identifier(name)} IS NOT NULL",
                    "failureAction": failure_action,
                }
            )
        for predicate, constraint_name in odcs_property_to_constraints(prop):
            expectations.append(
                {
                    "name": _unique(constraint_name),
                    "expression": predicate,
                    "failureAction": failure_action,
                }
            )

    return {
        "version": _ARTIFACT_VERSION,
        "table": object_name,
        "expectations": expectations,
    }
