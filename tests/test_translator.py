"""Behaviour tests for contract translation (public API only).

Every test drives :func:`odcs2lhp.translator.translate_contract` and asserts on
the returned :class:`Artifact` data — never on private helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from odcs2lhp.discovery import SCD2_COLUMNS
from odcs2lhp.errors import Odcs2LhpError
from odcs2lhp.parser import OdcsParser
from odcs2lhp.translator import Artifact, translate_contract

EXCLUDE = frozenset({"_processing_timestamp"}) | SCD2_COLUMNS


def _artifact(artifacts: List[Artifact], relative_path: str) -> Dict[str, Any]:
    for artifact in artifacts:
        if artifact.relative_path == relative_path:
            return artifact.data
    raise AssertionError(f"no artifact at {relative_path}; got "
                         f"{[a.relative_path for a in artifacts]}")


def _column(schema: Dict[str, Any], name: str) -> Dict[str, Any]:
    for column in schema["columns"]:
        if column["name"] == name:
            return column
    raise AssertionError(f"no column {name!r} in {schema['name']}")


@pytest.fixture
def sales(sales_contract_path) -> List[Artifact]:
    contract = OdcsParser().parse(sales_contract_path)
    return translate_contract(contract, stem="sales", exclude=EXCLUDE)


# --- artifact set -----------------------------------------------------------


def test_translate_contract_emits_five_artifacts_per_object(sales):
    assert sorted(a.relative_path for a in sales) == [
        "sales/1.0.0/load/schemas/customer_schema.yaml",
        "sales/1.0.0/transform/expectations/customer_expectations.yaml",
        "sales/1.0.0/transform/schemas/customer_transform.yaml",
        "sales/1.0.0/write/schemas/customer_schema.yaml",
        "sales/1.0.0/write/tags/customer_tags.yaml",
    ]


def test_translate_contract_nests_output_under_stem_and_version(sales):
    for artifact in sales:
        assert artifact.relative_path.startswith("sales/1.0.0/")


def test_translate_contract_emits_one_artifact_set_per_object_when_multi_object(
    multi_contract_path,
):
    contract = OdcsParser().parse(multi_contract_path)

    artifacts = translate_contract(contract, stem="multi")

    paths = {a.relative_path for a in artifacts}
    assert "multi/2.0/write/schemas/orders_schema.yaml" in paths
    assert "multi/2.0/write/schemas/products_schema.yaml" in paths
    assert len(artifacts) == 10


# --- load schema ------------------------------------------------------------


def test_load_schema_names_columns_by_physical_name(sales):
    schema = _artifact(sales, "sales/1.0.0/load/schemas/customer_schema.yaml")

    assert _column(schema, "cust id")["type"] == "BIGINT"
    assert not any(c["name"] == "customer_id" for c in schema["columns"])


def test_load_schema_marks_required_property_not_nullable(sales):
    schema = _artifact(sales, "sales/1.0.0/load/schemas/customer_schema.yaml")

    assert _column(schema, "cust id")["nullable"] is False
    assert _column(schema, "email")["nullable"] is True


def test_load_schema_excludes_operational_metadata_when_in_exclusion_set(sales):
    schema = _artifact(sales, "sales/1.0.0/load/schemas/customer_schema.yaml")

    assert not any(c["name"] == "_processing_timestamp" for c in schema["columns"])


def test_load_schema_excludes_scd2_columns_always(sales):
    schema = _artifact(sales, "sales/1.0.0/load/schemas/customer_schema.yaml")

    names = {c["name"] for c in schema["columns"]}
    assert names.isdisjoint(SCD2_COLUMNS)


def test_load_schema_maps_array_logical_type_to_ddl(sales):
    schema = _artifact(sales, "sales/1.0.0/load/schemas/customer_schema.yaml")

    assert _column(schema, "labels")["type"] == "ARRAY<STRING>"


# --- transform schema -------------------------------------------------------


def test_transform_schema_renames_only_when_physical_name_differs(sales):
    schema = _artifact(sales, "sales/1.0.0/transform/schemas/customer_transform.yaml")

    assert schema["column_mapping"] == {"cust id": "customer_id"}


def test_transform_schema_casts_every_kept_column(sales):
    schema = _artifact(sales, "sales/1.0.0/transform/schemas/customer_transform.yaml")

    assert schema["type_casting"]["customer_id"] == "BIGINT"
    assert schema["type_casting"]["email"] == "STRING"
    assert schema["type_casting"]["labels"] == "ARRAY<STRING>"


def test_transform_schema_excludes_operational_metadata_and_scd2(sales):
    schema = _artifact(sales, "sales/1.0.0/transform/schemas/customer_transform.yaml")

    excluded = {"_processing_timestamp"} | SCD2_COLUMNS
    assert set(schema["type_casting"]).isdisjoint(excluded)


def test_transform_schema_omits_column_mapping_when_no_renames(multi_contract_path):
    contract = OdcsParser().parse(multi_contract_path)
    artifacts = translate_contract(contract, stem="multi")

    schema = _artifact(artifacts, "multi/2.0/transform/schemas/orders_transform.yaml")

    assert "column_mapping" not in schema


# --- write schema -----------------------------------------------------------


def test_write_schema_keeps_operational_metadata_and_scd2_columns(sales):
    schema = _artifact(sales, "sales/1.0.0/write/schemas/customer_schema.yaml")

    names = {c["name"] for c in schema["columns"]}
    assert "_processing_timestamp" in names
    assert SCD2_COLUMNS.issubset(names)


def test_write_schema_uses_contract_names_not_physical_names(sales):
    schema = _artifact(sales, "sales/1.0.0/write/schemas/customer_schema.yaml")

    assert any(c["name"] == "customer_id" for c in schema["columns"])
    assert not any(c["name"] == "cust id" for c in schema["columns"])


def test_write_schema_attaches_column_tags_when_property_declares_them(sales):
    schema = _artifact(sales, "sales/1.0.0/write/schemas/customer_schema.yaml")

    assert _column(schema, "email")["tags"] == {"pii": "email", "sensitive": ""}


def test_write_schema_omits_tags_key_when_property_has_no_tags(sales):
    schema = _artifact(sales, "sales/1.0.0/write/schemas/customer_schema.yaml")

    assert "tags" not in _column(schema, "signup_count")


def test_write_schema_orders_primary_key_by_position(sales):
    schema = _artifact(sales, "sales/1.0.0/write/schemas/customer_schema.yaml")

    assert schema["primary_key"] == ["tenant_id", "customer_id"]


# --- tags file --------------------------------------------------------------


def test_tags_file_maps_object_tags_with_key_value_convention(sales):
    tags = _artifact(sales, "sales/1.0.0/write/tags/customer_tags.yaml")

    assert tags == {
        "version": "1.0",
        "table": "customer",
        "tags": {"domain": "sales", "layer": "bronze", "pii": ""},
    }


def test_tags_file_has_empty_tags_when_object_declares_none(multi_contract_path):
    contract = OdcsParser().parse(multi_contract_path)
    artifacts = translate_contract(contract, stem="multi")

    tags = _artifact(artifacts, "multi/2.0/write/tags/orders_tags.yaml")

    assert tags["tags"] == {}


# --- expectations file ------------------------------------------------------


def test_expectations_emit_not_null_when_property_required(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    entries = {e["name"]: e for e in exp["expectations"]}
    assert entries["customer_id_not_null"]["expression"] == "`customer_id` IS NOT NULL"


def test_expectations_backtick_quote_required_column_when_name_has_special_chars():
    contract = {
        "version": "1.0",
        "schema": [
            {
                "name": "t",
                "properties": [
                    {
                        "name": "cust id",
                        "logicalType": "string",
                        "physicalType": "STRING",
                        "required": True,
                    }
                ],
            }
        ],
    }
    artifacts = translate_contract(contract, stem="c")

    exp = _artifact(artifacts, "c/1.0/transform/expectations/t_expectations.yaml")

    entries = {e["name"]: e for e in exp["expectations"]}
    assert entries["cust_id_not_null"]["expression"] == "`cust id` IS NOT NULL"


def test_expectations_omit_not_null_when_property_not_required(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    names = {e["name"] for e in exp["expectations"]}
    assert "email_not_null" not in names


def test_expectations_derive_string_predicates_from_logical_type_options(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    by_name = {e["name"]: e["expression"] for e in exp["expectations"]}
    assert by_name["email_min_length"] == "length(`email`) >= 3"
    assert by_name["email_max_length"] == "length(`email`) <= 320"
    assert by_name["email_pattern"] == r"`email` RLIKE '^[^@]+@[^@]+\.[^@]+$'"


def test_expectations_guard_array_predicates_against_null(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    by_name = {e["name"]: e["expression"] for e in exp["expectations"]}
    assert by_name["labels_min_items"] == "`labels` IS NULL OR (size(`labels`) >= 1)"
    assert (
        by_name["labels_unique_items"]
        == "`labels` IS NULL OR (size(`labels`) = size(array_distinct(`labels`)))"
    )


def test_expectations_set_failure_action_fail_when_critical_data_element(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    entries = {e["name"]: e for e in exp["expectations"]}
    assert entries["customer_id_not_null"]["failureAction"] == "fail"
    assert entries["customer_id_min"]["failureAction"] == "fail"


def test_expectations_set_failure_action_warn_when_not_critical(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    entries = {e["name"]: e for e in exp["expectations"]}
    assert entries["email_min_length"]["failureAction"] == "warn"


def test_expectations_use_wrapped_shape_with_version_and_table(sales):
    exp = _artifact(sales, "sales/1.0.0/transform/expectations/customer_expectations.yaml")

    assert exp["version"] == "1.0"
    assert exp["table"] == "customer"
    assert isinstance(exp["expectations"], list)


# --- errors -----------------------------------------------------------------


def test_translate_contract_raises_when_physical_type_missing():
    contract = {
        "version": "1.0",
        "schema": [
            {"name": "t", "properties": [{"name": "c", "logicalType": "string"}]}
        ],
    }

    with pytest.raises(Odcs2LhpError) as exc_info:
        translate_contract(contract, stem="t")

    assert exc_info.value.code == "ODCS-TYPE-001"
