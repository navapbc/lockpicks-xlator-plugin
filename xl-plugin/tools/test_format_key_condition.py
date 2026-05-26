# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for _format_key_condition in transpile_to_catala (ticket 21).

Catala requires date literals in the `|YYYY-MM-DD|` fence form. Bare dates
(e.g. `2024-01-01`) are an OCaml syntax error. Covers every value type the
helper handles and confirms it raises on unsupported types.
"""

import datetime
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import _format_key_condition


def test_string_emits_with_pattern():
    assert _format_key_condition("household_type", "A1E") == "household_type with pattern A1E"


def test_date_emits_catala_fence_form():
    result = _format_key_condition("effective_date", datetime.date(2024, 1, 1))
    assert result == "effective_date = |2024-01-01|"


def test_datetime_emits_date_only_fence():
    result = _format_key_condition("effective_date", datetime.datetime(2025, 6, 15, 12, 0, 0))
    assert result == "effective_date = |2025-06-15|"


def test_int_emits_equality():
    assert _format_key_condition("household_size", 4) == "household_size = 4"


def test_float_emits_equality():
    assert _format_key_condition("threshold", 1.5) == "threshold = 1.5"


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="unsupported key value type"):
        _format_key_condition("some_field", object())


def test_yaml_loaded_date_row_emits_fence():
    """A date loaded by yaml.safe_load arrives as datetime.date — confirm fence form."""
    civil_row = yaml.safe_load("effective_date: 2024-01-01")
    result = _format_key_condition("effective_date", civil_row["effective_date"])
    assert result == "effective_date = |2024-01-01|"


def test_compound_condition_date_and_enum():
    """Both branches emit correctly in the same multi-key table condition."""
    date_part = _format_key_condition("effective_date", datetime.date(2024, 1, 1))
    enum_part = _format_key_condition("household_type", "A1E")
    condition = f"under condition {enum_part} and {date_part}"
    assert "|2024-01-01|" in condition
    assert "household_type with pattern A1E" in condition
    assert "2024-01-01" not in condition.replace("|2024-01-01|", "")
