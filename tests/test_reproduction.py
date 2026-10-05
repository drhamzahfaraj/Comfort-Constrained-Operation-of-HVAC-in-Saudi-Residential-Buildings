"""Tests: geometry of the floor plan, engine consistency, the optimal-leap identity (Theorem 1), the thermostat
scheduling result under the volume tariff (Theorem 3), and the paper's headline numbers."""
import json
from pathlib import Path
import numpy as np
import pytest
from hvac_savings import model as M

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results" / "results.json"
needs_results = pytest.mark.skipif(not RES.exists(), reason="run hvac-reproduce first")
CT = ("Riyadh", "Jeddah"); SCS = ("apartment", "floor", "building")


def R_():
    return json.load(open(RES))


def test_plan_closes_and_matches_labels():
    """The zone rectangles (wall centrelines) reproduce the clear room sizes of the drawing: each single-rectangle
    room is its clear size plus half of each bounding wall (0.15-0.30 m in total per direction); the outline closes."""
    B = json.load(open(ROOT / "experiments" / "configs" / "building.json"))
    sp, area, faces, shared = M._plan_geometry()
    for r in B["apartment"]["rooms"] + B["core"]["rooms"]:
        if r.get("clear_m") and len(r["rects"]) == 1 and r["name"] not in ("living", "living_open"):
            x0, y0, x1, y1 = r["rects"][0]
            for d, c in ((x1 - x0, r["clear_m"][0]), (y1 - y0, r["clear_m"][1])):
                assert 0.149 <= d - c <= 0.301, (r["name"], d, c)
    W, D = B["plan"]["main_body_m"]; Wo, Do = B["plan"]["footprint_m"]
    assert abs(W - (Wo - 0.30)) < 1e-9 and abs(D - (Do - 0.30)) < 1e-9          # 0.30 m exterior walls
    assert abs(faces.sum() - (2 * (W + D) + 4 * B["plan"]["balcony_depth_m"])) < 1e-6
    assert np.allclose(shared, shared.T)


def test_batched_equals_single():
    M.set_scope("apartment")
    try:
        pols = [M.CODE, [M.CODE[0], M.CODE[1], 2, 0]]
        B = M.simulate_batch("Riyadh", "compliant", pols)
        for p, b in zip(pols, B):
            s = M.simulate("Riyadh", "compliant", p)
            assert abs(s["bill"] - b["bill"]) / b["bill"] < 1e-12
    finally:
        M.set_scope("building")


def test_base_case_equipment():
    """The base case models the units as installed: the 3-ton units are fixed-speed on/off, every unit has a cycling
    degradation coefficient, and every zone has two nodes; the ISO 13370 slab transmittance is about 0.40 W/m2K."""
    assert M.ONOFF_ZONES[M.COND].sum() == np.isclose(M.QC, 3 * M.TON_KW)[M.COND].sum() > 0
    assert set(np.unique(M.CYCLING_CD[M.COND])) == {0.15, 0.25}
    assert M.TWO_NODE and abs(M.TWO_NODE["k_am_W_m2K"] - 9.9) < 0.1
    assert abs(M.U_GRND_ISO - 0.40) < 0.01 and M.HYST == 0.5 and M.SP_STEP == 0.5
    assert M.MIN_OFF_MIN == 3 and M.ESC_KICK and abs(M.GAMMA_IN - 0.02) < 1e-12 and abs(M.cop_indoor(23.5) - 0.93) < 1e-9


def test_thermostat_holds_the_ceiling():
    """With a 1 K differential centred on 23.5 degC no conditioned room rises above the 24 degC ceiling."""
    M.set_scope("apartment")
    try:
        r = M.simulate("Riyadh", "compliant", M.CODE)
        assert r["Vmax"][M.COND].max() <= 24.0 + 1e-6 and r["cviol_hot"] <= 1e-6
    finally:
        M.set_scope("building")


def test_optimal_leap_identity():
    """Theorem 1: mean power over a leap cycle = L(level) / COP_m; with constant L it does not depend on the amplitude."""
    L, q, cop, C = 0.375, 1.846, 3.41, 1.2
    for dV in (0.2, 0.5, 1.0):
        t_on = C * dV / (q - L); t_off = C * dV / L
        assert abs(q * t_on / cop / (t_on + t_off) - L / cop) < 1e-12


def test_staircase_and_occupancy_helpers():
    prof = M.staircase(2.0, 3, 9)
    assert prof[3] == -2.0 and prof[8] == -0.5 and prof[9] == 0.0 and (prof <= 0).all()
    on, occ = M.occupancy(1)
    assert (on | ~occ).all() and on.sum() > occ.sum()                             # early start covers every occupied hour


def test_scheduling_null_under_volume():
    """Under the volume tariff no thermostat pre-cooling policy beats none (Case 1, Riyadh)."""
    M.set_scope("apartment")
    try:
        cs, hs = M.CODE[0], M.CODE[1]
        grid = [[cs, hs, a, b] for a in (0, 0.5, 2, 3) for b in (0, 0.5, 2, 3)]
        R = M.simulate_batch("Riyadh", "compliant", grid)
        bills = [r["bill"] for r in R]
        assert int(np.argmin(bills)) == 0 and min(bills[1:]) > bills[0]
        assert max(r["sviol_cold"] for r in R) == 0.0                             # (C3): no room below the 20 degC safe floor
    finally:
        M.set_scope("building")


@needs_results
def test_headline_numbers():
    R = R_()
    for c in CT:
        for sc in SCS:
            for e in ("compliant", "noncompliant"):
                x = R[f"scopes_{c}"][sc][e]
                assert x["volume"]["bldg_pct"] == 0.0 and x["volume"]["best_policy"] == [0, 0]
            assert R[f"scopes_{c}"][sc]["compliant"]["hot_excursion_Ch"] == 0 and R[f"scopes_{c}"][sc]["compliant"]["saturated_units"] == 0
            assert R[f"scopes_{c}"][sc]["compliant"]["frontier_setpoint"] == 23.5
    PINNED = json.load(open(ROOT / "tests" / "pinned.json"))
    for path, val in PINNED.items():
        x = R
        for k in path.split("/") if "|" not in path else path.split("|"):
            x = x[k]
        assert x == val, (path, x, val)


@needs_results
def test_passive_zone_budget_closes():
    """Proposition 2 (heat budget of a passive zone): every bath's July budget closes, doors included."""
    for c in CT:
        for name, b in R_()["passive_zone"][c]["baths"].items():
            assert abs(b["closure_W"]) / (b["in_outdoor_W"] + b["in_gains_W"]) < 0.05, (c, name, b["closure_W"])


@needs_results
def test_cost_model_null():
    """Theorem 3: no energy-priced tariff makes thermostat pre-cooling pay."""
    for c in CT:
        for name, t in R_()["cost_model"][c].items():
            assert t["saving_pct"] == 0.0 and t["best_policy"] == [0, 0] and t["prepeak_extra_pct"] > 0, (c, name)


@needs_results
def test_both_seasons_within_band_at_code_policy():
    R = R_()["seasons_hc"]
    for c in CT:
        s = R[f"compliant/{c}"]
        assert s["cold_excursion_Ch"] <= 1 and s["hot_excursion_Ch"] <= 1
    assert R["noncompliant/Riyadh"]["heat_kwh"] > R["compliant/Riyadh"]["heat_kwh"] > 0


@needs_results
def test_instances_normal_and_hard():
    """Pre-cooling never pays on any instance with an admissible policy. Right-sized units break the ceiling under every
    policy (no admissible policy: infeasible); catalogue units hold it, and only the floor can fail (heat-wave year,
    pre-code Riyadh, heating months lost to the seasonal changeover)."""
    for k, v in R_()["instances"].items():
        if v["best_policy"] is not None:
            assert v["best_policy"] == [0.0, 0.0] and v["sched_pct"] == 0.0, k
        else:
            assert v["n_feasible"] == 0 and v["hard"], k
        if k.endswith("right_sized"):
            assert v["hard"] and v["best_policy"] is None and v["hot_Kh"] > 0, k
        else:
            assert v["hot_Kh"] == 0, k                                   # catalogue units always hold the ceiling
            assert v["hard"] == (v["cold_Kh"] > 0), k


@needs_results
def test_train_then_evaluate_on_full_year():
    for c, v in R_()["train_eval"].items():
        assert v["full_year_best_policy"] == [0.0, 0.0] and v["full_year_sched_pct"] == 0.0 and v["spearman_rank"] > 0.98
        assert abs(v["rel_err_pct"]) < 5.0                                  # sampled year vs full year (paper: -3.7 / +0.4%)


@needs_results
def test_full_year_every_precool_costs():
    for k, v in R_()["precool_fullyear"].items():
        assert v["min_increase_pct"] > 0, k
        assert v["best_policy"] == [0, 0] if k.startswith("compliant/") else v["best_policy"] in ([0, 0], None), k
        if v["best_policy"] is None:
            assert v["hot_Kh"] == 0 and v["cold_Kh"] > 0 and v["n_admissible"] == 0, k   # only the floor fails


@needs_results
def test_tenants():
    """Every renter saves by raising the setpoint, and the envelope is worth more to each renter than the setpoint."""
    for c, v in R_()["tenant_savings"].items():
        for k, a in v["apartments"].items():
            assert a["setpoint_SAR"] > 0 and a["envelope_SAR"] > a["setpoint_SAR"], (c, k)


@needs_results
def test_passive_zones_with_and_without_controller():
    R = R_()["passive_control"]
    for c in CT:
        S = R[c]["scenarios"]
        kh = lambda t: sum(S[t]["groups"][g]["hot_Kh"] for g in ("baths_exterior", "baths_interior"))
        assert kh("controller") < 0.2 * kh("no_controller")
        assert kh("controller_doors_closed") > kh("controller")
        assert kh("unit_in_every_bath") == 0
        assert S["controller"]["conditioned_T_max"] <= 24.0 + 1e-6


@needs_results
def test_algorithm2_as_printed_matches_pipeline():
    from experiments.optimiser import optimiser as O
    R = R_()
    for c in CT:
        out = O.optimise(c, "compliant")
        ref = R[f"scheduling_{c}"]["compliant"]["volume"]
        assert list(out["theta"]) == ref["best_policy"] == [0, 0]
        assert round(out["baseline_bill"]) == ref["base_bill"]


@needs_results
def test_seasonal_levers_consistent_with_scopes():
    R = R_()
    for c in CT:
        for sc in SCS:
            a = R["seasonal_levers"][c][sc]["annual"]; s = R[f"scopes_{c}"][sc]
            assert abs(a["setpoint_pct"] - s["compliant"]["setpoint_pct"]) <= 0.05
            assert abs(a["envelope_pct"] - s["envelope_pct"]) <= 0.05


@needs_results
def test_band_cycles_of_running_example():
    """Exact affine drift/leap cycles of the lumped room: the heat removed equals the heat admitted at the cycle's
    mean temperature, and deeper cycles (lower mean) cost more (Theorem 1)."""
    ex = R_()["running_example"]; B = ex["band_cycles"]; cyc = B["cycles"]
    for c in cyc:
        assert abs(c["mean_power_kW"] - c["power_at_mean_level_kW"]) / c["mean_power_kW"] < 0.01, c["cycle"]
    p = [c["mean_power_kW"] for c in cyc]; v = [c["mean_temp_C"] for c in cyc]
    assert p[0] < p[1] < p[2] and v[0] > v[1] > v[2]


@needs_results
def test_ablation_every_component_needed():
    for c, d in R_()["ablation"].items():
        rows = d["rows"]; b0 = rows["full"]["bill"]
        for k, v in rows.items():
            if k != "full":
                assert v["bill"] > b0 and v["delta_pct"] > 0, (c, k)


@needs_results
def test_time_of_use_contrast():
    for k, v in R_()["tou"].items():
        rows = v["rows"]; s = [rows[r]["saving_pct"] for r in ("1", "2", "3", "5")]
        if k.startswith("two_node"):                                         # the one-node zone is the reported exception
            assert rows["1"]["best_policy"] == [0, 0] and s[0] == 0, k
        assert s[1] <= s[2] <= s[3] and s[3] > 0, k


def test_lower_bound_consistency():
    """Perfect-foresight benchmark: with pre-cooling forbidden (rooms held within 0.01 K of the ceiling) the linear
    program reproduces the hold-at-ceiling energy; allowing deeper pre-cooling can only lower its cost, and the
    optimal schedule never uses the higher modes (Case 1, Riyadh, July mean day)."""
    from experiments.optimiser import lower_bound as LB
    try:
        M.set_scope("apartment")
        r0 = LB.solve("Riyadh", month=6, vmin=M.COMFORT[1] - 0.01, gamma=0.0)
        assert abs(r0["gap_pct"]) < 0.2
        g = [LB.solve("Riyadh", month=6, vmin=v, gamma=0.0)["gap_pct"] for v in (23.0, 22.0, 20.0)]
        assert 0 <= g[0] <= g[1] <= g[2]                     # gamma = 0: an exact LP, monotone in the pre-cooling floor
        r2 = LB.solve("Riyadh", month=6, vmin=22.0)           # gamma = 0.02: achievable saving, below the gamma = 0 bound
        assert r2["gap_pct"] <= g[1] and r2["high_mode_share"] < 1e-6
    finally:
        M.set_scope("building")


def test_lower_bound_solvers_agree():
    """The two interior-point solvers of the benchmark (HiGHS, used for Cases 1 and 2 and the top floor; Clarabel, used for
    Case 3 and the 31-day horizon) give the same bound on the same program (Case 1, Riyadh, July mean day, gamma = 0)."""
    pytest.importorskip("clarabel")
    from experiments.optimiser import lower_bound as LB
    try:
        M.set_scope("apartment")
        h, c = (LB.solve("Riyadh", month=6, vmin=22.0, gamma=0.0, solver=s) for s in ("highs", "clarabel"))
        assert abs(h["gap_pct"] - c["gap_pct"]) < 1e-3 and abs(h["E_lp"] / c["E_lp"] - 1) < 1e-5
    finally:
        M.set_scope("building")


@needs_results
def test_lower_bound_bracket():
    """Every horizon solved: the gamma = 0 bound is at least the achievable saving at gamma = 0.02 (Cases 1 and 2, the top
    floor, the 31-day July horizon); Case 3 has its bound on every design day of Case 2."""
    R = R_(); LBR = R["lower_bound"]; LBB = R["lower_bound_building"]
    for sc in ("apartment", "floor", "top_floor"):
        for c in CT:
            for vm in (22.0, 20.0):
                b = LBR[f"{sc}/{c}/vmin{vm}/gamma0.0"]["days"]; a = LBR[f"{sc}/{c}/vmin{vm}/gamma0.02"]["days"]
                assert all(b[d]["gap_pct"] >= max(a[d]["gap_pct"], 0) for d in b), (sc, c, vm)
        for c in CT:
            ch = LBR[f"apartment/{c}/july_chained"] if sc == "apartment" else None
            if ch: assert ch["bound_gap_pct"] >= max(ch["gap_pct"], 0)
    assert all(len(LBB[f"building/{c}/vmin{vm}/gamma0.0"]["days"]) == 3 for c in CT for vm in (22.0, 20.0))


@needs_results
def test_sensitivity():
    """The setpoint lever survives every sensitivity variant; thermostat pre-cooling saves at most a
    few hundredths of a percent in every variant with the indoor-temperature COP; cycling
    losses and fixed-speed units cost energy; units sized by load at the comfort-equivalent margin hold the band and
    save, partly through their size alone."""
    R = R_(); V = R["revision"]["variants"]
    allv = {**V, **{k: R["robustness"][k] for k in R["robustness"] if k != "full_year_riyadh_prepeak"},
            **{k: R["robustness_extra"][k] for k in R["robustness_extra"] if k != "operative"}}
    for c in CT:
        assert all(4 < v[c]["setpoint_pct"] < 25 for v in allv.values())
        assert all(v[c]["best_precool_pct"] <= 0.1 for k, v in allv.items()
                   if k != "one_node_cop_indoor_0" and v[c]["best_precool_pct"] is not None), c
        assert all(v[c]["reference_admissible"] for k, v in allv.items() if k != "right_sized_tight"), c
        assert V["cycling_cd0"][c]["bill"] < V["reference"][c]["bill"] < V["cycling_cd0.25"][c]["bill"]
        assert V["three_ton_inverter"][c]["bill"] < V["reference"][c]["bill"]
        assert V["right_sized"][c]["hot_Kh"] == 0 and V["right_sized_keep_fixed"][c]["hot_Kh"] == 0
        assert V["right_sized"][c]["bill"] < V["right_sized_keep_fixed"][c]["bill"] < V["reference"][c]["bill"]


@needs_results
def test_setback_validation():
    """EnergyPlus and the engine agree that a 2 K setback removes more heat than holding the reference setpoint."""
    sb = json.load(open(ROOT / "results" / "comparative.json"))["setback"]
    for k, v in sb.items():
        for q in ("prepeak_2K", "night_2K"):
            assert v[q]["ep_delta_pct"] > 0 and v[q]["engine_delta_pct"] > 0, (k, q)


@needs_results
def test_equipment_split_reproduces_three_ton_saving():
    """The electricity of the fixed-speed units, recomputed as inverter units, gives the 3-ton variant of Table 18,
    and the safe-floor check counts only excursions below the floor (the SBC-compliant building has none)."""
    R = R_()
    for c, v in R["equipment_split"].items():
        ref = R["revision"]["variants"]["reference"][c]["bill"]; inv = R["revision"]["variants"]["three_ton_inverter"][c]["bill"]
        d_kwh = v["total_unit_kwh"] - v["total_unit_kwh_all_inverter"]
        assert abs(d_kwh * 0.207 - (ref - inv)) / ref < 0.005, c
        assert abs(sum(v[g]["kwh_share_pct"] for g in ("guest_fixed", "stair_fixed", "inverter")) - 100) < 0.5, c
    for k, v in R["safe_floor"].items():
        if k.startswith("compliant/"):
            assert v["n_violating"] == 0, k
        assert v["all_costlier"], k


@needs_results
def test_precool_robustness_appendix():
    """Pre-cooling saves in no robustness test except with the favourable choices combined; a full-capacity start does
    not change the comfort of load-sized units; humidity per lever is reported for both cities."""
    R = R_()
    for c in CT:
        assert all(v["best_saving_pct"] <= 0 for v in R["controller_resolution"][c].values() if v["best_saving_pct"] is not None), c
        assert R["precool_targeted"][c]["top_floor"]["best_saving_pct"] <= 0 and R["precool_targeted"][c]["west_rooms"]["best_saving_pct"] <= 0, c
        assert R["joint_favourable"][c]["best_saving_pct"] < 1.0, c
        sm = R["sizing_margin"][c]
        for m in sm["base_controller"]:
            assert abs(sm["base_controller"][m]["hot_Ch"] - sm["full_capacity_start"][m]["hot_Ch"]) <= 1.0, (c, m)
        first = min((float(m) for m, v in sm["base_controller"].items() if v["hot_Ch"] == 0), default=None)
        assert first == R["revision"]["right_sizing"][c]["margin"], c   # first margin with no room outside the safe set
    assert set(R["humidity_levers"]) == {f"{c}/SHR{s}" for c in CT for s in (0.75, 0.65)}


@needs_results
def test_tight_units_admissible_at_lower_setpoint():
    """Units sized with a 15% margin break the ceiling at the reference setpoint; a lower setpoint restores admissibility."""
    for k, v in R_()["tight_frontier"].items():
        assert not v["admissible"][-1] and v["setpoints"][-1] == 23.5, k
        assert v["frontier_setpoint"] is not None and v["frontier_setpoint"] < 23.5, k
        assert v["saving_vs_catalogue_pct"] > 0, k


@needs_results
def test_floor_guard_enforces_the_safe_floor():
    """Without the floor guard the pre-code building leaves the 20 degC floor over the full year; with it every reference
    run is admissible, at a negligible cost, and the guard never acts in the SBC-compliant building."""
    for k, v in R_()["floor_guard"].items():
        assert v["with_guard"]["admissible"] and v["with_guard"]["cold_Kh"] == 0, k
        assert abs(v["guard_bill_pct"]) < 0.1, k
        if k.startswith("compliant/"):
            assert v["guard_heat_kwh"] == 0, k
        else:
            assert not v["without_guard"]["admissible"], k


@needs_results
def test_factorial_decomposition():
    """2^4 factorial: every combination admissible; Shapley shares sum to 100 %; the envelope takes the largest share and
    dropping pre-cooling the smallest; the levers are sub-additive."""
    F = R_()["factorial"]
    for c in CT:
        v = F[c]
        assert v["all_admissible"], c
        assert abs(sum(v["shapley_share_pct"].values()) - 100) < 0.5, c
        sh = v["shapley_share_pct"]
        assert max(sh, key=sh.get) == "envelope" and min(sh, key=sh.get) == "no_precool", c
        assert all(x > 0 for k, x in v["interaction_pct_of_start"].items() if "no_precool" not in k), c


@needs_results
def test_uncertainty_analysis():
    """Latin-hypercube uncertainty analysis: a 2 K pre-peak pre-cool costs in every sample and the lever ordering holds in
    most admissible samples."""
    R = R_()
    for c in CT:
        if f"uncertainty_{c}" not in R:
            continue
        u = R[f"uncertainty_{c}"][c]
        assert u["n_samples"] == len(u["samples"]) > 0, c
        assert u["prepeak2_always_costs"], c
        assert u["share_ranking_holds_pct"] >= 80, c


@needs_results
def test_fullyear_admissibility():
    """Every run the results rely on stays within the safe set on all 365 days (strict rule)."""
    R = R_()
    for c in CT:
        k = f"fullyear_admissibility_{c}"
        if k in R:
            assert R[k][c]["all_admissible"], (c, R[k][c]["inadmissible"])
