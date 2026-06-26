"""Tests for the pure helper functions in backend/common.py."""

from datetime import timezone

import common


def test_parse_size_to_bytes_binary_units():
    assert common.parse_size_to_bytes("1KiB") == 1024
    assert common.parse_size_to_bytes("8.00GiB") == 8 * 1024 ** 3
    assert common.parse_size_to_bytes("2MiB") == 2 * 1024 ** 2


def test_parse_size_to_bytes_decimal_units():
    assert common.parse_size_to_bytes("1KB") == 1000
    assert common.parse_size_to_bytes("1GB") == 1000 ** 3


def test_parse_size_to_bytes_invalid():
    assert common.parse_size_to_bytes("not a size") is None
    assert common.parse_size_to_bytes("") is None
    assert common.parse_size_to_bytes("123") is None


def test_format_bytes_gib():
    assert common.format_bytes_gib(8 * 1024 ** 3) == "8.00GiB"
    assert common.format_bytes_gib(0) == "0.00GiB"
    assert common.format_bytes_gib("nonsense") == "Unknown"


def test_size_round_trip():
    raw = "16.00GiB"
    as_bytes = common.parse_size_to_bytes(raw)
    assert common.format_bytes_gib(as_bytes) == raw


def test_safe_int():
    assert common._safe_int("42") == 42
    assert common._safe_int(7) == 7
    assert common._safe_int("oops") == 0
    assert common._safe_int(None, fallback=-1) == -1


def test_parse_iso_handles_z_suffix_and_offset():
    dt = common._parse_iso("2026-06-26T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timezone.utc.utcoffset(None)


def test_parse_iso_invalid_returns_none():
    assert common._parse_iso("") is None
    assert common._parse_iso(None) is None
    assert common._parse_iso("not-a-date") is None


def test_utc_now_is_timezone_aware():
    now = common._utc_now()
    assert now.tzinfo is not None
