"""SYNC-1 — SyncResult disk round-trip fidelity."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync import load_session, load_sync_result, save_sync_result
from sentrixsync.core import (
    ClockDescriptor, DeviceDescriptor, DeviceRegistration, DeviceRole,
    EvidenceTier, Kernel, Origin, Session, SessionMetadata, StreamDescriptor,
)
from sentrixsync.sync.engine import synchronize

N = 16
STEP_US = 625  # 1600 Hz


def _descriptor(device_id: str = "glove_L") -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id=device_id, modality="tactile", producer="sentrixsim",
        is_synthetic=True, reference_candidate=True,
        clock=ClockDescriptor(clock_id=f"{device_id}_hub", resolution_us=1,
                              nominal_epoch="session_start"),
        evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
        streams=[StreamDescriptor(
            stream_id="tactile_field", device_id=device_id, kind="tactile_field",
            kernel=Kernel.CONTINUOUS, payload_kind="bmm350_cluster_uT", units="uT",
            nominal_rate_hz=1600.0, payload_shape=[21, 3], subframe_capable=True)])


def _sync_result():
    ts = np.arange(N, dtype=np.int64) * STEP_US
    desc = _descriptor()
    sr = synchronize(
        reference_device_id="glove_L", descriptors={"glove_L": desc},
        stream_timestamps={("glove_L", "tactile_field"): ts}, sync_events=[],
        grid_rate_hz=1600.0, rejection_tolerance_us=1875)
    return sr, desc


def _session(desc) -> Session:
    return Session(
        metadata=SessionMetadata(session_id="01J9SYNTH0001", origin=Origin.SYNTHETIC,
                                 producers=["sentrixsim"], grid_rate_hz=1600,
                                 rejection_tolerance_us=1875),
        devices=[DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                    descriptor=desc,
                                    stream_refs={"tactile_field": "memory://x"})])


def test_roundtrip_scalars_and_reports(tmp_path):
    sr, _ = _sync_result()
    save_sync_result(sr, tmp_path / "sr")
    lo = load_sync_result(tmp_path / "sr")

    assert lo.reference_device_id == sr.reference_device_id
    assert lo.reference_clock_id == sr.reference_clock_id
    assert lo.sync_report.sync_resid_us == sr.sync_report.sync_resid_us
    assert lo.validation_report.gate_verdict == sr.validation_report.gate_verdict
    assert set(lo.clock_models) == set(sr.clock_models)
    for d, cm in sr.clock_models.items():
        assert lo.clock_models[d].alpha == cm.alpha
        assert lo.clock_models[d].beta_us == cm.beta_us
    # metrics preserved (release_gate reads sync_resid_us)
    assert float(lo.metrics.get("sync_resid_us", -1)) == float(sr.metrics.get("sync_resid_us", -2))


def test_roundtrip_timeline_arrays_exact(tmp_path):
    sr, _ = _sync_result()
    save_sync_result(sr, tmp_path / "sr")
    lo = load_sync_result(tmp_path / "sr")

    assert np.array_equal(lo.timeline.grid_us, sr.timeline.grid_us)
    assert lo.timeline.grid_us.dtype == sr.timeline.grid_us.dtype
    assert set(lo.timeline.per_stream) == set(sr.timeline.per_stream)
    for key, al in sr.timeline.per_stream.items():
        la = lo.timeline.per_stream[key]
        assert la.kernel == al.kernel
        assert np.array_equal(la.source_index, al.source_index)
        assert np.array_equal(la.next_index, al.next_index)
        assert np.array_equal(la.valid, al.valid)
        assert la.valid.dtype == np.bool_
        assert np.allclose(la.weight, al.weight)
        assert np.allclose(la.interp_confidence, al.interp_confidence)


def test_roundtrip_confidence_components(tmp_path):
    sr, _ = _sync_result()
    save_sync_result(sr, tmp_path / "sr")
    lo = load_sync_result(tmp_path / "sr")

    assert set(lo.confidence) == set(sr.confidence)
    for key, cc in sr.confidence.items():
        lc = lo.confidence[key]
        assert np.allclose(lc.source, cc.source)
        assert np.allclose(lc.clock, cc.clock)
        assert np.allclose(lc.interpolation, cc.interpolation)
        # derived scalar (export convenience) reproduces exactly
        assert np.allclose(lc.derived_scalar(), cc.derived_scalar())


def test_bundle_with_session_roundtrips(tmp_path):
    sr, desc = _sync_result()
    sess = _session(desc)
    save_sync_result(sr, tmp_path / "sr", session=sess)
    loaded = load_session(tmp_path / "sr")
    assert loaded is not None
    assert loaded.metadata.session_id == "01J9SYNTH0001"
    # no session bundled -> None
    save_sync_result(sr, tmp_path / "sr2")
    assert load_session(tmp_path / "sr2") is None


def test_load_from_manifest_path_and_bad_dir(tmp_path):
    sr, _ = _sync_result()
    save_sync_result(sr, tmp_path / "sr")
    # loading via the manifest file path also works
    lo = load_sync_result(tmp_path / "sr" / "sync_result.json")
    assert lo.reference_clock_id == sr.reference_clock_id
    with pytest.raises(Exception):
        load_sync_result(tmp_path / "does_not_exist")
