"""Behaviour tests for the CLI (public API only, via click's CliRunner)."""

from __future__ import annotations

import shutil

from click.testing import CliRunner

import pytest

from odcs2lhp.cli import cli, main

from .conftest import load_yaml


def _make_project(tmp_path, fixtures_dir, *, with_lhp_yaml=True, contracts="contracts"):
    """Build a throwaway project: lhp.yaml + a contracts dir with the sales contract."""
    if with_lhp_yaml:
        shutil.copy(fixtures_dir / "lhp.yaml", tmp_path / "lhp.yaml")
    contracts_dir = tmp_path / contracts
    contracts_dir.mkdir(parents=True)
    shutil.copy(fixtures_dir / "sales.contract.yaml", contracts_dir / "sales.contract.yaml")
    return tmp_path


def test_cli_defaults_contracts_dir_to_contracts_when_arg_omitted(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)

    result = CliRunner().invoke(cli, ["--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / ".lhp" / "odcs" / "sales" / "1.0.0" / "load" / "schemas").is_dir()


def test_cli_writes_all_five_artifact_kinds_under_dot_lhp_odcs(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)

    result = CliRunner().invoke(cli, ["--project-root", str(tmp_path)])

    assert result.exit_code == 0
    odcs = tmp_path / ".lhp" / "odcs"
    assert (odcs / "sales" / "1.0.0" / "load" / "schemas" / "customer_schema.yaml").is_file()
    assert (odcs / "sales" / "1.0.0" / "transform" / "schemas" / "customer_transform.yaml").is_file()
    assert (odcs / "sales" / "1.0.0" / "write" / "schemas" / "customer_schema.yaml").is_file()
    assert (odcs / "sales" / "1.0.0" / "write" / "tags" / "customer_tags.yaml").is_file()
    assert (odcs / "sales" / "1.0.0" / "transform" / "expectations" / "customer_expectations.yaml").is_file()


def test_cli_honours_custom_contracts_dir_arg(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir, contracts="data_contracts")

    result = CliRunner().invoke(
        cli, ["--project-root", str(tmp_path), "--contracts-dir", "data_contracts"]
    )

    assert result.exit_code == 0
    assert (tmp_path / ".lhp" / "odcs" / "sales" / "1.0.0" / "load" / "schemas").is_dir()


def test_cli_excludes_operational_metadata_when_lhp_yaml_declares_it(
    tmp_path, fixtures_dir
):
    _make_project(tmp_path, fixtures_dir, with_lhp_yaml=True)

    CliRunner().invoke(cli, ["--project-root", str(tmp_path)])

    load_schema = load_yaml(
        tmp_path / ".lhp" / "odcs" / "sales" / "1.0.0" / "load" / "schemas" / "customer_schema.yaml"
    )
    names = {c["name"] for c in load_schema["columns"]}
    assert "_processing_timestamp" not in names


def test_cli_writes_output_to_custom_output_dir_when_given(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)
    out = tmp_path / "custom_out"

    result = CliRunner().invoke(
        cli, ["--project-root", str(tmp_path), "--output-dir", str(out)]
    )

    assert result.exit_code == 0
    assert (out / "sales" / "1.0.0" / "load" / "schemas" / "customer_schema.yaml").is_file()


def test_cli_reports_no_contracts_when_dir_empty(tmp_path):
    (tmp_path / "lhp.yaml").write_text("name: p\n")
    (tmp_path / "contracts").mkdir()

    result = CliRunner().invoke(cli, ["--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "No ODCS contracts found" in result.output


def test_cli_exits_nonzero_when_a_contract_is_invalid(tmp_path, fixtures_dir):
    (tmp_path / "lhp.yaml").write_text("name: p\n")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    shutil.copy(fixtures_dir / "broken.contract.yaml", contracts_dir / "broken.contract.yaml")

    result = CliRunner().invoke(cli, ["--project-root", str(tmp_path)])

    assert result.exit_code != 0


def test_cli_produces_one_artifact_set_per_object_when_multi_object(
    tmp_path, fixtures_dir
):
    (tmp_path / "lhp.yaml").write_text("name: p\n")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    shutil.copy(fixtures_dir / "multi.odcs.yaml", contracts_dir / "multi.odcs.yaml")

    result = CliRunner().invoke(cli, ["--project-root", str(tmp_path)])

    assert result.exit_code == 0
    write_dir = tmp_path / ".lhp" / "odcs" / "multi" / "2.0" / "write" / "schemas"
    assert (write_dir / "orders_schema.yaml").is_file()
    assert (write_dir / "products_schema.yaml").is_file()


def test_cli_lists_each_file_when_verbose(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)

    result = CliRunner().invoke(cli, ["--project-root", str(tmp_path), "-v"])

    assert result.exit_code == 0
    assert "wrote" in result.output
    assert "customer_schema.yaml" in result.output


def test_cli_discovers_project_root_by_walking_up_when_not_given(
    tmp_path, fixtures_dir, monkeypatch
):
    _make_project(tmp_path, fixtures_dir)
    nested = tmp_path / "pipelines"
    nested.mkdir()
    monkeypatch.chdir(nested)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert (tmp_path / ".lhp" / "odcs" / "sales" / "1.0.0" / "load" / "schemas").is_dir()


def test_main_exits_one_when_contract_invalid(tmp_path, fixtures_dir, monkeypatch):
    (tmp_path / "lhp.yaml").write_text("name: p\n")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    shutil.copy(
        fixtures_dir / "broken.contract.yaml", contracts_dir / "broken.contract.yaml"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["odcs2lhp"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
