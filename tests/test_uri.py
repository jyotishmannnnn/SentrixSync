"""Tests for the payload-URI grammar (CONTRACT.md §6)."""
from __future__ import annotations

import pytest

from sentrixsync.core import uri
from sentrixsync.core.types import ValidationError


@pytest.mark.parametrize("scheme", ["file", "mcap", "parquet", "memory"])
def test_supported_schemes_parse(scheme):
    u = uri.parse_payload_uri(f"{scheme}://some/location")
    assert u.scheme == scheme
    assert u.location == "some/location"
    assert u.fragment is None


def test_fragment_parsed():
    u = uri.parse_payload_uri("parquet:///abs/ep.parquet#stream=tactile_field&row=12")
    assert u.scheme == "parquet"
    assert u.location == "/abs/ep.parquet"
    assert u.fragment == "stream=tactile_field&row=12"
    assert str(u) == "parquet:///abs/ep.parquet#stream=tactile_field&row=12"


def test_unknown_scheme_rejected():
    with pytest.raises(ValidationError, match="unsupported payload-URI scheme"):
        uri.parse_payload_uri("uri://x")


def test_malformed_rejected():
    with pytest.raises(ValidationError):
        uri.parse_payload_uri("not-a-uri")
    with pytest.raises(ValidationError):
        uri.parse_payload_uri("file://")          # empty location


def test_is_payload_uri():
    assert uri.is_payload_uri("memory://glove_L")
    assert not uri.is_payload_uri("opaque-handle")
    assert not uri.is_payload_uri("uri://x")       # unregistered scheme


def test_build_roundtrip():
    built = uri.build_payload_uri("memory", "glove_L", fragment="stream=tactile_field&row=0")
    assert built == "memory://glove_L#stream=tactile_field&row=0"
    assert uri.parse_payload_uri(built).location == "glove_L"


def test_register_scheme_extensibility():
    assert "rerun" not in uri.allowed_schemes()
    uri.register_scheme("rerun")
    try:
        assert uri.is_payload_uri("rerun://recording/42")
        assert uri.parse_payload_uri("rerun://recording/42").scheme == "rerun"
    finally:
        uri.allowed_schemes()  # registry is process-global; harmless for tests
