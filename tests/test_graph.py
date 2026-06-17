"""Tests for graph-based clock reconciliation."""
from __future__ import annotations

import numpy as np

from sentrixsync.clock.forward import ForwardClock
from sentrixsync.core.events import SyncEvent
from sentrixsync.core.types import EvidenceTier
from sentrixsync.sync import build_edges, reconcile


def _obs_event(eid, mapping):
    return SyncEvent(eid, EvidenceTier.SHARED_EVENT, {d: int(round(t)) for d, t in mapping.items()})


def test_star_reconcile_recovers_followers():
    # A is reference (identity); B and C each share all events with A.
    A = ForwardClock()
    B = ForwardClock.from_offset_skew(10000, 12.0)
    C = ForwardClock.from_offset_skew(-7000, -5.0)
    refs = np.linspace(1e6, 7e6, 20)
    events = [_obs_event(f"e{i}", {"A": tr, "B": B.local_from_ref(tr), "C": C.local_from_ref(tr)})
              for i, tr in enumerate(refs)]
    models, diag = reconcile(events, ["A", "B", "C"], "A", "A_clk")
    assert diag.unreachable == set()
    assert abs(models["B"].alpha - B.alpha) < 1e-6
    assert abs(models["B"].beta_us - B.beta_us) < 50
    assert abs(models["C"].beta_us - C.beta_us) < 50
    assert diag.hops["B"] == 1 and diag.hops["C"] == 1


def test_transitive_reconcile_through_intermediate():
    # A--B share one event set; B--C share another. A and C share NOTHING.
    A = ForwardClock()
    B = ForwardClock.from_offset_skew(10000, 10.0)
    C = ForwardClock.from_offset_skew(-5000, 20.0)
    ab = np.linspace(1.0e6, 4.0e6, 5)
    bc = np.linspace(1.5e6, 4.5e6, 5)
    events = [_obs_event(f"ab{i}", {"A": tr, "B": B.local_from_ref(tr)}) for i, tr in enumerate(ab)]
    events += [_obs_event(f"bc{i}", {"B": B.local_from_ref(tr), "C": C.local_from_ref(tr)})
               for i, tr in enumerate(bc)]
    models, diag = reconcile(events, ["A", "B", "C"], "A", "A_clk")
    # C reached only via B
    assert diag.hops["C"] == 2
    assert diag.paths["C"] == ["A", "B", "C"]
    assert abs(models["C"].alpha - C.alpha) < 5e-6
    assert abs(models["C"].beta_us - C.beta_us) < 200
    assert "C" in diag.reachable


def test_disconnected_device_is_unreachable_not_fatal():
    A = ForwardClock()
    B = ForwardClock.from_offset_skew(10000, 0.0)
    refs = np.linspace(1e6, 4e6, 5)
    events = [_obs_event(f"e{i}", {"A": tr, "B": B.local_from_ref(tr)}) for i, tr in enumerate(refs)]
    # D shares no events with anyone
    models, diag = reconcile(events, ["A", "B", "D"], "A", "A_clk")
    assert "D" in diag.unreachable
    assert models["D"].alpha == 1.0 and models["D"].clock_confidence == 0.0
    assert {"A", "B"} <= diag.reachable


def test_reference_is_identity():
    models, _ = reconcile([], ["A"], "A", "A_clk")
    assert models["A"].alpha == 1.0 and models["A"].beta_us == 0.0
    assert models["A"].clock_confidence == 1.0


def test_build_edges_requires_min_events():
    A, B = ForwardClock(), ForwardClock.from_offset_skew(5000, 0.0)
    one = [_obs_event("e0", {"A": 1000.0, "B": B.local_from_ref(1000.0)})]
    assert build_edges(one, {"A", "B"}, min_events=2) == []     # only 1 shared event
    two = one + [_obs_event("e1", {"A": 2000.0, "B": B.local_from_ref(2000.0)})]
    assert len(build_edges(two, {"A", "B"}, min_events=2)) == 1
