"""Exceptions raised by odcs2lhp.

A single lightweight exception type keeps the package dependency-free (no ``lhp``
import). Each error carries a short ``code`` mirroring the LHP error it maps to,
plus a human-readable message and optional remediation suggestions.
"""

from __future__ import annotations

from typing import List, Optional


class Odcs2LhpError(Exception):
    """A translation error with an error code and optional suggestions."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        suggestions: Optional[List[str]] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.suggestions = suggestions or []
        rendered = f"[{code}] {message}"
        if self.suggestions:
            rendered += "\n  - " + "\n  - ".join(self.suggestions)
        super().__init__(rendered)
