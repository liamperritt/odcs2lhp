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


def path_segment(value: str, *, field: str) -> str:
    """Slug ``value`` for use as a single output-path segment; reject unsafe results.

    ``slug`` maps path separators to underscores, so the only remaining hazards are
    a value that collapses to empty, ``.``, or ``..`` (``slug`` keeps dots) — those
    would escape or alias the output directory, so they raise.
    """
    seg = slug(str(value))
    if seg in ("", ".", ".."):
        raise Odcs2LhpError(
            "ODCS-PATH-001",
            f"Cannot build a safe output path: {field} {value!r} is empty or a "
            "path-traversal segment after sanitizing.",
            suggestions=[f"Use a {field} that contains at least one normal character."],
        )
    return seg


def quote_identifier(name: str) -> str:
    """Backtick-quote a Spark SQL identifier, doubling any embedded backtick."""
    return "`" + str(name).replace("`", "``") + "`"


def sanitize_name(name: str) -> str:
    """Replace every non-alphanumeric/underscore character with an underscore.

    Used to build expectation *names* (identifiers), which — unlike the SQL
    condition — cannot be quoted. Not injective: distinct source names may map
    to the same result (e.g. ``cust id`` and ``cust_id``), so callers accept the
    small collision risk this carries.
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name))


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
# ``integer`` is treated as a subtype of ``number``; ``binary`` and the
# string-serialisable ``interval``/``geography``/``geometry`` as subtypes of
# ``string``.
_COMPATIBLE_FAMILIES = {
    "string": {"string", "binary", "interval", "geography", "geometry"},
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

def _unmappable(prop: Dict[str, Any]) -> Odcs2LhpError:
    logical = prop.get("logicalType")
    return Odcs2LhpError(
        "ODCS-TYPE-001",
        (
            "Could not map ODCS property to a Spark type. The 'physicalType' is "
            f"not valid Spark DDL (logicalType={logical!r})."
        ),
        suggestions=[
            "Provide a valid Spark 'physicalType' (e.g. STRING, BIGINT, DECIMAL(18,2))",
            "For an object/array logicalType, define its 'properties'/'items'.",
        ],
    )


def _missing_types(prop: Dict[str, Any]) -> Odcs2LhpError:
    missing = [k for k in ("physicalType", "logicalType") if not prop.get(k)]
    return Odcs2LhpError(
        "ODCS-TYPE-001",
        (
            f"Could not map ODCS property {prop.get('name')!r} to a Spark type: "
            f"both 'physicalType' and 'logicalType' are required "
            f"(missing: {', '.join(missing)})."
        ),
        suggestions=[
            "Declare 'physicalType' (the source Spark DDL type, e.g. STRING, BIGINT).",
            "Declare 'logicalType' (one of: string, integer, number, boolean, "
            "date, timestamp, time, object, array).",
        ],
    )


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

    Applies the family-compatibility rules. Complex logical types (object/array)
    are handled by the caller when they carry a logical shape; this only judges a
    physical type against a scalar or shapeless complex logical. Deferred
    string-encoded conversions are intercepted by the caller before this is
    reached (see :func:`is_deferred_conversion`).
    """
    physical = prop.get("physicalType")
    node = parse_spark_ddl(physical) if physical else None
    if node is None:
        return False

    family = spark_family(node)

    if logical in _COMPATIBLE_COMPLEX_FAMILIES:
        return family in _COMPATIBLE_COMPLEX_FAMILIES[logical]

    return family in _COMPATIBLE_FAMILIES.get(logical, set())


def _has_numeric_format(prop: Dict[str, Any], logical: str) -> bool:
    """True when a numeric logical type carries a ``logicalTypeOptions.format``."""
    if logical not in ("integer", "number"):
        return False
    return bool((prop.get("logicalTypeOptions") or {}).get("format"))


def is_deferred_conversion(prop: Dict[str, Any]) -> bool:
    """True when a string-physical column needs a parse a bare cast can't do.

    These conversions are deferred to a later feature: ``string``->object/array
    (a JSON parse) and ``string``->``date``/``timestamp`` *with a declared
    ``format``* (a format-aware parse). For now such columns keep their (string)
    physical type. A ``date``/``timestamp`` with no ``format`` is NOT deferred — a
    bare Spark cast handles it — nor is any non-string physical type.
    """
    physical = prop.get("physicalType")
    node = parse_spark_ddl(physical) if physical else None
    if node is None or spark_family(node) != "string":
        return False

    logical = prop.get("logicalType")
    if logical in ("object", "array"):
        return True
    if logical in ("date", "timestamp"):
        return bool((prop.get("logicalTypeOptions") or {}).get("format"))
    return False


def odcs_type_to_spark(prop: Dict[str, Any]) -> str:
    """Resolve an ODCS schema property to a Spark DDL type string.

    Both ``physicalType`` (the source Spark DDL type) and ``logicalType`` (the
    abstract contract type) are **required**; a property missing either raises.
    ``physicalType`` is used verbatim only when it is a valid, complete Spark type
    whose family is compatible with (a subtype of) the ``logicalType``. Otherwise
    the always-mappable ``logicalType`` wins.

    - **Deferred conversions:** a string-physical column whose logical type needs a
      parse a bare cast can't do (JSON->object/array, or a formatted
      ``date``/``timestamp``) keeps its ``physicalType`` (a string) — the parse is
      deferred to a later feature (see :func:`is_deferred_conversion`).
    - **Logical shape wins:** an object with ``properties``, an array with
      ``items``, or a numeric type with ``logicalTypeOptions.format`` builds the
      type from the logical definition, ignoring ``physicalType``.
    - **Scalar (and shapeless complex):** use ``physicalType`` verbatim when its
      family matches; a ``STRING`` physical for a ``date``/``timestamp`` logical with
      no ``format`` becomes ``DATE``/``TIMESTAMP`` (a bare cast).

    :raises Odcs2LhpError: when ``physicalType`` or ``logicalType`` is missing, or
        the type is otherwise unmappable (``ODCS-TYPE-001``).
    """
    logical = prop.get("logicalType")
    physical = prop.get("physicalType")

    if not physical or not logical:
        raise _missing_types(prop)

    # A string-encoded value needing a non-cast parse keeps its string type; the
    # conversion is deferred to a later feature.
    if is_deferred_conversion(prop):
        return physical

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


def _sql_str_literal(value: Any) -> str:
    """Render a single-quoted SQL string literal, escaping backslashes then quotes."""
    escaped = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


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
    ``logicalTypeOptions``. Column names are backtick-quoted inside the SQL
    conditions (so names with spaces/special characters stay valid); the
    expectation ``name`` suffixes use the column name sanitized to an
    identifier-safe form (:func:`sanitize_name`). Scalar comparisons
    are emitted bare (DLT/SDP treats a NULL-valued expectation as passing); array
    ``size()`` and object nested ``IS NOT NULL`` checks are NULL-guarded. The
    property ``required`` flag is NOT translated here (see
    :func:`odcs2lhp.translator` for the NOT NULL expectation). Unknown options are
    skipped.

    A deferred string-encoded column (see :func:`is_deferred_conversion`) yields no
    predicates: its ``logicalTypeOptions`` describe the *parsed* shape (object
    fields, array size, temporal bounds), which cannot be evaluated against the
    still-unconverted string.
    """
    if is_deferred_conversion(prop):
        return []

    col = prop["name"]
    qcol = quote_identifier(col)
    scol = sanitize_name(col)
    constraints: List[Tuple[str, str]] = []

    def _guard(predicate: str) -> str:
        return f"{qcol} IS NULL OR ({predicate})"

    logical = prop.get("logicalType")
    options = prop.get("logicalTypeOptions") or {}

    if logical == "string":
        if "minLength" in options:
            constraints.append(
                (f"length({qcol}) >= {_num(options['minLength'])}", f"{scol}_min_length")
            )
        if "maxLength" in options:
            constraints.append(
                (f"length({qcol}) <= {_num(options['maxLength'])}", f"{scol}_max_length")
            )
        if "pattern" in options:
            constraints.append(
                (f"{qcol} RLIKE {_sql_str_literal(options['pattern'])}",
                 f"{scol}_pattern")
            )

    elif logical in ("integer", "number"):
        if "minimum" in options:
            constraints.append((f"{qcol} >= {_num(options['minimum'])}", f"{scol}_min"))
        if "maximum" in options:
            constraints.append((f"{qcol} <= {_num(options['maximum'])}", f"{scol}_max"))
        if "exclusiveMinimum" in options:
            constraints.append(
                (f"{qcol} > {_num(options['exclusiveMinimum'])}", f"{scol}_exclusive_min")
            )
        if "exclusiveMaximum" in options:
            constraints.append(
                (f"{qcol} < {_num(options['exclusiveMaximum'])}", f"{scol}_exclusive_max")
            )
        if "multipleOf" in options:
            constraints.append(
                (f"{qcol} % {_num(options['multipleOf'])} = 0", f"{scol}_multiple_of")
            )

    elif logical in ("date", "timestamp", "time"):
        if "minimum" in options:
            constraints.append(
                (f"{qcol} >= {_sql_str_literal(options['minimum'])}", f"{scol}_min")
            )
        if "maximum" in options:
            constraints.append(
                (f"{qcol} <= {_sql_str_literal(options['maximum'])}", f"{scol}_max")
            )
        if "exclusiveMinimum" in options:
            constraints.append(
                (f"{qcol} > {_sql_str_literal(options['exclusiveMinimum'])}",
                 f"{scol}_exclusive_min")
            )
        if "exclusiveMaximum" in options:
            constraints.append(
                (f"{qcol} < {_sql_str_literal(options['exclusiveMaximum'])}",
                 f"{scol}_exclusive_max")
            )

    elif logical == "array":
        if "minItems" in options:
            constraints.append(
                (_guard(f"size({qcol}) >= {_num(options['minItems'])}"),
                 f"{scol}_min_items")
            )
        if "maxItems" in options:
            constraints.append(
                (_guard(f"size({qcol}) <= {_num(options['maxItems'])}"),
                 f"{scol}_max_items")
            )
        if options.get("uniqueItems") is True:
            constraints.append(
                (_guard(f"size({qcol}) = size(array_distinct({qcol}))"),
                 f"{scol}_unique_items")
            )

    elif logical == "object":
        for field in options.get("required", []) or []:
            constraints.append(
                (_guard(f"{qcol}.{quote_identifier(field)} IS NOT NULL"),
                 f"{scol}_{sanitize_name(field)}_not_null")
            )

    return constraints
