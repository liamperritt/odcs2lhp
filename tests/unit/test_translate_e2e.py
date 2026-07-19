"""End-to-end tests driving the whole flow via the CLI.

These run the real ``translate`` command through click's ``CliRunner`` so
discovery + parser + mapper + translator + writer are all exercised together,
then assert on the on-disk YAML content (not just file existence).
"""

from __future__ import annotations

import shutil

from click.testing import CliRunner

from odcs2lhp.cli import cli

from .conftest import load_yaml


def _make_project(tmp_path, fixtures_dir, contract="sales.contract.yaml"):
    """Build a throwaway project: lhp.yaml + contracts/<contract>."""
    shutil.copy(fixtures_dir / "lhp.yaml", tmp_path / "lhp.yaml")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(parents=True)
    shutil.copy(fixtures_dir / contract, contracts_dir / contract)
    return tmp_path


def _translate(tmp_path):
    return CliRunner().invoke(cli, ["translate", "--project-root", str(tmp_path)])


# --- passing flows ----------------------------------------------------------


def test_e2e_translate_sales_contract_produces_expected_sidecars(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)

    result = _translate(tmp_path)

    assert result.exit_code == 0
    base = tmp_path / ".lhp" / "odcs" / "sales.contract"

    load = load_yaml(base / "load" / "schemas" / "customer_schema.yaml")
    load_names = {c["name"] for c in load["columns"]}
    assert "cust id" in load_names  # physicalName used on read
    assert "_processing_timestamp" not in load_names  # OM dropped
    assert "__START_AT" not in load_names and "__END_AT" not in load_names  # SCD2 dropped

    transform = load_yaml(base / "transform" / "schemas" / "customer_transform.yaml")
    assert transform["column_mapping"] == {"cust id": "customer_id"}
    assert transform["type_casting"]["customer_id"] == "BIGINT"

    write = load_yaml(base / "write" / "schemas" / "customer_schema.yaml")
    write_names = {c["name"] for c in write["columns"]}
    assert "customer_id" in write_names  # logical name after transform
    assert "_processing_timestamp" in write_names  # OM kept on write
    assert write["primary_key"] == ["tenant_id", "customer_id"]
    assert all("tags" not in c for c in write["columns"])  # column tags moved out

    tags = load_yaml(base / "write" / "uc_tags" / "customer_tags.yaml")
    assert tags["table"] == "customer"
    assert tags["tags"] == {"domain": "sales", "layer": "bronze", "pii": ""}
    col_tags = {c["name"]: c["tags"] for c in tags["columns"]}
    assert col_tags["email"] == {"pii": "email", "sensitive": ""}
    assert col_tags["tenant_id"] == {}  # untagged column present with empty tags

    exp = load_yaml(base / "transform" / "expectations" / "customer_expectations.yaml")
    entries = {e["name"]: e for e in exp["expectations"]}
    assert entries["customer_id_not_null"]["expression"] == "`customer_id` IS NOT NULL"


def test_e2e_translate_multi_object_contract_writes_all_objects(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir, contract="multi.odcs.yaml")

    result = _translate(tmp_path)

    assert result.exit_code == 0
    base = tmp_path / ".lhp" / "odcs" / "multi.odcs"
    files = sorted(p.relative_to(base).as_posix() for p in base.rglob("*.yaml"))
    assert files == [
        "load/schemas/orders_schema.yaml",
        "load/schemas/products_schema.yaml",
        "transform/expectations/orders_expectations.yaml",
        "transform/expectations/products_expectations.yaml",
        "transform/schemas/orders_transform.yaml",
        "transform/schemas/products_transform.yaml",
        "write/schemas/orders_schema.yaml",
        "write/schemas/products_schema.yaml",
        "write/uc_tags/orders_tags.yaml",
        "write/uc_tags/products_tags.yaml",
    ]


def test_e2e_translate_is_idempotent_and_removes_stale_output(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)
    odcs = tmp_path / ".lhp" / "odcs"

    first = _translate(tmp_path)
    assert first.exit_code == 0
    tree_after_first = sorted(p.relative_to(odcs).as_posix() for p in odcs.rglob("*.yaml"))

    orphan = odcs / "sales" / "old" / "orphan.yaml"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("stale: true\n", encoding="utf-8")

    second = _translate(tmp_path)
    assert second.exit_code == 0
    assert not orphan.exists()  # wiped
    tree_after_second = sorted(p.relative_to(odcs).as_posix() for p in odcs.rglob("*.yaml"))
    assert tree_after_second == tree_after_first  # fresh + idempotent


def test_e2e_translate_leaves_output_untouched_when_no_contracts(tmp_path):
    (tmp_path / "lhp.yaml").write_text("name: p\n", encoding="utf-8")
    (tmp_path / "contracts").mkdir()
    keep = tmp_path / ".lhp" / "odcs" / "previous.yaml"
    keep.parent.mkdir(parents=True)
    keep.write_text("keep: me\n", encoding="utf-8")

    result = _translate(tmp_path)

    assert result.exit_code == 0
    assert "No ODCS contracts found" in result.output
    assert keep.exists()  # no wipe when there is nothing to translate


# --- failing flows ----------------------------------------------------------


def test_e2e_translate_exits_nonzero_when_contract_invalid(tmp_path, fixtures_dir):
    (tmp_path / "lhp.yaml").write_text("name: p\n", encoding="utf-8")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    shutil.copy(
        fixtures_dir / "broken.contract.yaml", contracts_dir / "broken.contract.yaml"
    )

    result = _translate(tmp_path)

    assert result.exit_code != 0
    assert not (tmp_path / ".lhp" / "odcs").exists() or not list(
        (tmp_path / ".lhp" / "odcs").rglob("*.yaml")
    )


def test_e2e_translate_exits_nonzero_when_contract_empty(tmp_path):
    (tmp_path / "lhp.yaml").write_text("name: p\n", encoding="utf-8")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "empty.contract.yaml").write_text("", encoding="utf-8")

    result = _translate(tmp_path)

    assert result.exit_code != 0


def test_e2e_translate_preserves_prior_output_when_a_contract_fails(tmp_path, fixtures_dir):
    _make_project(tmp_path, fixtures_dir)
    # A second contract that fails ODCS validation.
    shutil.copy(
        fixtures_dir / "broken.contract.yaml",
        tmp_path / "contracts" / "broken.contract.yaml",
    )
    prior = tmp_path / ".lhp" / "odcs" / "prior.yaml"
    prior.parent.mkdir(parents=True)
    prior.write_text("keep: me\n", encoding="utf-8")

    result = _translate(tmp_path)

    # The whole run fails, and because the wipe happens only after every
    # contract parses/translates, the prior output survives untouched.
    assert result.exit_code != 0
    assert prior.exists()


def test_e2e_translate_raises_when_two_contracts_share_basename(tmp_path, fixtures_dir):
    (tmp_path / "lhp.yaml").write_text("name: p\n", encoding="utf-8")
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    # Same basename, differing only by extension -> identical output prefix "sales".
    shutil.copy(fixtures_dir / "sales.contract.yaml", contracts_dir / "sales.yaml")
    shutil.copy(fixtures_dir / "sales.contract.yaml", contracts_dir / "sales.yml")
    prior = tmp_path / ".lhp" / "odcs" / "prior.yaml"
    prior.parent.mkdir(parents=True)
    prior.write_text("keep: me\n", encoding="utf-8")

    result = _translate(tmp_path)

    assert result.exit_code != 0
    assert prior.exists()  # wipe never runs; prior output survives
    assert not (tmp_path / ".lhp" / "odcs" / "sales").exists()
