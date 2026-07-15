"""Pure ODCS-property mappers.

These functions operate on plain ODCS dicts and return strings / dicts / tuples:

- :func:`odcs_type_to_spark` -> a Spark/Databricks DDL type string.
- :func:`odcs_tags_to_uc` -> a Unity Catalog tag mapping from an ODCS ``tags``
  array (present on both schema objects and properties).
- :func:`odcs_property_to_constraints` -> row-level ``(predicate, name)`` pairs
  derived from a property's ``logicalTypeOptions``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .errors import Odcs2LhpError

# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------


def slug(name: str) -> str:
    """Replace filesystem-unsafe characters in an object name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


# ---------------------------------------------------------------------------
# Type mapping  (ODCS property -> Spark DDL type)
# ---------------------------------------------------------------------------

# Simple ODCS logical types -> Spark DDL type strings.
_SIMPLE_LOGICAL = {
    "string": "STRING",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "time": "STRING",
}


def _unmappable(prop: Dict[str, Any]) -> Odcs2LhpError:
    logical = prop.get("logicalType")
    return Odcs2LhpError(
        "ODCS-TYPE-001",
        (
            "Could not map ODCS property to a Spark type. Neither a usable "
            f"'physicalType' nor a recognised 'logicalType' was found "
            f"(logicalType={logical!r})."
        ),
        suggestions=[
            "Provide an explicit 'physicalType' (e.g. STRING, BIGINT, DECIMAL(18,2))",
            "Use a supported logicalType: string, integer, number, boolean, "
            "date, timestamp, time, object, array",
        ],
    )


def odcs_type_to_spark(prop: Dict[str, Any]) -> str:
    """Resolve an ODCS schema property to a Spark DDL type string.

    Resolution order:

    1. If ``physicalType`` is present, return it verbatim.
    2. Otherwise map ODCS ``logicalType`` (honouring ``logicalTypeOptions``):
       ``string``->``STRING``, ``integer``->``BIGINT`` (``i32``->``INT``),
       ``number``->``DOUBLE`` (``f32``->``FLOAT``; precision/scale ->
       ``DECIMAL(p,s)``), ``boolean``->``BOOLEAN``, ``date``->``DATE``,
       ``timestamp``->``TIMESTAMP``, ``time``->``STRING``, ``object``->
       ``STRUCT<...>``, ``array``->``ARRAY<itemType>``.

    :raises Odcs2LhpError: when the type is unmappable.
    """
    physical = prop.get("physicalType")
    if physical:
        return physical

    logical = prop.get("logicalType")
    options = prop.get("logicalTypeOptions") or {}

    if logical in _SIMPLE_LOGICAL:
        return _SIMPLE_LOGICAL[logical]

    if logical == "integer":
        if options.get("format") == "i32":
            return "INT"
        return "BIGINT"

    if logical == "number":
        precision = options.get("precision")
        scale = options.get("scale")
        if isinstance(precision, (int, float)) and isinstance(scale, (int, float)):
            return f"DECIMAL({precision},{scale})"
        if options.get("format") == "f32":
            return "FLOAT"
        return "DOUBLE"

    if logical == "object":
        fields = []
        for sub in prop.get("properties", []) or []:
            fields.append(f"{sub['name']}:{odcs_type_to_spark(sub)}")
        return f"STRUCT<{','.join(fields)}>"

    if logical == "array":
        item_type = odcs_type_to_spark(prop["items"])
        return f"ARRAY<{item_type}>"

    raise _unmappable(prop)


# ---------------------------------------------------------------------------
# Tag mapping  (ODCS `tags` array -> Unity Catalog tag mapping)
# ---------------------------------------------------------------------------


def odcs_tags_to_uc(element: Dict[str, Any]) -> Dict[str, str]:
    """Map an ODCS element's ``tags`` array to a Unity Catalog tag mapping.

    Each entry uses a ``"key:value"`` convention: a colon-less string becomes a
    key-only tag (``"pii"`` -> ``{"pii": ""}``); a string with a colon is split
    on the **first** colon (``"domain:sales"`` -> ``{"domain": "sales"}``;
    ``"note:a:b"`` -> ``{"note": "a:b"}``). Both sides are stripped. Returns
    ``{}`` when ``tags`` is absent or empty; a later entry with the same key
    overwrites an earlier one.
    """
    result: Dict[str, str] = {}
    for tag in element.get("tags") or []:
        key, sep, value = str(tag).partition(":")
        result[key.strip()] = value.strip() if sep else ""
    return result


# ---------------------------------------------------------------------------
# Constraint mapping  (ODCS property -> data_quality expectation predicates)
# ---------------------------------------------------------------------------


def _num(value: Any) -> str:
    """Render a numeric literal without a spurious trailing ``.0``."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def odcs_property_to_constraints(prop: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Derive row-level expectation predicates for a single ODCS property.

    Returns ``(predicate, name)`` pairs (order preserved) from the property's
    ``logicalTypeOptions``. Scalar comparisons are emitted bare (DLT/SDP treats a
    NULL-valued expectation as passing); array ``size()`` and object nested
    ``IS NOT NULL`` checks are NULL-guarded. The property ``required`` flag is
    NOT translated here (see :func:`odcs2lhp.translator` for the NOT NULL
    expectation). Unknown options are skipped.
    """
    col = prop["name"]
    constraints: List[Tuple[str, str]] = []

    def _guard(predicate: str) -> str:
        return f"{col} IS NULL OR ({predicate})"

    logical = prop.get("logicalType")
    options = prop.get("logicalTypeOptions") or {}

    if logical == "string":
        if "minLength" in options:
            constraints.append(
                (f"length({col}) >= {_num(options['minLength'])}", f"{col}_min_length")
            )
        if "maxLength" in options:
            constraints.append(
                (f"length({col}) <= {_num(options['maxLength'])}", f"{col}_max_length")
            )
        if "pattern" in options:
            regex = str(options["pattern"]).replace("'", "''")
            constraints.append((f"{col} RLIKE '{regex}'", f"{col}_pattern"))

    elif logical in ("integer", "number"):
        if "minimum" in options:
            constraints.append((f"{col} >= {_num(options['minimum'])}", f"{col}_min"))
        if "maximum" in options:
            constraints.append((f"{col} <= {_num(options['maximum'])}", f"{col}_max"))
        if "exclusiveMinimum" in options:
            constraints.append(
                (f"{col} > {_num(options['exclusiveMinimum'])}", f"{col}_exclusive_min")
            )
        if "exclusiveMaximum" in options:
            constraints.append(
                (f"{col} < {_num(options['exclusiveMaximum'])}", f"{col}_exclusive_max")
            )
        if "multipleOf" in options:
            constraints.append(
                (f"{col} % {_num(options['multipleOf'])} = 0", f"{col}_multiple_of")
            )

    elif logical in ("date", "timestamp", "time"):
        if "minimum" in options:
            constraints.append((f"{col} >= '{options['minimum']}'", f"{col}_min"))
        if "maximum" in options:
            constraints.append((f"{col} <= '{options['maximum']}'", f"{col}_max"))
        if "exclusiveMinimum" in options:
            constraints.append(
                (f"{col} > '{options['exclusiveMinimum']}'", f"{col}_exclusive_min")
            )
        if "exclusiveMaximum" in options:
            constraints.append(
                (f"{col} < '{options['exclusiveMaximum']}'", f"{col}_exclusive_max")
            )

    elif logical == "array":
        if "minItems" in options:
            constraints.append(
                (_guard(f"size({col}) >= {_num(options['minItems'])}"),
                 f"{col}_min_items")
            )
        if "maxItems" in options:
            constraints.append(
                (_guard(f"size({col}) <= {_num(options['maxItems'])}"),
                 f"{col}_max_items")
            )
        if options.get("uniqueItems") is True:
            constraints.append(
                (_guard(f"size({col}) = size(array_distinct({col}))"),
                 f"{col}_unique_items")
            )

    elif logical == "object":
        for field in options.get("required", []) or []:
            constraints.append(
                (_guard(f"{col}.{field} IS NOT NULL"), f"{col}_{field}_not_null")
            )

    return constraints
