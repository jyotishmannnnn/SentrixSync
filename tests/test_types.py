"""Tests for foundational primitives: versions, enums, validation helpers."""
from __future__ import annotations

import pytest

from sentrixsync.core import types as T
from sentrixsync.core.types import ValidationError


def test_version_constants_present():
    assert T.CONTRACT_VERSION == "1.0.1"      # PATCH bump: payload_inline + URI grammar
    assert T.SCHEMA_VERSION == "0.3.0"
    assert T.SENTRIXSYNC_VERSION == "0.3.0"
    # PATCH bump must not change MAJOR support
    assert T.contract_version_supported("1.0.0")
    assert T.contract_version_supported("1.0.1")


@pytest.mark.parametrize("v,expected", [
    ("1.0.0", (1, 0, 0)), ("0.3.0", (0, 3, 0)), ("2.5.7-rc1", (2, 5, 7)),
    ("1.2.3+build9", (1, 2, 3)),
])
def test_parse_semver(v, expected):
    assert T.parse_semver(v) == expected


@pytest.mark.parametrize("bad", ["", "1.0", "x.y.z", "1"])
def test_parse_semver_rejects_bad(bad):
    with pytest.raises(ValidationError):
        T.parse_semver(bad)


def test_contract_version_support_major_only():
    assert T.contract_version_supported("1.0.0")
    assert T.contract_version_supported("1.9.4")
    assert not T.contract_version_supported("2.0.0")
    assert not T.contract_version_supported("0.9.0")


def test_microseconds_rejects_float_and_bool():
    with pytest.raises(ValidationError):
        T.require_microseconds(1.5, "t")
    with pytest.raises(ValidationError):
        T.require_microseconds(True, "t")
    with pytest.raises(ValidationError):
        T.require_microseconds(-1, "t")
    T.require_microseconds(0, "t")        # ok
    T.require_microseconds(-1, "t", allow_negative=True)  # ok


def test_unit_interval_helper():
    T.require_unit_interval(0.0, "c")
    T.require_unit_interval(1.0, "c")
    with pytest.raises(ValidationError):
        T.require_unit_interval(1.01, "c")
    with pytest.raises(ValidationError):
        T.require_unit_interval(True, "c")


def test_coerce_enum_roundtrip_and_error():
    assert T.coerce_enum("continuous", T.Kernel, "k") is T.Kernel.CONTINUOUS
    assert T.coerce_enum(T.Kernel.HOLD, T.Kernel, "k") is T.Kernel.HOLD
    with pytest.raises(ValidationError):
        T.coerce_enum("nope", T.Kernel, "k")


def test_enum_values_are_bare_strings():
    assert T.Kernel.CONTINUOUS.value == "continuous"
    assert T.EvidenceTier.SHARED_EVENT.value == "shared_event"
    assert T.GateVerdict.CERTIFIED.value == "certified"


def test_require_key_reports_owner():
    with pytest.raises(ValidationError, match="missing required field 'x'"):
        T.require_key({}, "x", "thing")
