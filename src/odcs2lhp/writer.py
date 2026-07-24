"""Write translated artifacts to YAML sidecar files under ``.lhp/odcs/``."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, List

import yaml

from .translator import Artifact

# The gitignored output root, relative to the project root.
DEFAULT_OUTPUT_SUBDIR = Path(".lhp") / "odcs"


def reset_output_dir(output_dir: Path) -> None:
    """Remove the output dir and everything under it, then recreate it empty.

    Ensures each translation run starts from a clean tree, so sidecars whose
    source object/contract was renamed or removed do not linger.
    """
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def write_artifacts(artifacts: Iterable[Artifact], output_dir: Path) -> List[Path]:
    """Write each artifact under ``output_dir``, creating parent dirs as needed.

    An artifact with ``text`` set (e.g. a generated ``.py`` transform module) is
    written verbatim; otherwise its ``data`` is emitted as block-style YAML with
    ``sort_keys=False`` (LHP convention) so authored key order is preserved.
    Existing files are overwritten (idempotent re-runs).

    :returns: the list of written file paths, in the order given.
    """
    output_dir = Path(output_dir)
    written: List[Path] = []
    for artifact in artifacts:
        target = output_dir / artifact.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if artifact.text is not None:
            content = artifact.text
        else:
            content = yaml.safe_dump(
                artifact.data, default_flow_style=False, sort_keys=False
            )
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
