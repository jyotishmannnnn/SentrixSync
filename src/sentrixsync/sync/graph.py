"""Graph-based clock reconciliation.

Replaces the star topology. Devices are nodes; a pair of devices that co-observe
>= 2 shared events forms an edge carrying a TLS-fitted relative clock model.
A reliability-weighted spanning tree rooted at the reference device is found
(lowest cumulative residual), and edge transforms are composed along each path to
yield every reachable device's clock model relative to the reference.

This supports arbitrary co-observation topologies: devices that share no event
with the reference can still be reconciled transitively through intermediates.
Devices with no path to the reference are reported unreachable (graceful
degradation), not errors. The module reasons only about clocks, timestamps,
events, and confidence — no modality assumptions.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from ..clock.estimate import clock_confidence, identity_model, ransac_affine, tls_affine
from ..core.events import SyncEvent
from ..core.timeline import ClockModel
from ..core.types import require


@dataclass
class Edge:
    """Relation between two device clocks, stored as b -> a: t_a = alpha*t_b + beta."""
    a: str
    b: str
    alpha: float
    beta_us: float
    residual_us: float
    n_events: int
    confidence: float


@dataclass
class ReconcileDiagnostics:
    edges: list[Edge] = field(default_factory=list)
    reachable: set[str] = field(default_factory=set)
    unreachable: set[str] = field(default_factory=set)
    hops: dict[str, int] = field(default_factory=dict)
    paths: dict[str, list[str]] = field(default_factory=dict)


def build_edges(events: list[SyncEvent], device_ids: set[str], *, min_events: int = 2,
                method: str = "tls", ransac_threshold_us: float = 1000.0,
                ransac_seed: int = 0) -> list[Edge]:
    """One edge per device pair that co-observes >= `min_events` shared events,
    with a fitted b -> a transform. `method` selects the estimator: 'tls'
    (default, robust to symmetric noise) or 'ransac' (robust to gross outliers /
    mis-associated events). Edge confidence and n reflect the consensus set."""
    pair_obs: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for e in events:
        devs = sorted(d for d in e.observations if d in device_ids)
        for a, b in combinations(devs, 2):
            pair_obs.setdefault((a, b), []).append(
                (int(e.observations[a]), int(e.observations[b])))
    edges: list[Edge] = []
    for (a, b), pairs in pair_obs.items():
        if len(pairs) < min_events:
            continue
        ta = np.array([p[0] for p in pairs], dtype=float)
        tb = np.array([p[1] for p in pairs], dtype=float)
        if method == "ransac":
            alpha, beta, rms, mask = ransac_affine(
                tb, ta, threshold_us=ransac_threshold_us, seed=ransac_seed)
            n_used = int(mask.sum())
        else:
            alpha, beta, rms = tls_affine(tb, ta)    # fit t_a = alpha*t_b + beta
            n_used = len(pairs)
        edges.append(Edge(a=a, b=b, alpha=alpha, beta_us=beta, residual_us=rms,
                          n_events=n_used, confidence=clock_confidence(rms, n_used)))
    return edges


def _compose(outer: tuple[float, float], inner: tuple[float, float]) -> tuple[float, float]:
    """Compose affine maps: outer(inner(t)). inner: child->mid, outer: mid->ref."""
    a_o, b_o = outer
    a_i, b_i = inner
    return a_o * a_i, a_o * b_i + b_o


def reconcile(events: list[SyncEvent], device_ids: list[str],
              reference_device_id: str, ref_clock_id: str, *, min_events: int = 2,
              method: str = "tls", ransac_threshold_us: float = 1000.0,
              ransac_seed: int = 0
              ) -> tuple[dict[str, ClockModel], ReconcileDiagnostics]:
    require(reference_device_id in device_ids,
            f"reference {reference_device_id!r} not in device_ids")
    ids = set(device_ids)
    edges = build_edges(events, ids, min_events=min_events, method=method,
                        ransac_threshold_us=ransac_threshold_us, ransac_seed=ransac_seed)

    # adjacency: node -> list of (neighbor, transform neighbor->node, residual, conf)
    adj: dict[str, list[tuple[str, tuple[float, float], float, float]]] = {d: [] for d in ids}
    for e in edges:
        adj[e.a].append((e.b, (e.alpha, e.beta_us), e.residual_us, e.confidence))
        inv_alpha = 1.0 / e.alpha
        adj[e.b].append((e.a, (inv_alpha, -e.beta_us * inv_alpha), e.residual_us, e.confidence))

    # Dijkstra from the reference, minimizing cumulative residual (most reliable path).
    transform: dict[str, tuple[float, float]] = {reference_device_id: (1.0, 0.0)}
    conf: dict[str, float] = {reference_device_id: 1.0}
    hops: dict[str, int] = {reference_device_id: 0}
    parent: dict[str, str] = {}
    dist: dict[str, float] = {reference_device_id: 0.0}
    pq: list[tuple[float, str]] = [(0.0, reference_device_id)]
    visited: set[str] = set()

    while pq:
        d_u, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        for v, t_v_to_u, res, c in adj[u]:
            if v in visited:
                continue
            # Reliability-weighted cost: prefer high-confidence edges (which fold
            # in both residual AND event count), so a few-observation perfect-fit
            # spurious edge is NOT trusted over a many-observation real edge.
            new_cost = d_u - math.log(max(c, 1e-9))
            if new_cost < dist.get(v, float("inf")):
                dist[v] = new_cost
                transform[v] = _compose(transform[u], t_v_to_u)   # v->ref
                conf[v] = conf[u] * c
                hops[v] = hops[u] + 1
                parent[v] = u
                heapq.heappush(pq, (new_cost, v))

    models: dict[str, ClockModel] = {}
    reachable, unreachable = set(), set()
    for d in ids:
        if d == reference_device_id:
            models[d] = identity_model(d, ref_clock_id)
            reachable.add(d)
        elif d in transform:
            a, b = transform[d]
            models[d] = ClockModel(device_id=d, ref_clock_id=ref_clock_id, alpha=a, beta_us=b,
                                   fit_residual_us=float(dist[d]), clock_confidence=float(conf[d]))
            reachable.add(d)
        else:
            m = identity_model(d, ref_clock_id)
            m.clock_confidence = 0.0
            models[d] = m
            unreachable.add(d)

    paths: dict[str, list[str]] = {}
    for d in reachable:
        chain, cur = [d], d
        while cur in parent:
            cur = parent[cur]
            chain.append(cur)
        paths[d] = list(reversed(chain))

    diag = ReconcileDiagnostics(edges=edges, reachable=reachable, unreachable=unreachable,
                                hops=hops, paths=paths)
    return models, diag
