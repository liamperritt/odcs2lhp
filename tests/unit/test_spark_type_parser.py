"""Behaviour tests for the ODCS-agnostic Spark DDL type parser.

These exercise :func:`parse_spark_ddl` and :func:`spark_family` directly:
parsing scalars (parameterised and not), nested generics, case/whitespace
tolerance, and rejection of invalid or incomplete type strings.
"""

from __future__ import annotations

import pytest

from odcs2lhp.parsers.spark_type_parser import (
    ArrayType,
    MapType,
    ScalarType,
    StructType,
    VariantType,
    parse_spark_ddl,
    spark_family,
)


# --- scalars ----------------------------------------------------------------


def test_parse_spark_ddl_parses_bare_scalar():
    node = parse_spark_ddl("BIGINT")

    assert isinstance(node, ScalarType)
    assert node.family == "integer"


def test_parse_spark_ddl_parses_parameterised_decimal():
    node = parse_spark_ddl("DECIMAL(18,2)")

    assert isinstance(node, ScalarType)
    assert node.family == "fractional"
    assert node.params == ("18", "2")


def test_parse_spark_ddl_parses_varchar_with_length_as_string_family():
    node = parse_spark_ddl("VARCHAR(255)")

    assert isinstance(node, ScalarType)
    assert node.family == "string"


@pytest.mark.parametrize(
    "text,expected_family",
    [
        ("TINYINT", "integer"),
        ("SMALLINT", "integer"),
        ("INT", "integer"),
        ("INTEGER", "integer"),
        ("LONG", "integer"),
        ("FLOAT", "fractional"),
        ("REAL", "fractional"),
        ("DOUBLE", "fractional"),
        ("STRING", "string"),
        ("CHAR(3)", "string"),
        ("BOOLEAN", "boolean"),
        ("DATE", "date"),
        ("TIMESTAMP", "timestamp"),
        ("TIMESTAMP_NTZ", "timestamp"),
        ("BINARY", "binary"),
        ("INTERVAL", "interval"),
        ("GEOGRAPHY", "geography"),
        ("GEOMETRY", "geometry"),
    ],
)
def test_parse_spark_ddl_classifies_scalar_family(text, expected_family):
    node = parse_spark_ddl(text)

    assert node is not None
    assert spark_family(node) == expected_family


def test_parse_spark_ddl_is_case_insensitive():
    node = parse_spark_ddl("bigint")

    assert isinstance(node, ScalarType)
    assert node.family == "integer"


def test_parse_spark_ddl_tolerates_surrounding_whitespace():
    node = parse_spark_ddl("  DECIMAL( 8 , 2 )  ")

    assert isinstance(node, ScalarType)
    assert node.params == ("8", "2")


def test_parse_spark_ddl_classifies_typeless_named_type_as_other():
    node = parse_spark_ddl("VOID")

    assert node is not None
    assert spark_family(node) == "other"


# --- complex ----------------------------------------------------------------


def test_parse_spark_ddl_parses_array_of_string():
    node = parse_spark_ddl("ARRAY<STRING>")

    assert isinstance(node, ArrayType)
    assert node.family == "array"
    assert isinstance(node.element, ScalarType)
    assert node.element.family == "string"


def test_parse_spark_ddl_parses_map_of_string_to_string():
    node = parse_spark_ddl("MAP<STRING,STRING>")

    assert isinstance(node, MapType)
    assert node.family == "map"


def test_parse_spark_ddl_parses_variant():
    node = parse_spark_ddl("VARIANT")

    assert isinstance(node, VariantType)
    assert node.family == "variant"


def test_parse_spark_ddl_parses_struct_with_named_fields():
    node = parse_spark_ddl("STRUCT<a:INT,b:STRING>")

    assert isinstance(node, StructType)
    assert node.family == "struct"
    assert [name for name, _ in node.fields] == ["a", "b"]


def test_parse_spark_ddl_parses_nested_struct_of_array_of_struct():
    node = parse_spark_ddl("STRUCT<a:ARRAY<STRUCT<b:INT>>,c:MAP<STRING,ARRAY<INT>>>")

    assert isinstance(node, StructType)
    names = [name for name, _ in node.fields]
    assert names == ["a", "c"]
    a_type = dict(node.fields)["a"]
    assert isinstance(a_type, ArrayType)
    assert isinstance(a_type.element, StructType)


# --- invalid / incomplete ---------------------------------------------------


def test_parse_spark_ddl_returns_none_when_array_missing_element_type():
    assert parse_spark_ddl("ARRAY") is None


def test_parse_spark_ddl_returns_none_when_map_missing_type_args():
    assert parse_spark_ddl("MAP") is None


def test_parse_spark_ddl_returns_none_when_struct_missing_fields():
    assert parse_spark_ddl("STRUCT") is None


def test_parse_spark_ddl_returns_none_when_brackets_unbalanced():
    assert parse_spark_ddl("ARRAY<STRING") is None


def test_parse_spark_ddl_returns_none_when_empty():
    assert parse_spark_ddl("   ") is None


def test_parse_spark_ddl_returns_none_when_element_type_invalid():
    assert parse_spark_ddl("ARRAY<>") is None
