"""Command-line entry point for odcs2lhp.

Discovers ODCS contracts, translates each schema object into LHP sidecar files,
and writes them under ``<project-root>/.lhp/odcs/``. Run it before
``lhp validate`` / ``lhp generate``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .discovery import (
    contract_stem,
    discover_contracts,
    exclusion_columns,
    find_project_root,
)
from .errors import Odcs2LhpError
from .parser import OdcsParser
from .translator import translate_contract
from .writer import DEFAULT_OUTPUT_SUBDIR, write_artifacts


@click.command(name="odcs2lhp")
@click.version_option(__version__, prog_name="odcs2lhp")
@click.option(
    "--contracts-dir",
    "contracts_dir",
    default="contracts",
    show_default=True,
    help="Directory (relative to the project root, or absolute) to scan for "
    "ODCS contract files.",
)
@click.option(
    "--project-root",
    "project_root_opt",
    default=None,
    type=click.Path(path_type=Path),
    help="Project root. Defaults to the nearest ancestor of the current "
    "directory containing lhp.yaml, else the current directory.",
)
@click.option(
    "--output-dir",
    "output_dir_opt",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write sidecar files. Defaults to <project-root>/.lhp/odcs.",
)
@click.option("-v", "--verbose", is_flag=True, help="Print each file written.")
def cli(
    contracts_dir: str,
    project_root_opt: Optional[Path],
    output_dir_opt: Optional[Path],
    verbose: bool,
) -> None:
    """Translate ODCS data contracts into LHP YAML sidecar files."""
    cwd = Path.cwd()
    project_root = (
        project_root_opt.resolve()
        if project_root_opt is not None
        else (find_project_root(cwd) or cwd)
    )

    contracts_path = Path(contracts_dir)
    if not contracts_path.is_absolute():
        contracts_path = project_root / contracts_path

    output_dir = (
        output_dir_opt.resolve()
        if output_dir_opt is not None
        else project_root / DEFAULT_OUTPUT_SUBDIR
    )

    contracts = discover_contracts(contracts_path)
    if not contracts:
        click.echo(f"No ODCS contracts found under {contracts_path}.")
        return

    exclude = exclusion_columns(project_root)
    parser = OdcsParser()

    total_files = 0
    for contract_file in contracts:
        contract = parser.parse(contract_file)
        artifacts = translate_contract(
            contract,
            stem=contract_stem(contract_file),
            exclude=exclude,
        )
        written = write_artifacts(artifacts, output_dir)
        total_files += len(written)
        if verbose:
            for path in written:
                click.echo(f"  wrote {path}")
        click.echo(f"{contract_file.name}: {len(written)} file(s)")

    click.echo(
        f"Translated {len(contracts)} contract(s) -> {total_files} file(s) "
        f"under {output_dir}."
    )


def main() -> None:
    """Console-script entry point. Converts translation errors into exit code 1."""
    try:
        cli.main(standalone_mode=False)
    except Odcs2LhpError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.exceptions.Abort:
        sys.exit(1)


if __name__ == "__main__":
    main()
