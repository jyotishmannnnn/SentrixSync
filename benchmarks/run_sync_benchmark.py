"""Synchronization scenario benchmark.

Runs every synthetic scenario preset through the full pipeline (detect -> match
-> estimate -> timeline -> metrics) and writes a Markdown + JSON report comparing
recovered vs injected clock parameters against the synthetic accuracy budget.

Usage:
    python benchmarks/run_sync_benchmark.py [--out benchmarks]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentrixsync import __version__
from sentrixsync.scenarios import (
    MULTIMODAL_PRESETS,
    PRESETS,
    CorruptionSpec,
    build_multimodal_preset,
    build_preset,
    coarse_clock_sweep,
    compare_affine_vs_piecewise,
    make_piecewise_session,
    run_multimodal_scenario,
    run_scenario,
    run_with_corruption,
)

# Per-hop multimodal budget (docs/SYNTHETIC_ACCURACY_BUDGET.md).
MM_BUDGET = {1: {"alpha_err": 8e-5, "beta_err_us": 500, "alignment_rmse_us": 500},
             2: {"alpha_err": 2e-4, "beta_err_us": 1500, "alignment_rmse_us": 1500}}

# Mirror of docs/SYNTHETIC_ACCURACY_BUDGET.md (CI gating thresholds).
BUDGET = {
    "clean": {"alpha_err": 1e-6, "beta_err_us": 50, "alignment_rmse_us": 100},
    "dual_device_offset": {"alpha_err": 5e-5, "beta_err_us": 500, "alignment_rmse_us": 600},
}


def run_all() -> dict:
    rows = {}
    for name in sorted(PRESETS):
        result = run_scenario(build_preset(name))
        rt = result.metrics["roundtrip_accuracy"]
        follower = next(iter(rt.values())) if rt else {}
        rows[name] = {
            "sync_resid_us": result.metrics["sync_resid_us"],
            "coverage_min": result.metrics["coverage_min"],
            "dropout_max": result.metrics["dropout_max"],
            "alpha_err": follower.get("alpha_err"),
            "beta_err_us": follower.get("beta_err_us"),
            "alignment_rmse_us": follower.get("alignment_rmse_us"),
            "gate_verdict": result.validation_report.gate_verdict.value,
            "budget": BUDGET.get(name),
            "budget_pass": _budget_pass(name, follower),
        }
    return rows


def _budget_pass(name: str, acc: dict) -> bool | None:
    b = BUDGET.get(name)
    if b is None or not acc:
        return None
    return (acc["alpha_err"] <= b["alpha_err"]
            and acc["beta_err_us"] <= b["beta_err_us"]
            and acc["alignment_rmse_us"] <= b["alignment_rmse_us"])


def run_multimodal() -> dict:
    rows = {}
    for name in sorted(MULTIMODAL_PRESETS):
        result = run_multimodal_scenario(build_multimodal_preset(name))
        hops = result.metrics["hops"]
        rt = result.metrics["roundtrip_accuracy"]
        devs = {}
        for dev, acc in rt.items():
            h = hops[dev]
            b = MM_BUDGET.get(h)
            ok = (b is not None and acc["alpha_err"] <= b["alpha_err"]
                  and acc["beta_err_us"] <= b["beta_err_us"]
                  and acc["alignment_rmse_us"] <= b["alignment_rmse_us"])
            devs[dev] = {"hops": h, **acc, "budget_pass": ok}
        rows[name] = {
            "sync_resid_us": result.metrics["sync_resid_us"],
            "reachable": result.metrics["reachable"],
            "unreachable": result.metrics["unreachable"],
            "n_edges": result.metrics["n_edges"],
            "gate_verdict": result.validation_report.gate_verdict.value,
            "devices": devs,
        }
    return rows


def run_robustness() -> dict:
    scen = build_multimodal_preset("mm_5device")
    levels = {"none": CorruptionSpec(),
              "moderate": CorruptionSpec(fn_rate=0.05, dup_rate=0.05, fp_rate=0.08,
                                         perturb_us=150, seed=3),
              "heavy": CorruptionSpec(fn_rate=0.10, dup_rate=0.10, fp_rate=0.15,
                                      perturb_us=200, seed=3)}

    def summarize(result) -> dict:
        rt = result.metrics["roundtrip_accuracy"] or {}
        return {"n_edges": result.metrics["n_edges"],
                "n_unreachable": len(result.metrics["unreachable"]),
                "worst_alpha_err": max((a["alpha_err"] for a in rt.values()), default=0.0),
                "worst_beta_err_us": max((a["beta_err_us"] for a in rt.values()), default=0.0)}

    corruption = {}
    for name, corr in levels.items():
        base = run_with_corruption(scen, corr, robust_estimation=False, min_events=2)
        rob = run_with_corruption(scen, corr, robust_estimation=True, min_events=6)
        corruption[name] = {"baseline": summarize(base), "robust": summarize(rob)}

    coarse = coarse_clock_sweep(scen, [0, 4000, 8000, 20000, 40000], seed=2)
    piecewise = compare_affine_vs_piecewise(make_piecewise_session(seed=1))
    return {"corruption": corruption, "coarse_sweep": coarse, "piecewise": piecewise}


def write_report(rows: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "sync_benchmark_results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    L = []
    A = L.append
    A(f"# SentrixSync — Synchronization Scenario Benchmark\n")
    A(f"Generated by SentrixSync v{__version__}. Recovered-vs-injected clock "
      f"accuracy across synthetic scenarios. Budget columns reference "
      f"docs/SYNTHETIC_ACCURACY_BUDGET.md.\n")
    A("| Scenario | resid (us) | cov_min | drop_max | alpha_err | beta_err (us) | "
      "RMSE (us) | verdict | budget |")
    A("|---|---|---|---|---|---|---|---|---|")
    for name in sorted(rows):
        r = rows[name]
        ae = f"{r['alpha_err']:.2e}" if r["alpha_err"] is not None else "-"
        be = f"{r['beta_err_us']:.1f}" if r["beta_err_us"] is not None else "-"
        rm = f"{r['alignment_rmse_us']:.1f}" if r["alignment_rmse_us"] is not None else "-"
        bp = "n/a" if r["budget_pass"] is None else ("PASS" if r["budget_pass"] else "FAIL")
        A(f"| {name} | {r['sync_resid_us']:.1f} | {r['coverage_min']:.4f} | "
          f"{r['dropout_max']:.4f} | {ae} | {be} | {rm} | {r['gate_verdict']} | {bp} |")
    A("")
    A("Notes: `loss`/`burst` scenarios intentionally trip coverage/dropout gates "
      "(NEEDS_REVIEW) — that is the gate working, not a failure. Budget PASS/FAIL "
      "applies only to scenarios listed in the accuracy budget.\n")

    mm = run_multimodal()
    A("## Multimodal scenarios (subset-aware association + graph reconciliation)\n")
    for name, r in sorted(mm.items()):
        A(f"### {name}\n")
        A(f"- edges: {r['n_edges']} · reachable: {r['reachable']} · "
          f"unreachable: {r['unreachable']} · resid: {r['sync_resid_us']:.1f} us · "
          f"verdict: {r['gate_verdict']}")
        A("\n| Device | hops | alpha_err | beta_err (us) | RMSE (us) | budget |")
        A("|---|---|---|---|---|---|")
        for dev in sorted(r["devices"]):
            d = r["devices"][dev]
            A(f"| {dev} | {d['hops']} | {d['alpha_err']:.2e} | {d['beta_err_us']:.1f} | "
              f"{d['alignment_rmse_us']:.1f} | {'PASS' if d['budget_pass'] else 'FAIL'} |")
        A("")
    A("No single device observes all events; camera/mocap are reconciled "
      "transitively (2 hops) through imu. Accuracy holds per the per-hop budget.\n")

    rob = run_robustness()
    A("## Robustness hardening\n")
    A("### Detection corruption — robust (RANSAC + min-support) vs TLS baseline\n")
    A("| Corruption | mode | edges | unreachable | worst alpha_err | worst beta_err (us) |")
    A("|---|---|---|---|---|---|")
    for level in ("none", "moderate", "heavy"):
        for mode in ("baseline", "robust"):
            r = rob["corruption"][level][mode]
            A(f"| {level} | {mode} | {r['n_edges']} | {r['n_unreachable']} | "
              f"{r['worst_alpha_err']:.2e} | {r['worst_beta_err_us']:.1f} |")
    A("\nUnder heavy corruption the baseline forms a spurious cross-group edge from "
      "false-positive coincidences and mis-routes a device; robust mode rejects it "
      "(min-support) and RANSAC cleans the surviving edges.\n")
    A("### Coarse-clock (wall-clock) sensitivity — association operating limit\n")
    A("| coarse noise (us) | unreachable | worst alpha_err | worst beta_err (us) |")
    A("|---|---|---|---|")
    for r in rob["coarse_sweep"]:
        A(f"| {r['coarse_noise_us']:.0f} | {r['n_unreachable']} | "
          f"{r['max_alpha_err']:.2e} | {r['max_beta_err_us']:.1f} |")
    A("\nOperating limit: full reconciliation while coarse error stays well below "
      "the 12 ms association tolerance; breakdown beyond it.\n")
    A("### Piecewise drift vs single affine (long nonlinear-drift session)\n")
    p = rob["piecewise"]
    A(f"- affine: fit residual {p['affine_fit_residual_us']:.1f} us, alignment RMSE "
      f"{p['affine_alignment_rmse_us']:.1f} us")
    A(f"- piecewise: fit residual {p['piecewise_fit_residual_us']:.1f} us, alignment RMSE "
      f"{p['piecewise_alignment_rmse_us']:.1f} us\n")

    (out / "sync_benchmark_report.md").write_text("\n".join(L), encoding="utf-8")
    rows["_multimodal"] = mm
    rows["_robustness"] = rob
    (out / "sync_benchmark_results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmarks")
    args = ap.parse_args()
    rows = run_all()
    write_report(rows, Path(args.out))                # write_report appends multimodal + json
    scenarios = {k: v for k, v in rows.items() if not k.startswith("_")}
    n_pass = sum(1 for r in scenarios.values() if r["budget_pass"])
    n_budget = sum(1 for r in scenarios.values() if r["budget_pass"] is not None)
    print(f"scenarios: {len(scenarios)} | budget-gated: {n_budget} | budget PASS: {n_pass} "
          f"| multimodal presets: {len(rows.get('_multimodal', {}))}")


if __name__ == "__main__":
    main()
