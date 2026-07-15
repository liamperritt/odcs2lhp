"""odcs2lhp: translate ODCS data contracts into Lakehouse Plumber YAML sidecars.

A standalone, dependency-light utility (no ``lhp`` import) that discovers ODCS
contracts in a project, translates each schema object into the LHP artifact
formats, and writes YAML sidecar files under ``.lhp/odcs/`` for LHP to consume
directly. Run it before ``lhp validate`` / ``lhp generate``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
