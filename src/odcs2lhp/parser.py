"""Parse and validate ODCS data-contract YAML files.

Vendored from ``lhp.parsers.odcs_parser`` but self-contained: loads YAML with
PyYAML and validates against the ODCS JSON Schema shipped inside this package
(``odcs2lhp.schemas.odcs.schema.json``, sourced verbatim from bitol-io).
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict

import jsonschema
import yaml

from .errors import Odcs2LhpError

ODCS_SCHEMA_RESOURCE = ("odcs2lhp.schemas", "odcs.schema.json")


class OdcsParser:
    """Load an ODCS contract from disk and validate it against the ODCS schema."""

    def __init__(self) -> None:
        package, resource = ODCS_SCHEMA_RESOURCE
        schema_text = (files(package) / resource).read_text(encoding="utf-8")
        self._schema: Dict[str, Any] = json.loads(schema_text)
        self._validator = jsonschema.Draft201909Validator(self._schema)

    def parse(self, path: Path) -> Dict[str, Any]:
        """Load ``path`` as YAML and validate it against the ODCS JSON Schema.

        :returns: the parsed contract as a dict.
        :raises Odcs2LhpError: if the file cannot be read, is not a single YAML
            document/mapping, or fails ODCS schema validation.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise Odcs2LhpError(
                "ODCS-IO-001",
                f"Could not read contract file {path}: {e}",
                suggestions=["Check the path exists and is readable."],
            ) from e

        try:
            documents = list(yaml.safe_load_all(text))
        except yaml.YAMLError as e:
            raise Odcs2LhpError(
                "ODCS-IO-002",
                f"Could not parse YAML in contract {path}: {e}",
                suggestions=["Check the file is valid YAML."],
            ) from e

        documents = [doc for doc in documents if doc is not None]
        if len(documents) != 1:
            raise Odcs2LhpError(
                "ODCS-IO-003",
                f"Contract {path} must contain exactly one YAML document "
                f"(found {len(documents)}).",
                suggestions=["Put a single ODCS contract in each file."],
            )
        contract = documents[0]
        if not isinstance(contract, dict):
            raise Odcs2LhpError(
                "ODCS-IO-004",
                f"Contract {path} must be a YAML mapping, not "
                f"{type(contract).__name__}.",
            )

        try:
            self._validator.validate(contract)
        except jsonschema.ValidationError as e:
            raise Odcs2LhpError(
                "ODCS-CFG-062",
                f"Contract {path} failed ODCS schema validation: {e.message}",
                suggestions=[
                    "Check the contract against the ODCS specification",
                    "Ensure required top-level fields are present "
                    "(version, kind, apiVersion, id, status)",
                ],
            ) from e

        return contract
