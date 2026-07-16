"""Parsers for odcs2lhp: ODCS contracts and Spark DDL types."""

from __future__ import annotations

from .odcs_parser import ODCS_SCHEMA_RESOURCE, OdcsParser

__all__ = ["OdcsParser", "ODCS_SCHEMA_RESOURCE"]
