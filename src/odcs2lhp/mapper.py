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
from .parsers.spark_type_parser import parse_spark_ddl, spark_family

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

# ODCS integer ``logicalTypeOptions.format`` (Rust widths) -> Spark DDL type.
# Unsigned widths promote to the next signed type holding the full range;
# u64/i128/u128 exceed BIGINT, so they map to the widest exact Spark integer
# (DECIMAL) on a best-effort basis. Default (absent/unknown) is BIGINT.
_INTEGER_FORMAT_TYPES = {
    "i8": "TINYINT",
    "i16": "SMALLINT",
    "i32": "INT",
    "i64": "BIGINT",
    "u8": "SMALLINT",
    "u16": "INT",
    "u32": "BIGINT",
    "u64": "DECIMAL(20,0)",
    "i128": "DECIMAL(38,0)",
    "u128": "DECIMAL(38,0)",
}

# ODCS number ``logicalTypeOptions.format`` (Rust float widths) -> Spark DDL
# type. Default (absent/unknown) is DOUBLE.
_NUMBER_FORMAT_TYPES = {
    "f32": "FLOAT",
    "f64": "DOUBLE",
}

# Spark families accepted as a valid refinement of each scalar logical type.
# ``integer`` is treated as a subtype of ``number``; ``binary`` as a subtype of
# ``string``.
_COMPATIBLE_FAMILIES = {
    "string": {"string", "binary"},
    "boolean": {"boolean"},
    "date": {"date"},
    "timestamp": {"timestamp"},
    "time": {"string"},
    "integer": {"integer"},
    "number": {"integer", "fractional"},
}

# Complex logical types -> Spark families accepted for a props-less / items-less
# object or array (i.e. when there is no logical shape to build from).
_COMPATIBLE_COMPLEX_FAMILIES = {
    "object": {"struct", "map", "variant"},
    "array": {"array"},
}

# A JDK/Spark datetime pattern is safe for a bare ``CAST(... AS DATE/TIMESTAMP)``
# only when it matches Spark's default parse shape. Non-matching patterns would
# silently cast to NULL, so we keep such columns as ``STRING`` until LHP gains
# format-aware parsing.
_SPARK_DEFAULT_DATETIME_FORMAT = re.compile(
    r"^y{4,}(?:-M{1,2}(?:-d{1,2}(?:(?: |'T')H{1,2}"
    r"(?::m{1,2}(?::s{1,2}(?:.S{1,6})?)?)?"
    r"(?:VV|z{1,4}|O|OOOO|X{1,5}|x{1,5}|Z{1,5})?)?)?)?$"
)


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


def _format_is_spark_default(options: Dict[str, Any]) -> bool:
    """True when ``logicalTypeOptions.format`` matches Spark's default parse shape."""
    fmt = options.get("format")
    return isinstance(fmt, str) and bool(_SPARK_DEFAULT_DATETIME_FORMAT.match(fmt))


def _logical_to_spark(prop: Dict[str, Any]) -> str:
    """Map an ODCS ``logicalType`` (+ ``logicalTypeOptions``) to a Spark DDL type.

    ``string``->``STRING``, ``integer``->``BIGINT`` (width from
    ``logicalTypeOptions.format``: ``i8``->``TINYINT`` ... ``u128``->
    ``DECIMAL(38,0)`` per :data:`_INTEGER_FORMAT_TYPES`), ``number``->``DOUBLE``
    (``f32``->``FLOAT``, ``f64``->``DOUBLE``), ``boolean``->``BOOLEAN``,
    ``date``->``DATE``, ``timestamp``->``TIMESTAMP``, ``time``->``STRING``,
    ``object``->``STRUCT<...>`` (or ``VARIANT`` when it has no sub-properties),
    ``array``->``ARRAY<itemType>``.

    :raises Odcs2LhpError: when the logical type is unmappable (unknown, or an
        array with no ``items``).
    """
    logical = prop.get("logicalType")
    options = prop.get("logicalTypeOptions") or {}

    if logical in _SIMPLE_LOGICAL:
        return _SIMPLE_LOGICAL[logical]

    if logical == "integer":
        return _INTEGER_FORMAT_TYPES.get(options.get("format"), "BIGINT")

    if logical == "number":
        return _NUMBER_FORMAT_TYPES.get(options.get("format"), "DOUBLE")

    if logical == "object":
        return _logical_object_to_spark(prop)

    if logical == "array":
        return _logical_array_to_spark(prop)

    raise _unmappable(prop)


def _logical_object_to_spark(prop: Dict[str, Any]) -> str:
    """Build ``STRUCT<...>`` from sub-properties, or ``VARIANT`` when there are none."""
    sub_properties = prop.get("properties") or []
    if not sub_properties:
        return "VARIANT"
    fields = [f"{sub['name']}:{odcs_type_to_spark(sub)}" for sub in sub_properties]
    return f"STRUCT<{','.join(fields)}>"


def _logical_array_to_spark(prop: Dict[str, Any]) -> str:
    """Build ``ARRAY<item>`` from logical ``items``; error when ``items`` absent."""
    items = prop.get("items")
    if not items:
        raise _unmappable(prop)
    return f"ARRAY<{odcs_type_to_spark(items)}>"


def _physical_is_usable(prop: Dict[str, Any], logical: str) -> bool:
    """Decide whether ``physicalType`` should be used verbatim as the target type.

    Applies the family-compatibility rules and the string->temporal format guard.
    Complex logical types (object/array) are handled by the caller when they
    carry a logical shape; this only judges a physical type against a scalar or
    shapeless complex logical.
    """
    physical = prop.get("physicalType")
    node = parse_spark_ddl(physical) if physical else None
    if node is None:
        return False

    family = spark_family(node)
    options = prop.get("logicalTypeOptions") or {}

    if logical in ("date", "timestamp") and family == "string":
        # A string source only becomes a temporal type when its declared format
        # is safe for a bare cast; otherwise it stays a string.
        return not _format_is_spark_default(options)

    if logical in _COMPATIBLE_COMPLEX_FAMILIES:
        return family in _COMPATIBLE_COMPLEX_FAMILIES[logical]

    return family in _COMPATIBLE_FAMILIES.get(logical, set())


def _has_numeric_format(prop: Dict[str, Any], logical: str) -> bool:
    """True when a numeric logical type carries a ``logicalTypeOptions.format``."""
    if logical not in ("integer", "number"):
        return False
    return bool((prop.get("logicalTypeOptions") or {}).get("format"))


def odcs_type_to_spark(prop: Dict[str, Any]) -> str:
    """Resolve an ODCS schema property to a Spark DDL type string.

    ``physicalType`` describes the *source* type and is used verbatim only when
    it is a valid, complete Spark type whose family is compatible with (a subtype
    of) the ``logicalType``. Otherwise the always-mappable ``logicalType`` wins.

    - **No logicalType:** use ``physicalType`` verbatim if it is valid Spark DDL,
      else raise.
    - **Logical shape wins:** an object with ``properties``, an array with
      ``items``, or a numeric type with ``logicalTypeOptions.format`` builds the
      type from the logical definition, ignoring ``physicalType``.
    - **Scalar (and shapeless complex):** use ``physicalType`` verbatim when its
      family matches; a ``STRING`` physical for a ``date``/``timestamp`` logical
      only becomes temporal when ``logicalTypeOptions.format`` is Spark-default.

    :raises Odcs2LhpError: when the type is unmappable.
    """
    logical = prop.get("logicalType")
    physical = prop.get("physicalType")

    if not logical:
        if physical and parse_spark_ddl(physical) is not None:
            return physical
        raise _unmappable(prop)

    # A defined logical shape always wins: object properties, array items, or a
    # numeric format all pin the target type regardless of physicalType.
    if logical == "object" and (prop.get("properties") or []):
        return _logical_object_to_spark(prop)
    if logical == "array" and prop.get("items"):
        return _logical_array_to_spark(prop)
    if _has_numeric_format(prop, logical):
        return _logical_to_spark(prop)

    if _physical_is_usable(prop, logical):
        return physical

    return _logical_to_spark(prop)


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
