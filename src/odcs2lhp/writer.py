"""Write translated artifacts to YAML sidecar files under ``.lhp/odcs/``."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import yaml

from .translator import Artifact

# The gitignored output root, relative to the project root.
DEFAULT_OUTPUT_SUBDIR = Path(".lhp") / "odcs"


def write_artifacts(artifacts: Iterable[Artifact], output_dir: Path) -> List[Path]:
    """Write each artifact under ``output_dir``, creating parent dirs as needed.

    Uses block-style YAML with ``sort_keys=False`` (LHP convention) so authored
    key order is preserved. Existing files are overwritten (idempotent re-runs).

    :returns: the list of written file paths, in the order given.
    """
    output_dir = Path(output_dir)
    written: List[Path] = []
    for artifact in artifacts:
        target = output_dir / artifact.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(artifact.data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        written.append(target)
    return written
