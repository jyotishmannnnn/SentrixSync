"""Tests for session manifest (de)serialization round-trips and validation."""
from __future__ import annotations

import json

import pytest

from sentrixsync.core import GroundTruthBlock
from sentrixsync.core.types import ValidationError
from sentrixsync.manifest import load_session, save_session


@pytest.mark.parametrize("ext", [".json", ".yaml", ".yml"])
def test_save_load_roundtrip(tmp_path, two_device_session, ext):
    path = tmp_path / f"session{ext}"
    save_session(two_device_session, path)
    restored = load_session(path)
    assert restored.to_dict() == two_device_session.to_dict()


def test_single_device_roundtrip(tmp_path, single_device_session):
    single_device_session.ground_truth = GroundTruthBlock(
        clock_models={"glove_L": {"alpha": 1.0, "beta_us": 0}})
    path = tmp_path / "n1.json"
    save_session(single_device_session, path)
    restored = load_session(path)
    assert restored.metadata.session_id == "01J9SYNTH0001"
    assert restored.ground_truth.clock_models["glove_L"]["beta_us"] == 0


def test_save_rejects_invalid_session(tmp_path, two_device_session):
    two_device_session.metadata.producers = []      # invalid
    with pytest.raises(ValidationError):
        save_session(two_device_session, tmp_path / "bad.json")


def test_unsupported_extension_rejected(tmp_path, single_device_session):
    with pytest.raises(ValidationError, match="extension"):
        save_session(single_device_session, tmp_path / "x.txt")


def test_load_rejects_unsupported_contract_version(tmp_path, single_device_session):
    save_session(single_device_session, tmp_path / "s.json")
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    data["metadata"]["contract_version"] = "2.0.0"
    (tmp_path / "s.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="contract_version"):
        load_session(tmp_path / "s.json")


def test_load_missing_file():
    with pytest.raises(FileNotFoundError):
        load_session("does/not/exist.json")
