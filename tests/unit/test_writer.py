"""Behaviour tests for the sidecar writer (public API only)."""

from __future__ import annotations

from odcs2lhp.translator import Artifact
from odcs2lhp.writer import reset_output_dir, write_artifacts

from .conftest import load_yaml


def _artifacts():
    return [
        Artifact("schemas/load/a_schema.yaml", {"name": "a", "columns": []}),
        Artifact("expectations/a_expectations.yaml", {"table": "a", "expectations": []}),
    ]


def test_write_artifacts_creates_nested_output_tree(tmp_path):
    write_artifacts(_artifacts(), tmp_path)

    assert (tmp_path / "schemas" / "load" / "a_schema.yaml").is_file()
    assert (tmp_path / "expectations" / "a_expectations.yaml").is_file()


def test_write_artifacts_returns_written_paths_in_order(tmp_path):
    written = write_artifacts(_artifacts(), tmp_path)

    assert [p.name for p in written] == ["a_schema.yaml", "a_expectations.yaml"]


def test_write_artifacts_round_trips_data_as_yaml(tmp_path):
    write_artifacts(_artifacts(), tmp_path)

    assert load_yaml(tmp_path / "schemas" / "load" / "a_schema.yaml") == {
        "name": "a",
        "columns": [],
    }


def test_write_artifacts_preserves_key_order(tmp_path):
    data = {"name": "z", "version": "1.0", "columns": []}
    write_artifacts([Artifact("s.yaml", data)], tmp_path)

    text = (tmp_path / "s.yaml").read_text(encoding="utf-8")
    assert text.index("name:") < text.index("version:") < text.index("columns:")


def test_write_artifacts_overwrites_cleanly_on_rerun(tmp_path):
    write_artifacts([Artifact("s.yaml", {"n": 1})], tmp_path)
    write_artifacts([Artifact("s.yaml", {"n": 2})], tmp_path)

    assert load_yaml(tmp_path / "s.yaml") == {"n": 2}


# --- reset_output_dir -------------------------------------------------------


def test_reset_output_dir_removes_existing_files(tmp_path):
    out = tmp_path / "odcs"
    stale = out / "stale" / "old.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("gone: true\n", encoding="utf-8")

    reset_output_dir(out)

    assert out.is_dir()
    assert list(out.iterdir()) == []


def test_reset_output_dir_creates_dir_when_absent(tmp_path):
    out = tmp_path / "nested" / "odcs"

    reset_output_dir(out)

    assert out.is_dir()
