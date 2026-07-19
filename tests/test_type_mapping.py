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
    artifacts = translate_contract(contract, stem="c")
    write = next(
        a.data for a in artifacts if a.relative_path == "c/1.0/write/schemas/t_schema.yaml"
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
        if a.relative_path == "c/1.0/transform/expectations/t_expectations.yaml"
    )
    return {e["name"]: e["expression"] for e in exp["expectations"]}


def test_type_mapping_uses_physical_type_verbatim_when_present():
    cols = _write_columns([{"name": "c", "physicalType": "DECIMAL(9,3)"}])

    assert cols["c"]["type"] == "DECIMAL(9,3)"


def test_type_mapping_raises_when_physical_type_missing():
    contract = {
        "version": "1.0",
        "schema": [{"name": "t", "properties": [{"name": "c", "logicalType": "integer"}]}],
    }

    with pytest.raises(Odcs2LhpError) as exc_info:
        translate_contract(contract, stem="c")

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
