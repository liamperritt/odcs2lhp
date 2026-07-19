# AGENTS.md

Guidance for coding agents working in this repository. Read this before making
changes. It is tool-agnostic — it applies to any coding agent or human contributor.

## What this project does

`odcs2lhp` is a standalone Python package (and `odcs2lhp` CLI) that translates
[ODCS](https://bitol.io/) (Open Data Contract Standard) data contracts into
[Lakehouse Plumber](https://github.com/Mmodarre/Lakehouse_Plumber) (LHP) YAML
**sidecar files**. It reads only your ODCS contract files and your project's
`lhp.yaml`; it never inspects pipeline YAMLs or any other files.

For every schema object in every discovered contract, it writes five sidecars
under `.lhp/odcs/<prefix>/` (which LHP gitignores), where `<prefix>` mirrors the
contract file's path under the contracts dir plus its filename without the final
extension:

| Sidecar | Path | Purpose |
|---|---|---|
| Load schema | `<prefix>/load/schemas/<obj>_schema.yaml` | cloudFiles read schema; columns named by `physicalName`; excludes operational-metadata + SCD2 columns |
| Transform schema | `<prefix>/transform/schemas/<obj>_transform.yaml` | `column_mapping` (renames) + `type_casting` for a `transform_type: schema` action |
| Expectations | `<prefix>/transform/expectations/<obj>_expectations.yaml` | `required` → NOT NULL plus `logicalTypeOptions` predicates |
| Write schema | `<prefix>/write/schemas/<obj>_schema.yaml` | table schema, all columns, logical names, `primary_key` (no tags) |
| UC tags | `<prefix>/write/uc_tags/<obj>_tags.yaml` | table-level `tags` + per-column `columns` list of `{name, tags}` |

Each run wipes and rebuilds the output directory, so the sidecars are always a
fresh reflection of the contracts.

### Module map (`src/odcs2lhp/`)

- `cli.py` — Click command group; `translate` subcommand orchestrates the flow.
- `discovery.py` — locate the project root, read `lhp.yaml` operational-metadata /
  SCD2 exclusions, find contract files, compute the output prefix.
- `parser.py` — load + validate an ODCS contract against the bundled JSON schema.
- `mapper.py` — pure ODCS→target mappers (types, UC tags, constraint predicates,
  name/identifier helpers). No I/O.
- `translator.py` — turn a parsed contract into the list of `Artifact` sidecars.
- `writer.py` — write `Artifact`s to disk as YAML.
- `errors.py` — the single `Odcs2LhpError` exception type (carries a `code`,
  message, and optional suggestions).
- `schemas/` — the bundled ODCS JSON schema (vendored verbatim; do not edit).

## Environment & commands

- Python `>=3.11`. The project uses [`uv`](https://docs.astral.sh/uv/) (`uv.lock`).
- **Use a `.venv` virtual environment.** Create it with `uv venv` (or
  `python -m venv .venv`) and work inside it — either activate it
  (`source .venv/bin/activate`) or let `uv run ...` use it automatically. Do not
  install into or run against the system Python.
- Run the whole test suite: `uv run pytest`
- Run one area: `uv run pytest tests/unit` or `uv run pytest tests/integration`
- Run the CLI: `uv run odcs2lhp translate --project-root <path>`
- Install for local dev: `pip install -e .` (or `uv sync`).

## Test layout

- `tests/unit/` — fast, behaviour-driven tests of the public API. `conftest.py`
  holds shared fixtures and the `load_yaml` helper; `fixtures/` holds sample
  contracts and an `lhp.yaml`.
- `tests/integration/` — a golden end-to-end test. `test_project/` is a realistic
  LHP project (its own `lhp.yaml` + several varied ODCS contracts); `expected/`
  is the committed golden output tree. The test runs the real `translate` CLI and
  compares the generated `.lhp/odcs/` tree against `expected/` both as parsed YAML
  and as raw bytes.
  - Regenerate goldens after an **intentional** output change:
    `ODCS2LHP_REGEN=1 uv run pytest tests/integration -q`, then review
    `git diff tests/integration/expected`.
  - Keep generated files for manual inspection:
    `ODCS2LHP_KEEP_OUTPUT=1 uv run pytest tests/integration -q -s` (writes to
    `test_project/.lhp/odcs/`, which is gitignored).

## Required workflow: TDD/BDD (RED → GREEN → refactor)

All implementation work follows this cycle. Do not skip it, and make the RED and
GREEN gates visible (run the suite at each).

1. **Stub** the new class/method/function first — signature plus
   `raise NotImplementedError` (or an equivalent placeholder).
2. **Write the tests**, run them, and show they **FAIL (RED)**.
3. **Implement** until the tests **PASS (GREEN)**.
4. **Review and refactor** (see principles below) while keeping tests green.

When a change alters generated output, add/adjust the unit test(s) for the
behaviour, then regenerate the integration goldens and review the diff.

### Test naming convention (BDD style)

Name tests for the behaviour they assert, using one of:

- `test_<subject>_<does_behaviour>`
- `test_<subject>_<does_behaviour>_<if|on|when|given|where>_<some_state>`

Examples from this repo:

- `test_load_schema_marks_required_property_not_nullable`
- `test_tags_file_object_tag_overrides_contract_tag_when_keys_collide`
- `test_translate_contract_raises_when_object_name_duplicated`

Tests assert on the **public API** (returned `Artifact` data or on-disk YAML),
never on private helpers. Group related tests with a `# --- section ---` banner.
Prefix private test helpers with an underscore (`_artifact`, `_make_project`).

## Coding principles

- **No forward references.** Define functions/methods/classes before they are
  used in a module. A helper called by `foo` should appear above `foo`, so the
  file reads top-to-bottom without jumping ahead.
- **Keep code flat.** Avoid deeply nested logic and high cyclomatic/cognitive
  complexity. Prefer early returns, guard clauses, and small helpers over nested
  `if`/`for` pyramids.
- **Fix at the right altitude.** Prefer generalizing the underlying mechanism
  over layering special cases onto shared infrastructure. A special case bolted
  onto a general path is a sign the fix isn't deep enough.
- **Reuse before adding.** Check `mapper.py` / adjacent modules for an existing
  helper before writing a new one (e.g. `slug`, `sanitize_name`,
  `quote_identifier`, `odcs_tags_to_uc`, `odcs_type_to_spark`).
- **Keep mappers pure.** Functions in `mapper.py` take plain dicts and return
  values with no I/O or global state; keep it that way so they stay trivially
  testable.
- **Errors carry a code.** Raise `Odcs2LhpError(code, message, suggestions=[...])`
  with a stable `ODCS-XXX-NNN` code and actionable suggestions, rather than bare
  exceptions. Keep the package dependency-light.
- **Preserve YAML key order.** The writer emits with `sort_keys=False` to keep
  authored order (an LHP convention). Build output dicts in the order they should
  appear; don't rely on alphabetical sorting.
- **Match surrounding style.** Type-hint public functions, write module and
  function docstrings in the existing voice, and keep comment density consistent
  with neighbouring code.

## Documentation to keep in sync

When you change generated output (paths, file shape, or behaviour), update:

- `README.md` — the sidecar table and the "Details" bullets.
- `src/odcs2lhp/translator.py` — the module docstring describing the sidecar set.
- The integration goldens under `tests/integration/expected/` (via `ODCS2LHP_REGEN=1`).

## Scope reminder

`odcs2lhp` reads only ODCS contract files and `lhp.yaml`. Do not add code that
reads pipeline YAMLs, network resources, or other project files unless the task
explicitly calls for expanding that scope.
