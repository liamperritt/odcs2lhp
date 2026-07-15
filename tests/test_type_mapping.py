"""Behaviour tests for ODCS type + constraint mapping, via the public translator.

These exercise :func:`odcs2lhp.translator.translate_contract` with hand-built
contract dicts so each ODCS type/option branch is covered without touching the
private mapper helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List

from odcs2lhp.translator import translate_contract


def _write_columns(properties: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Translate a one-object contract and return its write-schema columns by name."""
    contract = {
        "version": "1.0",
        "schema": [{"name": "t", "properties": properties}],
    }
    artifacts = translate_contract(contract, stem="c")
    write = next(
        a.data for a in artifacts if a.relative_path == "schemas/write/c__t_schema.yaml"
    )
    return {c["name"]: c for c in write["columns"]}


def _expectations(properties: List[Dict[str, Any]]) -> Dict[str, str]:
    """Translate a one-object contract and return ``{name: expression}``."""
    contract = {
        "version": "1.0",
        "schema": [{"name": "t", "properties": properties}],
    }
    artifacts = translate_contract(contract, stem="c")
    exp = next(
        a.data
        for a in artifacts
        if a.relative_path == "expectations/c__t_expectations.yaml"
    )
    return {e["name"]: e["expression"] for e in exp["expectations"]}


def test_type_mapping_uses_physical_type_verbatim_when_present():
    cols = _write_columns([{"name": "c", "physicalType": "DECIMAL(9,3)"}])

    assert cols["c"]["type"] == "DECIMAL(9,3)"


def test_type_mapping_maps_integer_i32_to_int():
    cols = _write_columns(
        [{"name": "c", "logicalType": "integer", "logicalTypeOptions": {"format": "i32"}}]
    )

    assert cols["c"]["type"] == "INT"


def test_type_mapping_maps_integer_to_bigint_by_default():
    cols = _write_columns([{"name": "c", "logicalType": "integer"}])

    assert cols["c"]["type"] == "BIGINT"


def test_type_mapping_maps_number_with_precision_and_scale_to_decimal():
    cols = _write_columns(
        [
            {
                "name": "c",
                "logicalType": "number",
                "logicalTypeOptions": {"precision": 18, "scale": 2},
            }
        ]
    )

    assert cols["c"]["type"] == "DECIMAL(18,2)"


def test_type_mapping_maps_number_f32_to_float():
    cols = _write_columns(
        [{"name": "c", "logicalType": "number", "logicalTypeOptions": {"format": "f32"}}]
    )

    assert cols["c"]["type"] == "FLOAT"


def test_type_mapping_maps_number_to_double_by_default():
    cols = _write_columns([{"name": "c", "logicalType": "number"}])

    assert cols["c"]["type"] == "DOUBLE"


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


def test_constraints_derive_numeric_min_and_max():
    exprs = _expectations(
        [
            {
                "name": "n",
                "logicalType": "integer",
                "logicalTypeOptions": {"minimum": 1, "maximum": 10},
            }
        ]
    )

    assert exprs["n_min"] == "n >= 1"
    assert exprs["n_max"] == "n <= 10"


def test_constraints_derive_numeric_exclusive_bounds():
    exprs = _expectations(
        [
            {
                "name": "n",
                "logicalType": "number",
                "logicalTypeOptions": {"exclusiveMinimum": 0, "exclusiveMaximum": 100},
            }
        ]
    )

    assert exprs["n_exclusive_min"] == "n > 0"
    assert exprs["n_exclusive_max"] == "n < 100"


def test_constraints_derive_date_bounds_as_quoted_literals():
    exprs = _expectations(
        [
            {
                "name": "d",
                "logicalType": "date",
                "logicalTypeOptions": {
                    "minimum": "2020-01-01",
                    "maximum": "2030-12-31",
                },
            }
        ]
    )

    assert exprs["d_min"] == "d >= '2020-01-01'"
    assert exprs["d_max"] == "d <= '2030-12-31'"


def test_constraints_derive_timestamp_exclusive_bounds():
    exprs = _expectations(
        [
            {
                "name": "ts",
                "logicalType": "timestamp",
                "logicalTypeOptions": {
                    "exclusiveMinimum": "2020-01-01T00:00:00",
                    "exclusiveMaximum": "2030-01-01T00:00:00",
                },
            }
        ]
    )

    assert exprs["ts_exclusive_min"] == "ts > '2020-01-01T00:00:00'"
    assert exprs["ts_exclusive_max"] == "ts < '2030-01-01T00:00:00'"


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

    assert exprs["arr_max_items"] == "arr IS NULL OR (size(arr) <= 5)"


def test_constraints_guard_object_required_fields():
    exprs = _expectations(
        [
            {
                "name": "o",
                "logicalType": "object",
                "logicalTypeOptions": {"required": ["street"]},
            }
        ]
    )

    assert exprs["o_street_not_null"] == "o IS NULL OR (o.street IS NOT NULL)"


def test_constraints_render_float_multiple_of_without_trailing_zero():
    exprs = _expectations(
        [
            {
                "name": "n",
                "logicalType": "number",
                "logicalTypeOptions": {"multipleOf": 1.0},
            }
        ]
    )

    assert exprs["n_multiple_of"] == "n % 1 = 0"
