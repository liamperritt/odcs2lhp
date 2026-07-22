"""Behaviour tests for ODCS type + constraint mapping, via the public translator.

These exercise :func:`odcs2lhp.translator.translate_contract` with hand-built
contract dicts so each ODCS type/option branch is covered without touching the
private mapper helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from odcs2lhp.errors import Odcs2LhpError
from odcs2lhp.mapper import quote_identifier, sanitize_name
from odcs2lhp.translator import translate_contract


def _write_columns(properties: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Translate a one-object contract and return its write-schema columns by name."""
    contract = {
        "version": "1.0",
        "schema": [{"name": "t", "properties": properties}],
    }
    artifacts = translate_contract(contract, prefix="c")
    write = next(
        a.data for a in artifacts if a.relative_path == "c/write/schemas/t_schema.yaml"
    )
    return {c["name"]: c for c in write["columns"]}


def _expectations(properties: List[Dict[str, Any]]) -> Dict[str, str]:
    """Translate a one-object contract and return ``{name: expression}``."""
    contract = {
        "version": "1.0",
        "schema": [{"name": "t", "properties": properties}],
    }
    artifacts = translate_contract(contract, prefix="c")
    exp = next(
        a.data
        for a in artifacts
        if a.relative_path == "c/transform/expectations/t_expectations.yaml"
    )
    return {e["name"]: e["expression"] for e in exp["expectations"]}


def _one_type(prop: Dict[str, Any]) -> str:
    """Resolve a single property's write-schema Spark type."""
    return _write_columns([{**prop, "name": "c"}])["c"]["type"]


def test_type_mapping_uses_physical_type_verbatim_when_present():
    cols = _write_columns([{"name": "c", "physicalType": "DECIMAL(9,3)"}])

    assert cols["c"]["type"] == "DECIMAL(9,3)"


# --- logicalType inference --------------------------------------------------


def test_type_mapping_maps_integer_i32_to_int():
    cols = _write_columns(
        [{"name": "c", "logicalType": "integer", "logicalTypeOptions": {"format": "i32"}}]
    )

    assert cols["c"]["type"] == "INT"


def test_type_mapping_maps_integer_to_bigint_by_default():
    cols = _write_columns([{"name": "c", "logicalType": "integer"}])

    assert cols["c"]["type"] == "BIGINT"


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("i8", "TINYINT"),
        ("i16", "SMALLINT"),
        ("i32", "INT"),
        ("i64", "BIGINT"),
        ("u8", "SMALLINT"),
        ("u16", "INT"),
        ("u32", "BIGINT"),
        ("u64", "DECIMAL(20,0)"),
        ("i128", "DECIMAL(38,0)"),
        ("u128", "DECIMAL(38,0)"),
    ],
)
def test_type_mapping_maps_integer_format_to_spark_width(fmt, expected):
    assert (
        _one_type({"logicalType": "integer", "logicalTypeOptions": {"format": fmt}})
        == expected
    )


def test_type_mapping_maps_number_f32_to_float():
    cols = _write_columns(
        [{"name": "c", "logicalType": "number", "logicalTypeOptions": {"format": "f32"}}]
    )

    assert cols["c"]["type"] == "FLOAT"


def test_type_mapping_maps_number_f64_to_double():
    assert (
        _one_type({"logicalType": "number", "logicalTypeOptions": {"format": "f64"}})
        == "DOUBLE"
    )


def test_type_mapping_maps_number_to_double_by_default():
    cols = _write_columns([{"name": "c", "logicalType": "number"}])

    assert cols["c"]["type"] == "DOUBLE"


def test_type_mapping_uses_logical_width_when_integer_format_present_over_physical():
    assert (
        _one_type(
            {
                "logicalType": "integer",
                "physicalType": "INT",
                "logicalTypeOptions": {"format": "u32"},
            }
        )
        == "BIGINT"
    )


def test_type_mapping_uses_logical_float_when_number_format_present_over_physical():
    assert (
        _one_type(
            {
                "logicalType": "number",
                "physicalType": "DOUBLE",
                "logicalTypeOptions": {"format": "f32"},
            }
        )
        == "FLOAT"
    )


def test_type_mapping_uses_physical_when_integer_has_no_format():
    assert _one_type({"logicalType": "integer", "physicalType": "BIGINT"}) == "BIGINT"


def test_type_mapping_maps_object_to_struct_recursively():
    cols = _write_columns(
        [
            {
                "name": "addr",
                "logicalType": "object",
                "properties": [
                    {"name": "street", "logicalType": "string"},
                    {"name": "zip", "logicalType": "integer"},
                ],
            }
        ]
    )

    assert cols["addr"]["type"] == "STRUCT<street:STRING,zip:BIGINT>"


def test_type_mapping_maps_simple_logical_types():
    cols = _write_columns(
        [
            {"name": "s", "logicalType": "string"},
            {"name": "b", "logicalType": "boolean"},
            {"name": "d", "logicalType": "date"},
            {"name": "ts", "logicalType": "timestamp"},
            {"name": "t", "logicalType": "time"},
        ]
    )

    assert cols["s"]["type"] == "STRING"
    assert cols["b"]["type"] == "BOOLEAN"
    assert cols["d"]["type"] == "DATE"
    assert cols["ts"]["type"] == "TIMESTAMP"
    assert cols["t"]["type"] == "STRING"


# --- physicalType <-> logicalType reconciliation ----------------------------


def test_type_mapping_uses_physical_verbatim_when_family_matches_logical():
    assert _one_type({"logicalType": "integer", "physicalType": "BIGINT"}) == "BIGINT"


def test_type_mapping_uses_physical_int_when_logical_is_number():
    # integer is a subtype of number, so INT is a valid refinement.
    assert _one_type({"logicalType": "number", "physicalType": "INT"}) == "INT"


def test_type_mapping_uses_physical_binary_when_logical_is_string():
    # binary is a subtype of string, so BINARY is a valid refinement.
    assert _one_type({"logicalType": "string", "physicalType": "BINARY"}) == "BINARY"


def test_type_mapping_uses_physical_interval_when_logical_is_string():
    assert _one_type({"logicalType": "string", "physicalType": "INTERVAL"}) == "INTERVAL"


def test_type_mapping_uses_physical_geography_when_logical_is_string():
    assert _one_type({"logicalType": "string", "physicalType": "GEOGRAPHY"}) == "GEOGRAPHY"


def test_type_mapping_uses_physical_geometry_when_logical_is_string():
    assert _one_type({"logicalType": "string", "physicalType": "GEOMETRY"}) == "GEOMETRY"


def test_type_mapping_uses_physical_decimal_when_logical_is_number():
    assert (
        _one_type({"logicalType": "number", "physicalType": "DECIMAL(8,2)"})
        == "DECIMAL(8,2)"
    )


def test_type_mapping_falls_back_to_logical_when_decimal_physical_but_integer_logical():
    assert _one_type({"logicalType": "integer", "physicalType": "DECIMAL(8,2)"}) == "BIGINT"


def test_type_mapping_falls_back_to_logical_when_physical_unparseable():
    assert _one_type({"logicalType": "integer", "physicalType": "NUMBER(10)"}) == "BIGINT"


def test_type_mapping_falls_back_to_logical_when_physical_family_mismatches():
    assert _one_type({"logicalType": "timestamp", "physicalType": "BINARY"}) == "TIMESTAMP"


def test_type_mapping_uses_physical_verbatim_when_logical_absent_and_ddl_valid():
    assert _one_type({"physicalType": "BINARY"}) == "BINARY"


def test_type_mapping_errors_when_logical_absent_and_physical_unparseable():
    with pytest.raises(Odcs2LhpError) as exc_info:
        _one_type({"physicalType": "NUMBER(10)"})

    assert exc_info.value.code == "ODCS-TYPE-001"


# --- string -> temporal format guard ----------------------------------------


def test_type_mapping_defers_string_to_timestamp_when_format_present():
    # A declared format needs a format-aware parse (deferred to a later feature),
    # so the column keeps its string type here.
    assert (
        _one_type(
            {
                "logicalType": "timestamp",
                "physicalType": "STRING",
                "logicalTypeOptions": {"format": "yyyy-MM-dd HH:mm:ss"},
            }
        )
        == "STRING"
    )


def test_type_mapping_defers_string_to_date_when_format_present():
    assert (
        _one_type(
            {
                "logicalType": "date",
                "physicalType": "STRING",
                "logicalTypeOptions": {"format": "yyyy-MM-dd"},
            }
        )
        == "STRING"
    )


def test_type_mapping_defers_string_to_timestamp_when_format_is_non_default():
    assert (
        _one_type(
            {
                "logicalType": "timestamp",
                "physicalType": "STRING",
                "logicalTypeOptions": {"format": "MM/dd/yyyy"},
            }
        )
        == "STRING"
    )


def test_type_mapping_casts_string_to_timestamp_when_no_format():
    # No format is a bare cast Spark handles, so it becomes TIMESTAMP.
    assert (
        _one_type({"logicalType": "timestamp", "physicalType": "STRING"}) == "TIMESTAMP"
    )


def test_type_mapping_casts_string_to_date_when_no_format():
    assert _one_type({"logicalType": "date", "physicalType": "STRING"}) == "DATE"


def test_type_mapping_uses_timestamp_when_logical_temporal_and_no_physical():
    assert _one_type({"logicalType": "timestamp"}) == "TIMESTAMP"


# --- complex type reconciliation --------------------------------------------


def test_type_mapping_builds_struct_from_logical_props_when_object_has_properties():
    # Logical shape wins over a mismatching physical struct field type.
    result = _one_type(
        {
            "logicalType": "object",
            "physicalType": "STRUCT<a:STRING>",
            "properties": [{"name": "a", "logicalType": "integer"}],
        }
    )

    assert result == "STRUCT<a:BIGINT>"


def test_type_mapping_builds_struct_from_logical_props_when_physical_is_map():
    result = _one_type(
        {
            "logicalType": "object",
            "physicalType": "MAP<STRING,STRING>",
            "properties": [{"name": "a", "logicalType": "integer"}],
        }
    )

    assert result == "STRUCT<a:BIGINT>"


def test_type_mapping_uses_physical_map_when_object_has_no_properties():
    assert (
        _one_type({"logicalType": "object", "physicalType": "MAP<STRING,STRING>"})
        == "MAP<STRING,STRING>"
    )


def test_type_mapping_uses_physical_variant_when_object_has_no_properties():
    assert _one_type({"logicalType": "object", "physicalType": "VARIANT"}) == "VARIANT"


def test_type_mapping_defaults_object_to_variant_when_no_props_and_no_physical():
    assert _one_type({"logicalType": "object"}) == "VARIANT"


def test_type_mapping_uses_logical_items_when_array_defines_items():
    result = _one_type(
        {
            "logicalType": "array",
            "physicalType": "ARRAY<INT>",
            "items": {"logicalType": "string"},
        }
    )

    assert result == "ARRAY<STRING>"


def test_type_mapping_uses_logical_items_when_array_physical_is_incomplete():
    result = _one_type(
        {
            "logicalType": "array",
            "physicalType": "ARRAY",
            "items": {"logicalType": "string"},
        }
    )

    assert result == "ARRAY<STRING>"


def test_type_mapping_uses_physical_array_when_no_logical_items():
    assert (
        _one_type({"logicalType": "array", "physicalType": "ARRAY<INT>"})
        == "ARRAY<INT>"
    )


def test_type_mapping_errors_when_array_has_no_items_and_no_physical():
    with pytest.raises(Odcs2LhpError) as exc_info:
        _one_type({"logicalType": "array"})

    assert exc_info.value.code == "ODCS-TYPE-001"


def test_constraints_derive_numeric_min_and_max():
    exprs = _expectations(
        [
            {
                "name": "n",
                "logicalType": "integer",
                "physicalType": "BIGINT",
                "logicalTypeOptions": {"minimum": 1, "maximum": 10},
            }
        ]
    )

    assert exprs["n_min"] == "`n` >= 1"
    assert exprs["n_max"] == "`n` <= 10"


def test_constraints_derive_numeric_exclusive_bounds():
    exprs = _expectations(
        [
            {
                "name": "n",
                "logicalType": "number",
                "physicalType": "DOUBLE",
                "logicalTypeOptions": {"exclusiveMinimum": 0, "exclusiveMaximum": 100},
            }
        ]
    )

    assert exprs["n_exclusive_min"] == "`n` > 0"
    assert exprs["n_exclusive_max"] == "`n` < 100"


def test_constraints_derive_date_bounds_as_quoted_literals():
    exprs = _expectations(
        [
            {
                "name": "d",
                "logicalType": "date",
                "physicalType": "DATE",
                "logicalTypeOptions": {
                    "minimum": "2020-01-01",
                    "maximum": "2030-12-31",
                },
            }
        ]
    )

    assert exprs["d_min"] == "`d` >= '2020-01-01'"
    assert exprs["d_max"] == "`d` <= '2030-12-31'"


def test_constraints_derive_timestamp_exclusive_bounds():
    exprs = _expectations(
        [
            {
                "name": "ts",
                "logicalType": "timestamp",
                "physicalType": "TIMESTAMP",
                "logicalTypeOptions": {
                    "exclusiveMinimum": "2020-01-01T00:00:00",
                    "exclusiveMaximum": "2030-01-01T00:00:00",
                },
            }
        ]
    )

    assert exprs["ts_exclusive_min"] == "`ts` > '2020-01-01T00:00:00'"
    assert exprs["ts_exclusive_max"] == "`ts` < '2030-01-01T00:00:00'"


def test_constraints_guard_array_max_items():
    exprs = _expectations(
        [
            {
                "name": "arr",
                "logicalType": "array",
                "physicalType": "ARRAY<STRING>",
                "logicalTypeOptions": {"maxItems": 5},
            }
        ]
    )

    assert exprs["arr_max_items"] == "`arr` IS NULL OR (size(`arr`) <= 5)"


def test_constraints_guard_object_required_fields():
    exprs = _expectations(
        [
            {
                "name": "o",
                "logicalType": "object",
                "physicalType": "STRUCT<street:STRING>",
                "logicalTypeOptions": {"required": ["street"]},
            }
        ]
    )

    assert exprs["o_street_not_null"] == "`o` IS NULL OR (`o`.`street` IS NOT NULL)"


def test_constraints_render_float_multiple_of_without_trailing_zero():
    exprs = _expectations(
        [
            {
                "name": "n",
                "logicalType": "number",
                "physicalType": "DOUBLE",
                "logicalTypeOptions": {"multipleOf": 1.0},
            }
        ]
    )

    assert exprs["n_multiple_of"] == "`n` % 1 = 0"


# --- backtick quoting of column names in conditions -------------------------


def test_quote_identifier_doubles_embedded_backticks():
    assert quote_identifier("cust id") == "`cust id`"
    assert quote_identifier("a`b") == "`a``b`"


def test_sanitize_name_replaces_special_characters_with_underscore():
    assert sanitize_name("cust id") == "cust_id"
    assert sanitize_name("a`b") == "a_b"
    assert sanitize_name("order#") == "order_"
    assert sanitize_name("clean_1") == "clean_1"


def test_constraints_backtick_quote_column_name_in_condition():
    exprs = _expectations(
        [
            {
                "name": "cust id",
                "logicalType": "string",
                "physicalType": "STRING",
                "logicalTypeOptions": {"minLength": 3},
            }
        ]
    )

    assert exprs["cust_id_min_length"] == "length(`cust id`) >= 3"


def test_constraints_escape_embedded_backtick_in_column_name():
    exprs = _expectations(
        [
            {
                "name": "a`b",
                "logicalType": "string",
                "physicalType": "STRING",
                "logicalTypeOptions": {"pattern": "x"},
            }
        ]
    )

    assert exprs["a_b_pattern"] == "`a``b` RLIKE 'x'"


def test_constraints_escape_single_quote_in_date_bound():
    exprs = _expectations(
        [
            {
                "name": "d",
                "logicalType": "date",
                "physicalType": "DATE",
                "logicalTypeOptions": {"minimum": "2020' OR '1'='1"},
            }
        ]
    )

    assert exprs["d_min"] == "`d` >= '2020'' OR ''1''=''1'"


def test_constraints_escape_backslash_in_pattern():
    exprs = _expectations(
        [
            {
                "name": "code",
                "logicalType": "string",
                "physicalType": "STRING",
                "logicalTypeOptions": {"pattern": r"\d+"},
            }
        ]
    )

    assert exprs["code_pattern"] == r"`code` RLIKE '\\d+'"


def test_constraints_quote_both_object_and_field_names():
    exprs = _expectations(
        [
            {
                "name": "o",
                "logicalType": "object",
                "physicalType": "STRUCT<street name:STRING>",
                "logicalTypeOptions": {"required": ["street name"]},
            }
        ]
    )

    assert (
        exprs["o_street_name_not_null"]
        == "`o` IS NULL OR (`o`.`street name` IS NOT NULL)"
    )
