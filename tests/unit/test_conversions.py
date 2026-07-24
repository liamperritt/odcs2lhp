"""Behaviour tests for :func:`odcs2lhp.mapper.string_conversion`.

These exercise the pure conversion-detection helper directly (like the other
public mapper helpers), covering every conversion row and the cases that must
resolve to ``None`` (left to the normal cast path).
"""

from __future__ import annotations

from typing import Any, Dict

from odcs2lhp.mapper import Conversion, string_conversion


def _conv(prop: Dict[str, Any]) -> Conversion:
    """Return the conversion for a property, failing if none is produced."""
    result = string_conversion({"name": "c", **prop})
    assert result is not None
    return result


# --- date / timestamp (format-aware) ----------------------------------------


def test_string_conversion_returns_to_date_when_string_physical_and_date_logical_with_format():
    conv = _conv(
        {
            "logicalType": "date",
            "physicalType": "STRING",
            "logicalTypeOptions": {"format": "MM/dd/yyyy"},
        }
    )

    assert conv.kind == "to_date"
    assert conv.target_type == "DATE"
    assert conv.source_type == "STRING"
    assert conv.sql_expr == "to_date(`c`, 'MM/dd/yyyy')"


def test_string_conversion_returns_to_timestamp_when_string_physical_and_timestamp_logical_with_format():
    conv = _conv(
        {
            "logicalType": "timestamp",
            "physicalType": "STRING",
            "logicalTypeOptions": {"format": "yyyy-MM-dd'T'HH:mm:ss"},
        }
    )

    assert conv.kind == "to_timestamp"
    assert conv.target_type == "TIMESTAMP"
    assert conv.sql_expr == "to_timestamp(`c`, 'yyyy-MM-dd''T''HH:mm:ss')"


def test_string_conversion_returns_to_utc_timestamp_when_timezone_false_and_default_timezone_set():
    conv = _conv(
        {
            "logicalType": "timestamp",
            "physicalType": "STRING",
            "logicalTypeOptions": {
                "format": "yyyy-MM-dd HH:mm:ss",
                "timezone": False,
                "defaultTimezone": "America/New_York",
            },
        }
    )

    assert conv.kind == "to_utc_timestamp"
    assert conv.target_type == "TIMESTAMP"
    assert conv.sql_expr == (
        "to_utc_timestamp(to_timestamp(`c`, 'yyyy-MM-dd HH:mm:ss'), "
        "'America/New_York')"
    )


def test_string_conversion_returns_to_timestamp_when_timezone_true_even_with_default_timezone():
    # timezone True means the string already carries the zone: plain to_timestamp.
    conv = _conv(
        {
            "logicalType": "timestamp",
            "physicalType": "STRING",
            "logicalTypeOptions": {
                "format": "yyyy-MM-dd HH:mm:ssXXX",
                "timezone": True,
                "defaultTimezone": "America/New_York",
            },
        }
    )

    assert conv.kind == "to_timestamp"


# --- from_json (struct / array) ---------------------------------------------


def test_string_conversion_returns_from_json_when_string_object_has_properties():
    conv = _conv(
        {
            "logicalType": "object",
            "physicalType": "STRING",
            "properties": [{"name": "city", "logicalType": "string", "physicalType": "STRING"}],
        }
    )

    assert conv.kind == "from_json_struct"
    assert conv.target_type == "STRUCT<city:STRING>"
    assert conv.sql_expr == "from_json(`c`, 'STRUCT<city:STRING>')"


def test_string_conversion_returns_from_json_when_string_array_has_items():
    conv = _conv(
        {
            "logicalType": "array",
            "physicalType": "STRING",
            "items": {"logicalType": "string", "physicalType": "STRING"},
        }
    )

    assert conv.kind == "from_json_array"
    assert conv.target_type == "ARRAY<STRING>"
    assert conv.sql_expr == "from_json(`c`, 'ARRAY<STRING>')"


# --- parse_json (variant) ---------------------------------------------------


def test_string_conversion_returns_parse_json_when_string_object_has_no_properties():
    conv = _conv({"logicalType": "object", "physicalType": "STRING"})

    assert conv.kind == "parse_json"
    assert conv.target_type == "VARIANT"
    assert conv.sql_expr == "parse_json(`c`)"


# --- unbase64 (binary) ------------------------------------------------------


def test_string_conversion_returns_unbase64_when_string_format_is_byte():
    conv = _conv(
        {
            "logicalType": "string",
            "physicalType": "STRING",
            "logicalTypeOptions": {"format": "byte"},
        }
    )

    assert conv.kind == "unbase64"
    assert conv.target_type == "BINARY"
    assert conv.sql_expr == "unbase64(`c`)"


def test_string_conversion_returns_unbase64_when_string_format_is_binary():
    conv = _conv(
        {
            "logicalType": "string",
            "physicalType": "STRING",
            "logicalTypeOptions": {"format": "binary"},
        }
    )

    assert conv.kind == "unbase64"


# --- source (physical) name -------------------------------------------------


def test_string_conversion_references_physical_name_when_it_differs():
    # The module runs on the raw load, before the rename, so it uses the source
    # (physical) column name in both the target and the expression.
    conv = string_conversion(
        {
            "name": "created_at",
            "physicalName": "cust ts",
            "logicalType": "timestamp",
            "physicalType": "STRING",
            "logicalTypeOptions": {"format": "MM/dd/yyyy HH:mm"},
        }
    )

    assert conv is not None
    assert conv.column == "cust ts"
    assert conv.sql_expr == "to_timestamp(`cust ts`, 'MM/dd/yyyy HH:mm')"


# --- None cases (left to the normal cast path) ------------------------------


def test_string_conversion_returns_none_when_physical_type_is_not_string():
    assert (
        string_conversion(
            {
                "name": "c",
                "logicalType": "timestamp",
                "physicalType": "TIMESTAMP",
                "logicalTypeOptions": {"format": "MM/dd/yyyy"},
            }
        )
        is None
    )


def test_string_conversion_returns_none_when_date_has_no_format():
    assert (
        string_conversion(
            {"name": "c", "logicalType": "date", "physicalType": "STRING"}
        )
        is None
    )


def test_string_conversion_returns_none_when_string_has_no_convertible_shape():
    # Plain string with only validation options: no conversion.
    assert (
        string_conversion(
            {
                "name": "c",
                "logicalType": "string",
                "physicalType": "STRING",
                "logicalTypeOptions": {"format": "email", "minLength": 3},
            }
        )
        is None
    )


def test_string_conversion_returns_none_when_string_to_integer():
    # Spark casts numeric strings natively; not a runtime-parse conversion.
    assert (
        string_conversion(
            {"name": "c", "logicalType": "integer", "physicalType": "STRING"}
        )
        is None
    )


def test_string_conversion_returns_none_when_no_physical_type():
    assert string_conversion({"name": "c", "logicalType": "date"}) is None
