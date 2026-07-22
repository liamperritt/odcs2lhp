"""A tiny, dependency-free Spark DDL type parser and family classifier.

This module knows nothing about ODCS. It answers two questions about a Spark
SQL DDL type string (e.g. ``"DECIMAL(18,2)"``, ``"ARRAY<STRING>"``,
``"STRUCT<a:INT,b:ARRAY<STRING>>"``):

- :func:`parse_spark_ddl` -> a :class:`SparkType` AST, or ``None`` when the
  string is not a valid *and complete* Spark type (a bare ``ARRAY`` with no
  element type is incomplete, hence ``None``; an unknown scalar name such as
  ``NUMBER`` is not a Spark type, hence ``None``).
- :func:`spark_family` -> a coarse family name (``"integer"``, ``"fractional"``,
  ``"string"``, ``"boolean"``, ``"date"``, ``"timestamp"``, ``"binary"``,
  ``"interval"``, ``"geography"``, ``"geometry"``, ``"array"``, ``"map"``,
  ``"struct"``, ``"variant"``, or ``"other"``) used to decide type compatibility.
  ``"other"`` covers valid Spark types with no compatible ODCS logical type
  (e.g. ``VOID``, ``NULL``).

Parsing is case-insensitive, whitespace-tolerant, and bracket-aware (nested
generics split on top-level commas only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple

# Known scalar Spark type names -> coarse family. A name absent from this map is
# not a Spark type and makes the whole parse fail (returns None).
_SCALAR_FAMILIES = {
    "BYTE": "integer",
    "TINYINT": "integer",
    "SHORT": "integer",
    "SMALLINT": "integer",
    "INT": "integer",
    "INTEGER": "integer",
    "LONG": "integer",
    "BIGINT": "integer",
    "FLOAT": "fractional",
    "REAL": "fractional",
    "DOUBLE": "fractional",
    "DECIMAL": "fractional",
    "DEC": "fractional",
    "NUMERIC": "fractional",
    "STRING": "string",
    "CHAR": "string",
    "VARCHAR": "string",
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP_NTZ": "timestamp",
    "TIMESTAMP_LTZ": "timestamp",
    "BINARY": "binary",
    # String-serialisable types: each its own family, treated (like ``binary``)
    # as a valid refinement of the ``string`` logical type.
    "INTERVAL": "interval",
    "GEOGRAPHY": "geography",
    "GEOMETRY": "geometry",
    # Valid Spark types with no compatible ODCS logical type.
    "VOID": "other",
    "NULL": "other",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SparkType:
    """Base class for parsed Spark types. Subclasses expose a ``family`` string."""


@dataclass(frozen=True)
class ScalarType(SparkType):
    """A non-parameterised or parameterised scalar (``INT``, ``DECIMAL(18,2)``)."""

    name: str
    family: str
    params: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ArrayType(SparkType):
    """``ARRAY<element>``."""

    element: SparkType
    family: ClassVar[str] = "array"


@dataclass(frozen=True)
class MapType(SparkType):
    """``MAP<key,value>``."""

    key: SparkType
    value: SparkType
    family: ClassVar[str] = "map"


@dataclass(frozen=True)
class StructType(SparkType):
    """``STRUCT<name:type,...>``."""

    fields: Tuple[Tuple[str, SparkType], ...]
    family: ClassVar[str] = "struct"


@dataclass(frozen=True)
class VariantType(SparkType):
    """``VARIANT``."""

    family: ClassVar[str] = "variant"


def _split_top_level(inner: str) -> Optional[List[str]]:
    """Split ``inner`` on top-level commas (depth 0), or ``None`` if unbalanced.

    Depth is tracked across ``<>`` and ``()``. Returns ``[]`` for empty input.
    """
    if not inner.strip():
        return []

    parts: List[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(inner):
        if char in "<(":
            depth += 1
        elif char in ">)":
            depth -= 1
            if depth < 0:
                return None
        elif char == "," and depth == 0:
            parts.append(inner[start:index])
            start = index + 1
    if depth != 0:
        return None
    parts.append(inner[start:])
    return parts


def _split_field(part: str) -> Optional[Tuple[str, str]]:
    """Split a STRUCT field ``name:type`` on its top-level colon."""
    depth = 0
    for index, char in enumerate(part):
        if char in "<(":
            depth += 1
        elif char in ">)":
            depth -= 1
        elif char == ":" and depth == 0:
            return part[:index].strip(), part[index + 1 :].strip()
    return None


def _parse_scalar(text: str) -> Optional[ScalarType]:
    """Parse a scalar type name with optional ``(params)``."""
    paren = text.find("(")
    if paren == -1:
        name = text.upper()
        params: Tuple[str, ...] = ()
    else:
        if not text.endswith(")"):
            return None
        name = text[:paren].strip().upper()
        arg_text = text[paren + 1 : -1]
        params = tuple(arg.strip() for arg in arg_text.split(","))

    if not _IDENTIFIER.match(name):
        return None
    family = _SCALAR_FAMILIES.get(name)
    if family is None:
        return None
    return ScalarType(name=name, family=family, params=params)


def _parse_generic(head: str, inner: str) -> Optional[SparkType]:
    """Parse a parameterised complex type ``HEAD<inner>``."""
    if head == "ARRAY":
        parts = _split_top_level(inner)
        if parts is None or len(parts) != 1:
            return None
        element = _parse_type(parts[0])
        return ArrayType(element) if element is not None else None

    if head == "MAP":
        parts = _split_top_level(inner)
        if parts is None or len(parts) != 2:
            return None
        key = _parse_type(parts[0])
        value = _parse_type(parts[1])
        if key is None or value is None:
            return None
        return MapType(key, value)

    if head == "STRUCT":
        parts = _split_top_level(inner)
        if not parts:
            return None
        fields: List[Tuple[str, SparkType]] = []
        for part in parts:
            split = _split_field(part)
            if split is None:
                return None
            name, type_text = split
            if not _IDENTIFIER.match(name):
                return None
            field_type = _parse_type(type_text)
            if field_type is None:
                return None
            fields.append((name, field_type))
        return StructType(tuple(fields))

    return None


def _parse_type(text: str) -> Optional[SparkType]:
    """Recursive core: parse a trimmed type string into a :class:`SparkType`."""
    text = text.strip()
    if not text:
        return None

    angle = text.find("<")
    if angle != -1:
        if not text.endswith(">"):
            return None
        head = text[:angle].strip().upper()
        inner = text[angle + 1 : -1]
        return _parse_generic(head, inner)

    upper = text.upper()
    if upper == "VARIANT":
        return VariantType()
    if upper in ("ARRAY", "MAP", "STRUCT"):
        return None  # complex type missing its <...> is incomplete
    return _parse_scalar(text)


def parse_spark_ddl(text: str) -> Optional[SparkType]:
    """Parse ``text`` as a Spark DDL type, or return ``None`` if invalid/incomplete."""
    if not text:
        return None
    return _parse_type(text)


def spark_family(node: SparkType) -> str:
    """Return the coarse family name for a parsed :class:`SparkType`."""
    return node.family
