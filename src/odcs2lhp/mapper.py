"""Pure ODCS-property mappers.

These functions operate on plain ODCS dicts and return strings / dicts / tuples:

- :func:`odcs_type_to_spark` -> a Spark/Databricks DDL type string (the
  property's ``physicalType`` verbatim; logicalType inference is not yet
  implemented).
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


def _missing_physical_type(prop: Dict[str, Any]) -> Odcs2LhpError:
    name = prop.get("name")
    return Odcs2LhpError(
        "ODCS-TYPE-001",
        (
            f"ODCS property {name!r} has no 'physicalType'. logicalType-based "
            "type mapping is not implemented yet; a 'physicalType' is required."
        ),
        suggestions=[
            "Provide an explicit 'physicalType' (e.g. STRING, BIGINT, "
            "DECIMAL(18,2), ARRAY<STRING>)",
        ],
    )


def odcs_type_to_spark(prop: Dict[str, Any]) -> str:
    """Return the property's ``physicalType`` verbatim.

    logicalType-based inference is intentionally not implemented yet; a
    ``physicalType`` is required until it is.

    :raises Odcs2LhpError: when ``physicalType`` is absent.
    """
    physical = prop.get("physicalType")
    if physical:
        return physical
    raise _missing_physical_type(prop)


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
    """
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
