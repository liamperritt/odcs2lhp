"""End-to-end golden-snapshot test for ``odcs2lhp translate``.

Drives the real ``translate`` CLI over a committed example LHP project
(``test_project/``) containing several valid ODCS contracts of differing shapes,
and compares the full generated ``.lhp/odcs/`` tree against committed golden
files under ``expected/`` — both as parsed YAML (clear structural diffs) and as
raw bytes (catches key-ordering/formatting regressions).

This replaces ad-hoc manual verification: after an intentional behaviour change,
regenerate the goldens with ``ODCS2LHP_REGEN=1`` and review the git diff::

    ODCS2LHP_REGEN=1 uv run pytest tests/integration -q
    git diff tests/integration/expected

By default the project is copied into pytest's ``tmp_path`` (auto-cleaned by pytest
after a few runs) so the committed source stays untouched. To keep the generated
files around for manual inspection, set ``ODCS2LHP_KEEP_OUTPUT=1`` — the run then
translates the committed ``test_project/`` in place, leaving the output under
``test_project/.lhp/odcs/`` (already gitignored, just like a real project)::

    ODCS2LHP_KEEP_OUTPUT=1 uv run pytest tests/integration -q -s

Only valid contracts (successful runs) are exercised here; failure cases live in
the unit suite.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict

import yaml
from click.testing import CliRunner

from odcs2lhp.cli import cli

ROOT = Path(__file__).parent
TEST_PROJECT = ROOT / "test_project"
EXPECTED = ROOT / "expected"

REGEN = os.environ.get("ODCS2LHP_REGEN") == "1"
KEEP_OUTPUT = os.environ.get("ODCS2LHP_KEEP_OUTPUT") == "1"


def _load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _translate_into(tmp_path: Path) -> Path:
    """Run translate against the example project and return its output dir.

    By default the project is copied into ``tmp_path`` (auto-cleaned) so the
    committed source is never mutated. When ``ODCS2LHP_KEEP_OUTPUT=1``, translate
    runs against the committed ``test_project/`` in place, leaving the generated
    ``.lhp/odcs/`` tree (gitignored) for inspection — exactly how a real project
    behaves. The ``translate`` command wipes ``.lhp/odcs/`` at the start of each
    run, so re-running stays idempotent.
    """
    if KEEP_OUTPUT:
        project = TEST_PROJECT
    else:
        project = tmp_path / "project"
        shutil.copytree(TEST_PROJECT, project)

    result = CliRunner().invoke(cli, ["translate", "--project-root", str(project)])
    assert result.exit_code == 0, result.output

    output_dir = project / ".lhp" / "odcs"
    if KEEP_OUTPUT:
        print(f"\n[ODCS2LHP_KEEP_OUTPUT] generated files left at: {output_dir}")
    return output_dir


def _rel_output_files(root: Path) -> Dict[str, Path]:
    """All generated sidecars (YAML + generated Python transform modules)."""
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and p.suffix in (".yaml", ".py")
    }


def _regenerate(output_dir: Path) -> None:
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    shutil.copytree(output_dir, EXPECTED)


def test_translate_matches_golden_snapshot(tmp_path):
    output_dir = _translate_into(tmp_path)

    if REGEN:
        _regenerate(output_dir)
        return

    generated = _rel_output_files(output_dir)
    expected = _rel_output_files(EXPECTED)

    # Same set of sidecar files (catches added/removed/renamed outputs).
    assert set(generated) == set(expected)

    for rel, exp_path in expected.items():
        # For YAML, first compare parsed content: a structural/value change gives
        # a clear diff. Generated .py modules are compared by raw bytes only.
        if rel.endswith(".yaml"):
            assert _load_yaml(generated[rel]) == _load_yaml(exp_path), rel
        # Then compare raw bytes: catches key-ordering/formatting regressions
        # (the writer emits with sort_keys=False to preserve authored order),
        # which parsed-YAML equality alone would miss.
        assert (
            generated[rel].read_text(encoding="utf-8")
            == exp_path.read_text(encoding="utf-8")
        ), rel


def test_translate_produces_one_output_tree_per_contract(tmp_path):
    output_dir = _translate_into(tmp_path)

    prefixes = {p.relative_to(output_dir).parts[0] for p in output_dir.rglob("*.yaml")}

    # 'inventory.odcs.yaml' keeps its inner '.odcs' (only the final extension is stripped).
    assert prefixes == {
        "sales.contract",
        "inventory.odcs",
        "marketing",
        "minimal",
        "type_matrix",
    }
