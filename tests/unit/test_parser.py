"""Behaviour tests for the ODCS parser (public API only)."""

from __future__ import annotations

import pytest

from odcs2lhp.errors import Odcs2LhpError
from odcs2lhp.parsers import OdcsParser


def test_parser_returns_contract_dict_when_contract_is_valid(sales_contract_path):
    contract = OdcsParser().parse(sales_contract_path)

    assert contract["name"] == "sales-contract"
    assert contract["schema"][0]["name"] == "customer"


def test_parser_raises_when_contract_fails_odcs_validation(broken_contract_path):
    with pytest.raises(Odcs2LhpError) as exc_info:
        OdcsParser().parse(broken_contract_path)

    assert exc_info.value.code == "ODCS-CFG-062"


def test_parser_raises_when_file_missing(tmp_path):
    with pytest.raises(Odcs2LhpError) as exc_info:
        OdcsParser().parse(tmp_path / "nope.yaml")

    assert exc_info.value.code == "ODCS-IO-001"


def test_parser_raises_when_file_is_empty(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(Odcs2LhpError) as exc_info:
        OdcsParser().parse(path)

    assert exc_info.value.code == "ODCS-IO-003"


def test_parser_raises_when_file_is_only_whitespace_and_comments(tmp_path):
    path = tmp_path / "blank.yaml"
    path.write_text("# just a comment\n\n", encoding="utf-8")

    with pytest.raises(Odcs2LhpError) as exc_info:
        OdcsParser().parse(path)

    assert exc_info.value.code == "ODCS-IO-003"


def test_parser_raises_when_file_has_multiple_documents(tmp_path):
    path = tmp_path / "multi_doc.yaml"
    path.write_text("a: 1\n---\nb: 2\n")

    with pytest.raises(Odcs2LhpError) as exc_info:
        OdcsParser().parse(path)

    assert exc_info.value.code == "ODCS-IO-003"


def test_parser_raises_when_document_is_not_a_mapping(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n")

    with pytest.raises(Odcs2LhpError) as exc_info:
        OdcsParser().parse(path)

    assert exc_info.value.code == "ODCS-IO-004"
