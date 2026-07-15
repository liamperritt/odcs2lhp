"""Behaviour tests for project/contract discovery (public API only)."""

from __future__ import annotations

import shutil

from odcs2lhp.discovery import (
    SCD2_COLUMNS,
    contract_stem,
    discover_contracts,
    exclusion_columns,
    find_project_root,
    read_operational_metadata_columns,
)


def test_find_project_root_returns_dir_containing_lhp_yaml(tmp_path):
    (tmp_path / "lhp.yaml").write_text("name: p\n")
    nested = tmp_path / "pipelines" / "bronze"
    nested.mkdir(parents=True)

    assert find_project_root(nested) == tmp_path


def test_find_project_root_returns_none_when_no_lhp_yaml_on_path(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert find_project_root(nested) is None


def test_read_operational_metadata_columns_reads_declared_names(tmp_path, fixtures_dir):
    shutil.copy(fixtures_dir / "lhp.yaml", tmp_path / "lhp.yaml")

    assert read_operational_metadata_columns(tmp_path) == frozenset(
        {"_processing_timestamp"}
    )


def test_read_operational_metadata_columns_is_empty_when_lhp_yaml_absent(tmp_path):
    assert read_operational_metadata_columns(tmp_path) == frozenset()


def test_read_operational_metadata_columns_is_empty_when_root_is_none():
    assert read_operational_metadata_columns(None) == frozenset()


def test_exclusion_columns_unions_operational_metadata_with_scd2(tmp_path, fixtures_dir):
    shutil.copy(fixtures_dir / "lhp.yaml", tmp_path / "lhp.yaml")

    result = exclusion_columns(tmp_path)

    assert result == frozenset({"_processing_timestamp"}) | SCD2_COLUMNS


def test_exclusion_columns_contains_scd2_when_no_lhp_yaml(tmp_path):
    assert exclusion_columns(tmp_path) == SCD2_COLUMNS


def test_discover_contracts_finds_yaml_and_yml_recursively(tmp_path):
    (tmp_path / "a.yaml").write_text("x: 1\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.yml").write_text("y: 2\n")

    found = discover_contracts(tmp_path)

    assert [p.name for p in found] == ["a.yaml", "b.yml"]


def test_discover_contracts_returns_empty_when_dir_missing(tmp_path):
    assert discover_contracts(tmp_path / "does_not_exist") == []


def test_contract_stem_splits_on_first_dot_when_multi_suffix():
    assert contract_stem("sales.contract.yaml") == "sales"
    assert contract_stem("orders.odcs.yaml") == "orders"


def test_contract_stem_uses_leading_segment_when_single_suffix():
    assert contract_stem("customer.yaml") == "customer"
