#!/usr/bin/env python3
"""
reproduce.py -- regenerates every number reported in the paper
"HVAC Savings in Saudi Residential Buildings" from the config-driven simulator.

Study object: a four-storey SBC apartment complex (experiments/configs/building.json), analysed as three cases --
Case 1: one apartment (one household meter); Case 2: the typical floor of two apartments; Case 3: the
whole complex (all meters) -- in Riyadh and Jeddah.

The run is RESUMABLE: every block writes results/parts/<block>.json and is skipped on the next
run if that file exists (use --force to recompute). All parts are merged into results/results.json.

    hvac-reproduce                      # run every missing block, then merge
    hvac-reproduce --blocks stage1,scheduling_Jeddah
    hvac-reproduce --force --blocks scopes_Riyadh
    hvac-reproduce --list               # show block names and status
"""
import os, sys, json, time
from pathlib import Path
import numpy as np

from hvac_savings import model as M
from hvac_savings.comparative import patched
from experiments.optimiser import optimiser as O

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results"; PARTS = OUT / "parts"; PARTS.mkdir(parents=True, exist_ok=True)
EXP = json.load(open(ROOT / "experiments" / "configs" / "experiments.json"))
CITIES = EXP["cities"]; ENVS = EXP["envelopes"]; FRAC = EXP["hvac_fraction"]; MAIN = EXP["main_envelope"]
PCG = EXP["precool_grid"]["night_depth"]; PPG = EXP["precool_grid"]["prepeak_depth"]
REF_FLOORS = M._BLD["reference_floors"]
cs, hs = M.CODE[0], M.CODE[1]
GRID = [[cs, hs, a, b] for a in PCG for b in PPG]          # (0,0) first = no scheduling
SD = EXP["sensitivity_depths"]                            # pre-cooling depths of the sensitivity runs [K]
SENS_POLS = [M.CODE, M.CURRENT] + [[cs, hs, 0, x] for x in SD] + [[cs, hs, x, 0] for x in SD]
r1 = lambda x: round(float(x), 1); r2 = lambda x: round(float(x), 2); r3 = lambda x: round(float(x), 3)
SAT = 0.85                                                # a unit is near saturation if its peak hourly load reaches 85 % of rated capacity
RS = EXP["right_sizing"]; TIGHT_MARGIN = RS["tight_margin"]   # units sized to the peak hourly load: tight margin (hard instances)
TWO = lambda: dict(M.ZONE_MODEL)                          # the base-case two-node zone model


def hvac_i(kwh, area=None):
    """HVAC energy intensity [kWh/m2 yr] (electricity of the units, sensible + latent + standby, per m2 of floor)."""
    return r1(kwh / (M.FLOOR_AREA if area is None else area))


def load_ratio(r):
    """Peak hourly sensible cooling of each conditioned unit as a fraction of its rated capacity."""
    cz = M.COND
    return r["q_hour_max"][cz] / M.QC[cz]


def adm(R):
    """Admissibility of each run (no excursion outside the safe set, Algorithm 2)."""
    return np.array([O.admissible(r) for r in R])


def argbest(b, feas):
    """Index of the cheapest admissible run, or None if none is admissible."""
    return int(np.argmin(np.where(feas, b, np.inf))) if np.any(feas) else None


def sens_row(R, area=None):
    """Metrics of a SENS_POLS run: intensity, setpoint lever, best comfort-feasible pre-cool, 1 and 2 K costs."""
    b = np.array([r["bill"] for r in R]); h = np.array([r["cviol_hot"] for r in R]); n = len(SD)
    ok = adm(R); sub = list(range(2, len(R))); feas = [i for i in sub if ok[i]]
    kbest = min(feas, key=lambda i: b[i]) if feas else 0
    i1, i2, n2 = 2 + SD.index(1.0), 2 + SD.index(2.0), 2 + n + SD.index(2.0)
    return dict(EUI=hvac_i(R[0]["kwh"], area), setpoint_pct=r1(pct(b[0], b[1])),
                best_precool_pct=(r2(max(0.0, pct(b[kbest], b[0]))) if kbest else 0.0) if ok[0] else None,
                best_precool_policy=(SENS_POLS[kbest][2:] if kbest else [0, 0]) if ok[0] else None,
                reference_admissible=bool(ok[0]), n_admissible=int(ok.sum()), hot_Kh=r2(h[0]),
                prepeak1_pct=r2(-pct(b[i1], b[0])), prepeak2_pct=r2(-pct(b[i2], b[0])), night2_pct=r2(-pct(b[n2], b[0])),
                hot_Ch=round(h[0]), bill=round(b[0]), latent_share_pct=r1(R[0]["kwh_lat"] / R[0]["kwh"] * 100))


def pct(new, old):
    return (1 - new / old) * 100


def sim(city, env, pols, tar="volume"):
    return M.simulate_batch(city, env, pols, tar)


def apt_meters():
    """Meters of the side-A apartments, ground floor first (B is the mirror image)."""
    return [M.apartment_meter(f, "A") for f in range(M.FLOORS)]


def cond_rooms_of(meter):
    return [i for i in range(M.NZ) if M.METER[i] == meter and M.COND[i]]


def grid_saving(city, env, tar):
    """Algorithm 2 over the pre-cool lattice: the cheapest admissible policy (no excursion outside the safe set);
    savings for the building and the rep. apartment."""
    R = sim(city, env, GRID, tar); b = np.array([r["bill"] for r in R]); h = np.array([r["cviol_hot"] for r in R])
    feas = adm(R); k = argbest(b, feas)
    ra = np.array([r["meter_bill"][M.REP_METER] for r in R]); kw = np.array([r["kwh"] for r in R])
    if k is None:
        return dict(bldg_pct=None, best_policy=None, apt_pct=None, base_bill=round(b[0]), n_infeasible=len(GRID),
                    safe_violation_Kh=r2(max(r["sviol_cold"] for r in R)), n_policies=len(GRID), linear_pct=None)
    return dict(bldg_pct=r2(max(0.0, pct(b[k], b[0]))), best_policy=GRID[k][2:],
                apt_pct=r2(pct(ra[k], ra[0])), base_bill=round(b[0]), n_infeasible=int((~feas).sum()),
                safe_violation_Kh=r2(max(r["sviol_cold"] for r in R)), n_policies=len(GRID),
                linear_pct=r2(max(0.0, pct(kw[feas].min(), kw[0]))))


# --------------------------------------------------------------------------- blocks
def b_stage1():
    out = {"per_configuration": {}, "envelope": {}}
    RM = M.REP_METER; ams = apt_meters()
    for env in ENVS:
        for c in CITIES:
            cod, cur = sim(c, env, [M.CODE, M.CURRENT])
            km = cod["meter_kwh_m"][RM]; ca = cond_rooms_of(RM)
            out["per_configuration"][f"{env}/{c}"] = dict(
                apartment=dict(kwh=round(cod["meter_kwh"][RM]), bill=round(cod["meter_bill"][RM]),
                               EUI=hvac_i(cod["meter_kwh"][RM], M.APT_AREA),
                               kwh_current=round(cur["meter_kwh"][RM]), bill_current=round(cur["meter_bill"][RM]),
                               setpoint_pct=r1(pct(cod["meter_bill"][RM], cur["meter_bill"][RM])),
                               tier2_share_pct=r1(np.maximum(km - 6000, 0).sum() / km.sum() * 100),
                               max_month_kwh=round(km.max()), max_month_household_kwh_est=round(km.max() / FRAC),
                               util_mean=r3(cod["util"][ca].mean())),
                building=dict(kwh=round(cod["kwh"]), bill=round(cod["bill"]),
                              EUI=hvac_i(cod["kwh"]),
                              kwh_current=round(cur["kwh"]), bill_current=round(cur["bill"]),
                              setpoint_pct=r1(pct(cod["bill"], cur["bill"])), peak_hourly_kw=r1(cod["peak_hourly_kw"]),
                              max_meter_month_kwh=round(cod["meter_kwh_m"].max()),
                              max_meter_month_household_kwh_est=round(cod["meter_kwh_m"][:-1].max() / FRAC)),
                util_mean=r3(cod["util"][M.COND].mean()), peak_load_ratio=r2(load_ratio(cod).max()),
                standby_kwh=round(cod["kwh_standby"]), standby_share_pct=r1(100 * cod["kwh_standby"] / cod["kwh"]),
                hot_excursion_Ch=round(cod["cviol_hot"]), cold_excursion_Ch=round(cod["cviol"] - cod["cviol_hot"]),
                latent_share_pct=r1(cod["kwh_lat"] / cod["kwh"] * 100), latent_kwh=round(cod["kwh_lat"]),
                apartment_kwh_by_floor=[round(cod["meter_kwh"][m]) for m in ams],
                common_meter_kwh=round(cod["meter_kwh"][-1]))
    for c in CITIES:
        a = out["per_configuration"][f"compliant/{c}"]; b = out["per_configuration"][f"noncompliant/{c}"]
        out["envelope"][c] = dict(
            apartment_pct=r1(pct(a["apartment"]["kwh"], b["apartment"]["kwh"])),
            building_pct=r1(pct(a["building"]["kwh"], b["building"]["kwh"])),
            by_floor_pct=[r1(pct(x, y)) for x, y in zip(a["apartment_kwh_by_floor"], b["apartment_kwh_by_floor"])])
    return out


def b_scheduling(city):
    def f():
        out = {}
        for env in ENVS:
            out[env] = dict(volume=grid_saving(city, env, "volume"))
        # single-lever probes on the SBC-compliant building (2 K night / pre-peak pre-cooling): cost change of each lever
        base, night, prepk = sim(city, MAIN, [M.CODE, [cs, hs, 2, 0], [cs, hs, 0, 2]])
        RM = M.REP_METER
        out["levers_bldg_pct"] = dict(night_precool_volume=r1(-pct(night["bill"], base["bill"])),
                                      prepeak_precool_volume=r1(-pct(prepk["bill"], base["bill"])),
                                      hot_excursion=[round(x["cviol_hot"]) for x in (base, night, prepk)])
        out["running_example_apartment"] = dict(
            volume_bill=round(base["meter_bill"][RM]),
            precool_increase_pct=r1(-pct(prepk["meter_bill"][RM], base["meter_bill"][RM])))
        out["demand"] = dict(peak_base_kw=r1(base["peak_hourly_kw"]), peak_precool_kw=r1(prepk["peak_hourly_kw"]),
                             ratio=r2(prepk["peak_hourly_kw"] / base["peak_hourly_kw"]))
        return out
    return f


def b_operations():
    """Without/with optimisation, seasonal profile (apartment + building)."""
    out = {"without_with": {}, "seasonal": {}}
    RM = M.REP_METER
    for c in CITIES:
        w0 = sim(c, "noncompliant", [M.CURRENT, M.CODE]); w1 = sim(c, "compliant", [M.CODE])[0]
        wo, nc = w0
        out["without_with"][c] = dict(
            apartment=dict(kwh_without=round(wo["meter_kwh"][RM]), kwh_with=round(w1["meter_kwh"][RM]),
                           bill_without=round(wo["meter_bill"][RM]), bill_with=round(w1["meter_bill"][RM]),
                           bill_saving_pct=r1(pct(w1["meter_bill"][RM], wo["meter_bill"][RM])),
                           sar_saved=round(wo["meter_bill"][RM] - w1["meter_bill"][RM])),
            building=dict(kwh_without=round(wo["kwh"]), kwh_with=round(w1["kwh"]),
                          bill_without=round(wo["bill"]), bill_with=round(w1["bill"]),
                          bill_saving_pct=r1(pct(w1["bill"], wo["bill"])), sar_saved=round(wo["bill"] - w1["bill"])))
        mn = w1 if MAIN == "compliant" else nc
        for tag, km in (("apartment", mn["meter_kwh_m"][RM]), ("building", mn["kwh_m"])):
            su = km[[5, 6, 7, 8]].sum(); wi = km[[11, 0, 1]].sum()
            out["seasonal"].setdefault(c, {})[tag] = dict(summer=round(su), shoulder=round(km.sum() - su - wi),
                                                          winter=round(wi), summer_share_pct=r1(su / km.sum() * 100),
                                                          monthly=[round(x) for x in km])
    return out


def b_rooms():
    """Room-level behaviour of the representative apartment, coupling, and the toilets."""
    out = {}; RM = M.REP_METER; apt = [i for i in range(M.NZ) if M.METER[i] == RM]
    for c in CITIES:
        for env in ENVS:
            r = sim(c, env, [M.CODE])[0]; cz = np.where(M.COND)[0]
            core = [i for i in cz if M.is_core[i]]; per = [i for i in cz if not M.is_core[i]]
            out[f"{env}/{c}"] = dict(
                rooms={M.ROOM[i]: dict(cond=bool(M.COND[i]), util=r3(r["util"][i]),
                                       T_mean=r1(r["Vmean"][i]), T_max=r1(r["Vmax"][i])) for i in apt},
                util_min=r3(r["util"][cz].min()), util_max=r3(r["util"][cz].max()),
                spread=r2(r["util"][cz].max() / r["util"][cz].min()),
                core_mean=r3(r["util"][core].mean()), perimeter_mean=r3(r["util"][per].mean()))
    # inter-zone coupling switched off (Jeddah, SBC-compliant)
    K0, R0, KD0 = M.Kc.copy(), M.Krow.copy(), M.K_DOOR
    on = sim("Jeddah", MAIN, [M.CODE])[0]
    M.Kc = np.zeros_like(K0); M.Krow = np.zeros_like(R0); M.K_DOOR = 0.0      # no walls, slabs or doors between rooms
    off = sim("Jeddah", MAIN, [M.CODE])[0]; M.Kc, M.Krow, M.K_DOOR = K0, R0, KD0
    cz = np.where(M.COND)[0]; core = [i for i in cz if M.is_core[i]]; per = [i for i in cz if not M.is_core[i]]
    tl = [i for i in range(M.NZ) if not M.COND[i]]
    out["coupling"] = dict(total_change_pct=r2(-pct(on["kwh"], off["kwh"])),
                           core_util_on=r3(on["util"][core].mean()), core_util_off=r3(off["util"][core].mean()),
                           perim_util_on=r3(on["util"][per].mean()), perim_util_off=r3(off["util"][per].mean()),
                           spread_on=r2(on["util"][cz].max() / on["util"][cz].min()),
                           spread_off=r2(off["util"][cz].max() / off["util"][cz].min()),
                           toilet_T_mean_on=r1(on["Vmean"][tl].mean()), toilet_T_mean_off=r1(off["Vmean"][tl].mean()))
    # counterfactual: toilets fitted with 1-ton units
    C0, Q0, H0, L0 = M.COND.copy(), M.QC.copy(), M.QH.copy(), M.LATW.copy()
    t_all = np.array([('bath' in n) for n in M.ROOM]); tc = {}     # wet rooms only
    for c in CITIES:
        base = sim(c, "compliant", [M.CODE])[0]
        CD0 = M.CYCLING_CD
        M.COND = C0 | t_all; M.QC = np.where(t_all, M.TON_KW, Q0); M.QH = M.QC * (M.Q_HEAT / M.Q_COOL)
        M.LATW = np.bincount(M.METER, M.COND * M.AREA / M.A_REF, M.NMET)
        M.CYCLING_CD = np.where(t_all, M._pl["cycling_Cd"]["inverter"], CD0)
        withac = sim(c, "compliant", [M.CODE])[0]
        M.COND, M.QC, M.QH, M.LATW, M.CYCLING_CD = C0, Q0, H0, L0, CD0
        tc[c] = dict(apartment_pct=r1(pct(base["meter_kwh"][RM], withac["meter_kwh"][RM])),
                     building_pct=r1(pct(base["kwh"], withac["kwh"])))
    out["wet_rooms_without_vs_with_ac"] = tc
    return out


def b_humidity():
    out = {}; RM = M.REP_METER; W0 = M.W_IN
    for c in CITIES:
        for env in ENVS:
            h = sim(c, env, [M.CODE, M.CURRENT]); M.W_IN = 1.0
            s = sim(c, env, [M.CODE, M.CURRENT]); M.W_IN = W0
            out[f"{env}/{c}"] = dict(
                humidity_aware_setpoint_pct=r1(pct(h[0]["meter_bill"][RM], h[1]["meter_bill"][RM])),
                sensible_only_setpoint_pct=r1(pct(s[0]["meter_bill"][RM], s[1]["meter_bill"][RM])),
                latent_kwh_building=round(h[0]["kwh_lat"]), latent_share_pct=r1(h[0]["kwh_lat"] / h[0]["kwh"] * 100))
    return out


def b_mass(Cz):
    def f():
        C0 = M.CZ.copy(); M.CZ = Cz * M.AREA / M.A_REF
        try:
            return dict(C_ref_kWhK=Cz, volume=grid_saving("Jeddah", MAIN, "volume"))
        finally:
            M.CZ = C0
    return f


def b_windows(city):
    def f():
        E0 = {k: dict(v) for k, v in M.ENVELOPES.items()}; W0 = M.WFRAC; wu = EXP["window_uncertainty"]
        cases = []
        try:
            for wf in wu["wfrac"]:
                for um in wu["uwin_multiplier"]:
                    M.WFRAC = wf
                    for k in E0: M.ENVELOPES[k]["U_WIN"] = E0[k]["U_WIN"] * um
                    cp, cpc = sim(city, "compliant", [M.CODE, M.CURRENT]); nc, ncc = sim(city, "noncompliant", [M.CODE, M.CURRENT])
                    mb, mbc = (cp, cpc) if MAIN == "compliant" else (nc, ncc)
                    cases.append(dict(wfrac=wf, uwin_mult=um, envelope_pct=r1(pct(cp["kwh"], nc["kwh"])),
                                      setpoint_pct=r1(pct(mb["bill"], mbc["bill"]))))
            ext = []
            for wf, um in ((min(wu["wfrac"]), min(wu["uwin_multiplier"])), (max(wu["wfrac"]), max(wu["uwin_multiplier"]))):
                M.WFRAC = wf
                for k in E0: M.ENVELOPES[k]["U_WIN"] = E0[k]["U_WIN"] * um
                g = grid_saving(city, MAIN, "volume"); ext.append(dict(wfrac=wf, uwin_mult=um, volume_pct=g["bldg_pct"]))
        finally:
            M.WFRAC = W0
            for k in E0: M.ENVELOPES[k]["U_WIN"] = E0[k]["U_WIN"]
        env = [x["envelope_pct"] for x in cases]; stp = [x["setpoint_pct"] for x in cases]
        return dict(cases=cases, envelope_range=[min(env), max(env)], setpoint_range=[min(stp), max(stp)],
                    scheduling_at_extremes=ext)
    return f


def b_gains():
    """Robustness to the (unknown) internal-gain level: scale the diurnal gain profile to a given annual mean."""
    G0 = (M.GAINb, M.GAINd); base_mean = (M.GAINb + M.GAINd * 0.3183) * 1000 / M.A_REF; out = []
    try:
        for g in EXP["internal_gain_sweep"]["mean_W_m2"]:
            k = g / base_mean; M.GAINb, M.GAINd = G0[0] * k, G0[1] * k; row = dict(mean_W_m2=g)
            for c in CITIES:
                nc, ncc = sim(c, "noncompliant", [M.CODE, M.CURRENT]); cp, cpc = sim(c, "compliant", [M.CODE, M.CURRENT])
                mb, mbc = (cp, cpc) if MAIN == "compliant" else (nc, ncc)
                row[c] = dict(EUI_noncompliant=hvac_i(nc["kwh"]), EUI_compliant=hvac_i(cp["kwh"]),
                              envelope_pct=r1(pct(cp["kwh"], nc["kwh"])), setpoint_pct=r1(pct(mb["bill"], mbc["bill"])),
                              volume_pct=grid_saving(c, MAIN, "volume")["bldg_pct"])
            out.append(row)
    finally:
        M.GAINb, M.GAINd = G0
    return out


def b_absorptance(city):
    def f():
        a0 = M.ALPHA; out = []
        try:
            for a in EXP["absorptance_sweep"]:
                M.ALPHA = a
                cp, cpc = sim(city, "compliant", [M.CODE, M.CURRENT]); nc, ncc = sim(city, "noncompliant", [M.CODE, M.CURRENT])
                mb, mbc = (cp, cpc) if MAIN == "compliant" else (nc, ncc)
                out.append(dict(alpha=a, envelope_pct=r1(pct(cp["kwh"], nc["kwh"])), setpoint_pct=r1(pct(mb["bill"], mbc["bill"])),
                                EUI_noncompliant=hvac_i(nc["kwh"]), EUI_compliant=hvac_i(cp["kwh"])))
            ext = []
            for a in (min(EXP["absorptance_sweep"]), max(EXP["absorptance_sweep"])):
                M.ALPHA = a; ext.append(dict(alpha=a, volume_pct=grid_saving(city, MAIN, "volume")["bldg_pct"]))
        finally:
            M.ALPHA = a0
        return dict(cases=out, scheduling_at_extremes=ext)
    return f


def b_cop_swing():
    orig = M.cop_factor; g = EXP["cop_swing_grid"]; pols = [[cs, hs, a, b] for a in g for b in g]; res = []
    try:
        for beta, lo, hi in EXP["cop_swing"]:
            M.cop_factor = lambda Ta, b=beta, l=lo, h=hi: np.clip(1 - b * (Ta - 27), l, h)
            ratio = float(M.cop_factor(22) / M.cop_factor(40)); row = [r2(ratio)]
            for c in CITIES:
                R = sim(c, MAIN, pols)
                bl = [r["bill"] for r in R if O.admissible(r)]                    # admissible policies only
                row.append(r2(max(0.0, pct(min(bl), R[0]["bill"]))))
            res.append(row)
    finally:
        M.cop_factor = orig
    return dict(columns=["cop_ratio_22C_vs_40C"] + [f"{c}_volume_pct" for c in CITIES], rows=res)


def b_verification():
    out = {}
    # V1: physical consistency -- no cooling, no heating: conditioned rooms must overheat
    r = sim("Jeddah", "noncompliant", [[99.0, -50.0, 0, 0]])[0]
    top = M.IX[(M.FLOORS - 1, "A.master_bedroom")]; mid = M.IX[(M.REP_METER // 2, "A.master_bedroom")]
    out["V1_uncooled"] = dict(top_master_July_mean=r1(r["Vmean_m"][6][top]), top_master_max=r1(r["Vmax"][top]),
                              mid_master_July_mean=r1(r["Vmean_m"][6][mid]),
                              building_max=r1(r["Vmax"][M.COND].max()))
    # V2: numerical convergence in the integration step
    DT0, SUB0 = M.DT, M.SUB; v2 = {}
    try:
        for c, env in (("Jeddah", "noncompliant"), ("Riyadh", "compliant")):
            row = {}
            for dt in EXP["convergence_check_dt_h"]:
                M.DT = dt; M.SUB = int(round(1 / dt))
                rr = sim(c, env, [M.CODE, M.CURRENT, [cs, hs, 0, 2]])
                row[f"{dt * 60:g}min"] = (rr[0]["kwh"] / M.FLOOR_AREA, pct(rr[0]["bill"], rr[1]["bill"]),
                                          -pct(rr[2]["bill"], rr[0]["bill"]))
            M.DT, M.SUB = DT0, SUB0
            dts = EXP["convergence_check_dt_h"]; ref = row[f"{DT0 * 60:g}min"]; fine = row[f"{dts[-1] * 60:g}min"]; coarse = row[f"{dts[0] * 60:g}min"]
            v2[f"{env}/{c}"] = {k: r2(v[0]) for k, v in row.items()}
            v2[f"{env}/{c}"]["refine_change_pct"] = r2(abs(fine[0] / ref[0] - 1) * 100)
            v2[f"{env}/{c}"]["coarse_change_pct"] = r2(abs(coarse[0] / ref[0] - 1) * 100)
            v2[f"{env}/{c}"]["setpoint_pct_by_step"] = {k: r2(v[1]) for k, v in row.items()}
            v2[f"{env}/{c}"]["prepeak2_pct_by_step"] = {k: r2(v[2]) for k, v in row.items()}
    finally:
        M.DT, M.SUB = DT0, SUB0
    out["V2_convergence_EUI"] = v2
    return out


def b_scopes(city):
    """The three analysis scopes: (a) one apartment, (b) the typical floor, (c) the whole complex.
    For each scope and envelope: EUI, setpoint saving, comfort frontier, scheduling under the volume tariff,
    comfort, capacity, and the busiest meter-month (tier-2 check)."""
    def f():
        out = {}; sps = [round(x, 2) for x in np.arange(cs, M.COMFORT[1] + 1e-6, M.SP_STEP)]
        try:
            for sc in M.SCOPES:
                M.set_scope(sc, REF_FLOORS); RM = M.REP_METER; o = dict(spaces=int(M.NZ), units=int(M.COND.sum()),
                                                                     area=round(M.FLOOR_AREA, 1), meters=int(M.NMET)); cz = np.where(M.COND)[0]
                for env in ENVS:
                    cod, cur = sim(city, env, [M.CODE, M.CURRENT])
                    Fr = sim(city, env, [[v, hs, 0, 0] for v in sps])
                    k = max(i for i, r in enumerate(Fr) if O.admissible(r))
                    o[env] = dict(kwh=round(cod["kwh"]), bill=round(cod["bill"]), EUI=hvac_i(cod["kwh"]),
                                  rep_apartment_kwh=round(cod["meter_kwh"][RM]), rep_apartment_bill=round(cod["meter_bill"][RM]),
                                  setpoint_pct=r1(pct(cod["bill"], cur["bill"])),
                                  frontier_setpoint=sps[k], frontier_extra_pct=r1(pct(Fr[k]["bill"], Fr[0]["bill"])),
                                  volume=grid_saving(city, env, "volume"),
                                  hot_excursion_Ch=round(cod["cviol_hot"]), peak_load_ratio=r2(load_ratio(cod).max()),
                                  saturated_units=int((load_ratio(cod) >= SAT).sum()), warmest_room=r1(cod["Vmax"][cz].max()),
                                  peak_hourly_kw=r1(cod["peak_hourly_kw"]),
                                  max_meter_month_kwh=round(cod["meter_kwh_m"].max()))
                o["envelope_pct"] = r1(pct(o["compliant"]["kwh"], o["noncompliant"]["kwh"]))
                out[sc] = o
        finally:
            M.set_scope("building", REF_FLOORS)
        return out
    return f


def _july_drive(city, env, i):
    """July-mean solar and sky drive of zone i [kW]: opaque sol-air excess + window solar - window sky loss."""
    S_opq, S_win, S_skw = M.solar_drive(city, env); moh = M.ambient(city)[1]; jul = moh == 6
    return float((S_opq[jul, i] + S_win[jul, i] - S_skw[jul, i]).mean())


def b_running_example():
    """Numeric running example: Bed 2 of the representative apartment (SBC envelope, Riyadh, July mean conditions),
    as a lumped (one-node) room, which is how the theory reads it. The thermostat cycles the room over its differential
    2 HYST about the setpoint; the cycle-mean temperature (the level) is the setpoint."""
    city, env = "Riyadh", MAIN
    i = M.IX[(M.REP_METER // 2, "A.bedroom_2")]; e = M.ENVELOPES[env]
    Ta, moh = M.ambient(city)[:2]; jul = moh == 6
    G = M.glazing()[i]; Awin = float((G * M.A_EXT_O[i]).sum())
    Kw = (e["U_WALL"] * (M.A_EXT[i] - Awin) + e["U_WIN"] * Awin) / 1000
    Kinf = M.ACH * M.AREA[i] * M.H_AIR * M._b["air_density"] * M._b["air_cp_kJkgK"] / 3600
    Kenv = Kw + Kinf; nb = {f"{M.SIDE[j]}{int(M.FLOOR_OF[j]) + 1}.{M.ROOM[j]}": round(float(M.Kc[i, j]) * 1000, 1) for j in np.nonzero(M.Kc[i])[0]}
    gain = (M.GAINb + M.GAINd * 0.3183) * M.GAINF[i] * M.AREA[i] / M.A_REF
    Sjul = _july_drive(city, env, i)
    Tjul = float(Ta[jul].mean()); V = M.CODE[0]
    L = Kenv * (Tjul - V) + gain + Sjul                                   # mean heat gain at the level [kW]
    Cz = float(M.CZ[i]); Q = float(M.QC[i]) * float(M.cap_factor(Tjul)); cf = float(M.cop_factor(Tjul)); dV = 2 * M.HYST
    modes = []
    for m in (1, 2, 3):
        q = Q * M.FR[m]; cop = M.COOLc[m] * cf
        if q > L:
            t_on = Cz * dV / (q - L); t_off = Cz * dV / L
            modes.append(dict(mode=m, capacity_kW=r3(q), cop=r2(cop), cool_rate_K_h=r2((q - L) / Cz), t_on_h=r3(t_on),
                              t_off_h=r3(t_off), elec_per_cycle_kWh=r3(q * t_on / cop), mean_power_kW=r3(q * t_on / cop / (t_on + t_off))))
    levels = []
    for lab, Vt in (("current practice", M.CURRENT[0]), ("reference setpoint", M.CODE[0])):
        Lv = Kenv * (Tjul - Vt) + gain + Sjul; q = Q * M.FR[1]; cop = M.COOLc[1] * cf
        t_on = Cz * dV / (q - Lv); t_off = Cz * dV / Lv
        levels.append(dict(level=lab, setpoint_C=Vt, switch_on_C=Vt + M.HYST, load_kW=r3(Lv), t_on_h=r3(t_on), t_off_h=r3(t_off),
                           mean_power_kW=r3(q * t_on / cop / (t_on + t_off))))
    # Exact affine drift/leap of the lumped room (conditioned neighbours at the same level, so only the envelope
    # conductance acts; L(V) = Kenv (Ta - V) + G + S). Three cycles that all reach the ceiling V_high: the thermostat
    # cycle (its differential), a cycle across the whole comfort band, and one across the whole safe set.
    Vfree = Tjul + (gain + Sjul) / Kenv; tau = Cz / Kenv; q1 = Q * M.FR[1]; cop1 = M.COOLc[1] * cf
    V1inf = Vfree - q1 / Kenv; Vh = float(M.COMFORT[1]); cyc = []
    for lab, Vb in (("thermostat cycle", Vh - dV), ("comfort band", float(M.COMFORT[0])), ("safe set", float(M.SAFE[0]))):
        t_off = tau * np.log((Vfree - Vb) / (Vfree - Vh)); t_on = tau * np.log((Vh - V1inf) / (Vb - V1inf))
        P = q1 * t_on / cop1 / (t_on + t_off)
        I_off = Vfree * t_off - (Vfree - Vb) * tau * (1 - np.exp(-t_off / tau))
        I_on = V1inf * t_on + (Vh - V1inf) * tau * (1 - np.exp(-t_on / tau))
        Vlo_ = float(M.COMFORT[0])
        below = 0.0 if Vb >= Vlo_ else tau * np.log((Vlo_ - V1inf) / (Vb - V1inf)) + tau * np.log((Vfree - Vb) / (Vfree - Vlo_))
        Vmean = (I_off + I_on) / (t_on + t_off)
        cyc.append(dict(cycle=lab, V_bottom_C=Vb, V_top_C=Vh, t_off_h=r3(t_off), t_on_h=r3(t_on),
                        mean_temp_C=r2(Vmean), mean_power_kW=round(float(P), 4),
                        power_at_mean_level_kW=round(float((Kenv * (Tjul - Vmean) + gain + Sjul) / cop1), 4),
                        below_band_pct=r1(100 * below / (t_on + t_off))))
    for c_ in cyc: c_["vs_thermostat_pct"] = r1(100 * (c_["mean_power_kW"] / cyc[0]["mean_power_kW"] - 1))
    band = dict(V_free_C=r1(Vfree), V_mode1_inf_C=r1(V1inf), tau_h=r1(tau), cop_mode1=r2(cop1), capacity_mode1_kW=r3(q1),
                V_min_C=float(M.SAFE[0]), V_low_C=float(M.COMFORT[0]), V_high_C=Vh, cycles=cyc)
    return dict(room="bedroom_2 (apartment 2A)", levels_mode1=levels, band_cycles=band, area_m2=r1(M.AREA[i]), unit_ton=float(M.QC[i] / M.TON_KW),
                compressor="fixed_speed" if M.FIXED_SPEED[i] else "inverter",
                C_kWh_K=r3(Cz), K_env_W_K=r1(Kenv * 1000), K_wall_W_K=r1(Kw * 1000), K_inf_W_K=r1(Kinf * 1000),
                neighbours_W_K=nb, Ta_july=r1(Tjul), internal_gain_kW=r3(gain), solar_july_kW=r3(Sjul), load_kW=r3(L),
                warming_rate_off_K_h=r2(L / Cz), cop_factor_july=r2(cf), cap_factor_july=r2(M.cap_factor(Tjul)),
                differential_K=dV, leaps=modes)


def b_party_wall(city="Riyadh"):
    """Typical floor (b), main envelope: temperature difference across apartment A's party walls and the effect of
    removing or tripling the party-wall coupling on A's energy (Proposition: coupled neighbours at a common level)."""
    out = {}
    try:
        M.set_scope("floor")
        A = [i for i in range(M.NZ) if M.SIDE[i] == "A"]; OUTS = [i for i in range(M.NZ) if M.SIDE[i] != "A"]
        K0 = M.Kc.copy(); pm = np.zeros_like(K0, bool)
        for i in A:
            for j in OUTS: pm[i, j] = pm[j, i] = True
        for c in CITIES:
            r = sim(c, MAIN, [M.CODE])[0]; base = r["meter_kwh"][0]
            ii, jj = np.nonzero(np.triu(pm) & (K0 > 0)); w = K0[ii, jj]
            dT = np.array([r["Vmean"][j] - r["Vmean"][i] for i, j in zip(ii, jj)])
            row = dict(mean_abs_dT_K=r2(np.average(np.abs(dT), weights=w)))
            for fct in (0.0, 3.0):
                M.Kc = np.where(pm, K0 * fct, K0); M.Krow = M.Kc.sum(1)
                row[f"x{fct:g}_pct"] = r1(-pct(sim(c, MAIN, [M.CODE])[0]["meter_kwh"][0], base))
            M.Kc = K0; M.Krow = K0.sum(1); out[c] = row
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def b_wet_neighbours():
    """What-if: can the rooms next to the baths keep the baths within the band?  (A) upsize their units to the largest
    catalogue model (never smaller than the unit already there); (B) additionally hold them at the bottom of the comfort band (22 degC)."""
    TMAX = max(float(k) for k in M._BLD["catalogue_units"])                    # largest catalogue unit [ton]
    out = {}; RM = M.REP_METER; apt = [i for i in range(M.NZ) if M.METER[i] == RM]
    wet = np.array(["bath" in r for r in M.ROOM])
    nbr = np.array([bool(M.COND[i]) and any(wet[j] and M.FLOOR_OF[j] == M.FLOOR_OF[i] and M.Kc[i, j] > 0 for j in range(M.NZ))
                    for i in range(M.NZ)])
    Q0, H0 = M.QC.copy(), M.QH.copy()
    for c in CITIES:
        res = {}
        for tag, up, off in (("base", False, 0.0), ("A_upsized", True, 0.0), ("B_upsized_22C", True, M.CURRENT[0] - M.CODE[0]),
                             ("C_upsized_Vmin", True, M.SAFE[0] - M.CODE[0])):
            if up: M.QC = np.where(nbr, np.maximum(Q0, TMAX * M.TON_KW), Q0); M.QH = M.QC * (M.Q_HEAT / M.Q_COOL)
            M.SP_OFFSET = np.where(nbr, off, 0.0)
            # keep each room's heating setpoint below its cooling setpoint (otherwise heating and cooling fight)
            hs_needed = M.CODE[0] + off - M.DEADBAND - M.CODE[1]
            M.HEAT_OFFSET = np.where(nbr, min(0.0, hs_needed), 0.0)
            try:
                res[tag] = sim(c, MAIN, [M.CODE])[0]
            finally:
                M.QC, M.QH, M.SP_OFFSET, M.HEAT_OFFSET = Q0, H0, 0.0, 0.0
        b = res["base"]
        rows = {M.ROOM[i]: {t: dict(T_july=r1(r["Vmean_m"][6][i]), T_max=r1(r["Vmax"][i])) for t, r in res.items()}
                for i in apt if wet[i]}
        out[c] = dict(neighbours=sorted({M.ROOM[i] for i in apt if nbr[i]}), baths=rows,
                      apartment_change_pct={t: r1(-pct(r["meter_kwh"][RM], b["meter_kwh"][RM])) for t, r in res.items()},
                      conditioned_max={t: r1(r["Vmax"][M.COND].max()) for t, r in res.items()},
                      neighbour_T_mean={t: r1(r["Vmean"][[i for i in apt if nbr[i]]].mean()) for t, r in res.items()},
                      neighbour_min={t: r1(min(r["Vmean_m"][mm][i] for i in apt if nbr[i] for mm in range(12))) for t, r in res.items()})
    return out


def b_passive_zone():
    """Passive-zone proposition: July heat budget of each bath of the representative apartment (two-node zones: the
    windows, infiltration and door act on the air node; the opaque walls, roof and partitions on the structure node).
    Heat enters from the outdoors and from the bath's own and solar gains and leaves through its partitions (to
    conditioned and to passive neighbours) and through its open door; the budget closes when the stored heat is
    negligible over the month."""
    out = {}; RM = M.REP_METER; apt = [i for i in range(M.NZ) if M.METER[i] == RM]
    for c in CITIES:
        M.RECORD_HOURLY = True
        try:
            r = sim(c, MAIN, [M.CODE])[0]
        finally:
            M.RECORD_HOURLY = False
        e = M.ENVELOPES[MAIN]
        Ta, moh = M.ambient(c)[:2]; jul = moh == 6; Taj = float(Ta[jul].mean())
        V = r["V_hourly"][jul].mean(0); W = r["W_hourly"][jul].mean(0)
        S_opq, S_win, S_skw = M.solar_drive(c, MAIN)
        GZ = M.glazing(); rows = {}
        for i in apt:
            if M.COND[i] or "bath" not in M.ROOM[i]: continue
            Kinf = M.ACH * M.AREA[i] * M.H_AIR * M._b["air_density"] * M._b["air_cp_kJkgK"] / 3600
            Awin = float((GZ[i] * M.A_EXT_O[i]).sum())
            K_win = e["U_WIN"] * Awin / 1000 + Kinf
            K_opq = e["U_WALL"] * (M.A_EXT[i] - Awin) / 1000 + (M.AREA[i] * e["U_ROOF"] / 1000 if M.is_roof[i] else 0.0)
            G = float(((M.GAINb + M.GAINd * np.maximum(np.sin((np.arange(24) - M._sch["gain_solar_phase_hr"]) / 24 * 2 * np.pi), 0)).mean())
                      * M.GAINF[i] * M.AREA[i] / M.A_REF)
            S = float((S_opq[jul, i] + S_win[jul, i] - S_skw[jul, i]).mean())
            nb = np.nonzero(M.Kc[i])[0]
            cond_out = float(sum(M.Kc[i, j] * (W[i] - W[j]) for j in nb if M.COND[j]))
            pass_out = float(sum(M.Kc[i, j] * (W[i] - W[j]) for j in nb if not M.COND[j]))
            door_out = float((r["door_q_m"][6] * M.DOORS[i]).sum())
            outdoor = K_win * (Taj - V[i]) + K_opq * (Taj - W[i])
            inflow = outdoor + G + S
            rows[M.ROOM[i]] = dict(T_july=r2(V[i]), T_max=r1(r["Vmax"][i]),
                                   in_outdoor_W=round(outdoor * 1000), in_gains_W=round((G + S) * 1000),
                                   out_walls_to_conditioned_W=round(cond_out * 1000), out_walls_to_passive_W=round(pass_out * 1000),
                                   out_door_W=round(door_out * 1000), closure_W=r1((inflow - cond_out - pass_out - door_out) * 1000))
        out[c] = dict(Ta_July=r1(Taj), baths=rows)
    return out


def b_inference():
    """Controller cost per control step (mode selection for every unit) at the three analysis steps."""
    out = {}
    try:
        for sc in M.SCOPES:
            M.set_scope(sc, REF_FLOORS); V = np.full(M.NZ, 23.0); t0 = time.perf_counter()
            for _ in range(20000):
                mag = np.where(M.COND, np.abs(V - cs), 0.0)
                mode = (mag > 0).astype(int) + (mag >= M._MODE[0]) + (mag >= M._MODE[1]); _ = M.FR[mode]
            out[sc] = dict(units=int(M.COND.sum()), inference_us=r1((time.perf_counter() - t0) / 20000 * 1e6))
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def b_cost_model():
    """Sensitivity to the cost model within energy-priced tariffs (the family Saudi households face): the same grid of
    schedules priced by (i) a flat linear price, (ii) the actual SEC two-tier tariff, (iii) the two tiers with the
    threshold lowered to 500 kWh/month, which the apartment meters exceed in summer. Best comfort-feasible scheduling saving and
    the extra cost of 2 K pre-peak pre-cooling under each (SBC-compliant complex)."""
    VAT = 1.15
    tariffs = {"linear": lambda E: 0.18 * E,
               "saudi_two_tier": lambda E: 0.18 * np.minimum(E, 6000) + 0.30 * np.maximum(E - 6000, 0),
               "two_tier_500": lambda E: 0.18 * np.minimum(E, 500) + 0.30 * np.maximum(E - 500, 0)}
    out = {}
    for c in CITIES:
        R = sim(c, MAIN, GRID); feas = adm(R)
        pk = [k for k, g in enumerate(GRID) if list(g[2:]) == [0, 2]][0]
        row = {}
        for name, fn in tariffs.items():
            bills = np.array([VAT * fn(r["meter_kwh_m"]).sum() for r in R])
            k = int(np.argmin(np.where(feas, bills, np.inf)))
            row[name] = dict(best_policy=list(GRID[k][2:]), saving_pct=r2(max(0.0, pct(bills[k], bills[0]))),
                             prepeak_extra_pct=r1(-pct(bills[pk], bills[0])))
        out[c] = row
    return out


def b_stock_equivalent():
    """External validation against the Saudi residential stock model (Krarti et al. 2020): the pre-code complex
    re-run with the stock apartment prototype's equipment efficiency (EER 8.5) and infiltration (0.8 ACH), so that
    the comparison with the reported regional EUIs tests the envelope and building model, not the equipment."""
    se = M._P["stock_equivalent"]; C0, A0, L0 = M.COOLc.copy(), M.ACH, M.COP_LAT
    cop_rated = se["EER"] / 3.412; s = cop_rated / (C0[3] * M.cop_factor(35.0))
    out = dict(EER=se["EER"], ACH=se["ACH"], cop_scale=r3(s))
    try:
        M.COOLc = C0 * s; M.ACH = se["ACH"]; M.COP_LAT = cop_rated
        for c in CITIES:
            out[c] = {e: round(sim(c, e, [M.CODE])[0]["kwh"] / M.FLOOR_AREA / FRAC) for e in ENVS}   # whole-building EUI (HVAC / 0.70), as the stock model reports
    finally:
        M.COOLc, M.ACH, M.COP_LAT = C0, A0, L0
    return out


def b_setpoint_frontier():
    """Highest cooling setpoint on the thermostat's grid (SP_STEP) whose cycle stays within the 24 degC ceiling (hot-side
    excursion no larger than at the reference setpoint), and the extra saving it brings over the reference setpoint."""
    out = {}; RM = M.REP_METER; sps = [round(x, 2) for x in np.arange(cs, M.COMFORT[1] + 1e-6, M.SP_STEP)]
    for env in ENVS:
        for c in CITIES:
            R = sim(c, env, [[v, hs, 0, 0] for v in sps])
            ok = [k for k, r in enumerate(R) if O.admissible(r)]; k = max(ok)
            out[f"{env}/{c}"] = dict(max_feasible_setpoint=sps[k], hot_excursion=round(R[k]["cviol_hot"]),
                                     next_setpoint=sps[k + 1] if k + 1 < len(sps) else None,
                                     next_hot_excursion=round(R[k + 1]["cviol_hot"]) if k + 1 < len(sps) else None,
                                     extra_bldg_pct=r1(pct(R[k]["bill"], R[0]["bill"])),
                                     extra_apt_pct=r1(pct(R[k]["meter_bill"][RM], R[0]["meter_bill"][RM])))
    return out


def b_landscape():
    """Cost landscape over (cooling setpoint, pre-peak depth), SBC-compliant Jeddah building, volume tariff."""
    vc = [round(x, 3) for x in np.arange(M.COMFORT[0], M.COMFORT[1] + 1e-6, M.SP_STEP)]   # thermostat steps (includes the reference)
    pk = [round(float(x), 3) for x in PPG]
    R = sim("Jeddah", MAIN, [[v, hs, 0, p] for p in pk for v in vc])
    bill = np.array([r["bill"] for r in R]).reshape(len(pk), len(vc))
    hot = np.array([r["cviol_hot"] for r in R]).reshape(len(pk), len(vc))
    feas = adm(R).reshape(len(pk), len(vc))
    j, i = np.unravel_index(np.where(feas, bill, np.inf).argmin(), bill.shape)
    return dict(setpoints=vc, prepeak=pk, bill=bill.round(1).tolist(), feasible=feas.tolist(), optimum=[vc[i], pk[j]],
                setpoint_axis_drop_pct=r1(pct(bill[0].min(), bill[0].max())),
                prepeak_axis_rise_pct=r1(-pct(bill[:, i].max(), bill[:, i].min())))


SUMMER, WINTER = (5, 6, 7, 8), (11, 0, 1)                  # Jun-Sep, Dec-Feb (0-based months)


def b_seasons_hc():
    """Summer and winter operation of the heat-pump units (SBC-compliant and pre-code complex at the code policy):
    monthly cooling / heating electricity, the time conditioned rooms spend in each mode (off, cooling 1-3,
    heating 1-3) by season, and the coldest conditioned and passive rooms in winter."""
    out = {}; M.TRACK_MODES = True
    try:
        for c in CITIES:
            for env in ENVS:
                r = sim(c, env, [M.CODE])[0]; mc = r["mode_counts_m"]
                share = lambda months: [r1(x) for x in mc[list(months)].sum(0) / mc[list(months)].sum() * 100]
                Vm = r["Vmean_m"]; wm = list(WINTER)
                Ta_, moh_ = M.ambient(c)[:2]
                out[f"{env}/{c}"] = dict(
                    heating_months=[k + 1 for k in range(12) if Ta_[moh_ == k].mean() < M.COMFORT[0]],
                    cool_kwh_m=[round(x) for x in r["kwh_cool_m"]], heat_kwh_m=[round(x) for x in r["kwh_heat_m"]],
                    cool_kwh=round(r["kwh_cool_m"].sum()), heat_kwh=round(r["kwh_heat_m"].sum()), latent_kwh=round(r["kwh_lat"]),
                    summer_modes_pct=share(SUMMER), winter_modes_pct=share(WINTER), year_modes_pct=share(range(12)),
                    cold_excursion_Ch=r2(r["cviol"] - r["cviol_hot"]), hot_excursion_Ch=r2(r["cviol_hot"]),
                    winter_cond_min_monthly_C=r2(Vm[wm][:, M.COND].min()), winter_passive_min_monthly_C=r2(Vm[wm][:, ~M.COND].min()),
                    summer_passive_max_monthly_C=r2(Vm[list(SUMMER)][:, ~M.COND].max()))
    finally:
        M.TRACK_MODES = False
    return out


def heat_months(city):
    """Months (1-12) with automatic heating under the seasonal changeover: mean air temperature below the band floor
    (of the weather M.ambient currently returns)."""
    Ta, moh = M.ambient(city)[:2]
    return [k + 1 for k in range(12) if (moh == k).any() and Ta[moh == k].mean() < M.COMFORT[0]]


def b_instances():
    """Normal and hard instances of the scheduling problem (whole complex). An instance is normal if every unit holds
    the ceiling with capacity to spare (peak hourly load below SAT of its rated capacity, no excursion above the
    ceiling) and hard otherwise. Instances: both envelopes and cities with the catalogue units in the TMYx year and in a
    heat-wave year (measured weather +3 K), and with units right-sized to each building's own peak load (TMYx), which
    leaves them no margin. For each: capacity margin, near-saturated units per storey, comfort excursion, and the
    exhaustive scheduling optimum."""
    out = {}; amb0 = M.ambient
    try:
        for sizing, dT in (("catalogue", 0.0), ("catalogue", 3.0), ("right_sized", 0.0)):
            def amb(city, rep=None, _d=dT):
                Ta, moh, Wa, Iv, ghi = amb0(city, rep); return Ta + _d, moh, Wa, Iv, ghi
            M.ambient = amb
            for env in ENVS:
                for c in CITIES:
                    with patched():
                        if sizing == "right_sized": M.right_size(c, env, margin=TIGHT_MARGIN)
                        R = sim(c, env, GRID); qc = M.QC.copy()
                    b = np.array([r["bill"] for r in R]); h = np.array([r["cviol_hot"] for r in R])
                    feas = adm(R); k = argbest(b, feas); r0 = R[0]; cz = np.where(M.COND)[0]
                    lr = np.zeros(M.NZ); lr[M.COND] = r0["q_hour_max"][M.COND] / qc[M.COND]
                    sat = [int(((lr >= SAT) & M.COND & (M.FLOOR_OF == f)).sum()) for f in range(M.FLOORS)]
                    key = f"{env}/{c}/+{dT:g}K" + ("/right_sized" if sizing == "right_sized" else "")
                    out[key] = dict(
                        hard=bool(sum(sat) > 0 or not O.admissible(r0)), peak_load_ratio=r2(lr[cz].max()), util_mean=r3(r0["util"][cz].mean()),
                        saturated_by_storey=sat, hot_excursion_Ch=round(r0["cviol_hot"]), warmest_room=r1(r0["Vmax"][cz].max()),
                        EUI=hvac_i(r0["kwh"]), best_policy=[float(x) for x in GRID[k][2:]] if k is not None else None,
                        sched_pct=r2(pct(b[k], b[0])) if k is not None else None, worst_precool_pct=r1(-pct(b.max(), b[0])), n_feasible=int(feas.sum()),
                        hot_Kh=r2(r0["cviol_hot"]), cold_Kh=r2(r0.get("sviol_cold", 0.0)), min_room_C=r2(r0["Vmin"][cz].min()),
                        n_rooms_below_floor=int((r0["Vmin"][cz] < M.SAFE[0] - 1e-9).sum()),
                        heating_months=heat_months(c))      # M.ambient is already shifted by dT
    finally:
        M.ambient = amb0
    return out


def b_train_eval():
    """Training and evaluation procedure. The policy is selected ('trained') on the 60 sampled days (5 per month) and
    then evaluated on all 365 days of the measured year, 305 of which were never seen during selection. Reported:
    the full-year optimum, the rank agreement of the 49 lattice policies, and the absolute / relative error of the
    sampled-year estimate of the annual energy and bill at the code policy."""
    out = {}; REP0, MS0 = M.REP, M.MSCALE.copy()
    for c in CITIES:
        Rs = sim(c, MAIN, GRID)
        try:
            M.REP = "all"; M.MSCALE = np.ones(12); Rf = sim(c, MAIN, GRID)
        finally:
            M.REP, M.MSCALE = REP0, MS0
        bs = np.array([r["bill"] for r in Rs]); bf = np.array([r["bill"] for r in Rf])
        feas = adm(Rf); kf = argbest(bf, feas)
        rs = np.argsort(np.argsort(bs)); rf = np.argsort(np.argsort(bf)); n = len(bs)
        rho = 1 - 6 * ((rs - rf) ** 2).sum() / (n * (n * n - 1))
        es, ef = Rs[0]["kwh"], Rf[0]["kwh"]
        out[c] = dict(train_days=60, eval_days=365, full_year_best_policy=[float(x) for x in GRID[kf][2:]],
                      full_year_sched_pct=r2(pct(bf[kf], bf[0])), spearman_rank=r3(rho),
                      kwh_sampled=round(es), kwh_full=round(ef), abs_err_kwh=round(es - ef), rel_err_pct=r2((es / ef - 1) * 100),
                      bill_abs_err_SAR=round(bs[0] - bf[0]), bill_rel_err_pct=r2((bs[0] / bf[0] - 1) * 100),
                      precool_cost_rel_err_pp=r2(np.abs((bs / bs[0] - 1) - (bf / bf[0] - 1)).max() * 100))
    return out


def b_complexity():
    """Calculation time. Wall-clock time of one annual evaluation (sampled year, 3-min steps) for one policy and for a
    batch of 25, at each analysis case; the evaluation counts of each search; and the per-step inference time."""
    out = {}
    try:
        for sc in M.SCOPES:
            M.set_scope(sc, REF_FLOORS); sim("Riyadh", MAIN, [M.CODE])          # warm the weather cache
            t = []
            for P in (1, len(GRID)):
                best = 1e9
                for _ in range(2):
                    t0 = time.perf_counter(); sim("Riyadh", MAIN, GRID[:P]); best = min(best, time.perf_counter() - t0)
                t.append(best)
            out[sc] = dict(zones=int(M.NZ), units=int(M.COND.sum()), storeys=int(M.FLOORS), doors=int(M.DOORS.shape[1]),
                           steps_per_year=int(len(M.ambient("Riyadh")[0]) * M.SUB), t_eval_1_s=r2(t[0]), t_eval_batch_s=r2(t[1]), batch_n=len(GRID),
                           t_per_policy_batched_s=r2(t[1] / len(GRID)))
    finally:
        M.set_scope("building", REF_FLOORS)
    out["evaluations"] = dict(grid=len(GRID))
    return out


def ramp_profile(depth, end=13, step=0.5):
    """(24,) setpoint offsets of a ramped pre-peak setback: the setpoint falls by `step` per hour and reaches -depth in
    the hour before `end`, then returns to the setpoint (e.g. 2 K: -0.5, -1, -1.5, -2 K at 09-13 h)."""
    off = np.zeros(24); n = int(round(depth / step))
    for k in range(n):
        off[end - n + k] = -step * (k + 1)
    return off


def b_precool_decomposition():
    """Why pre-cooling does not pay: for the SBC-compliant building (Case 3), each pre-cooling depth split into the change
    in heat removed and in mean cooling COP, with the default controller and with modes capped at low (a pre-cool that
    never escalates to the less efficient modes); and a ramped pre-peak setback (0.5 K per hour) under the default
    controller."""
    out = {}; M0 = M._MODE; d = SD
    pols = [[cs, hs, 0, 0]] + [[cs, hs, 0, x] for x in d] + [[cs, hs, x, 0] for x in d]
    try:
        for tag, mode in (("default", M0), ("low_only", (99.0, 99.0))):
            M._MODE = mode; M.ESC_KICK = tag == "default"
            for c in CITIES:
                R = sim(c, MAIN, pols); r0 = R[0]
                rows = [dict(night=p[2], prepeak=p[3], bill_pct=r2(-pct(r["bill"], r0["bill"])),
                             heat_pct=r2(-pct(r["cool_th"], r0["cool_th"])), cop=r3(r["cool_th"] / r["kwh_cool"]),
                             hot_Ch=round(r["cviol_hot"]), admissible=bool(O.admissible(r))) for p, r in zip(pols, R)]
                out[f"{tag}/{c}"] = dict(base_cop=r3(r0["cool_th"] / r0["kwh_cool"]), rows=rows[1:],
                                         best_pct=min(x["bill_pct"] for x in rows))
        M._MODE = M0
        for c in CITIES:                                 # ramped pre-peak setback: 0.5 K per hour, deepest at 12-13 h
            r0 = sim(c, MAIN, [M.CODE])[0]; rows = []
            for x in d:
                with patched(SP_HOURLY=ramp_profile(x)):
                    r = sim(c, MAIN, [M.CODE])[0]
                rows.append(dict(night=0.0, prepeak=x, bill_pct=r2(-pct(r["bill"], r0["bill"])), heat_pct=r2(-pct(r["cool_th"], r0["cool_th"])),
                                 cop=r3(r["cool_th"] / r["kwh_cool"]), hot_Ch=round(r["cviol_hot"]), admissible=bool(O.admissible(r))))
            out[f"ramp/{c}"] = dict(base_cop=r3(r0["cool_th"] / r0["kwh_cool"]), rows=rows, best_pct=min(x["bill_pct"] for x in rows))
    finally:
        M._MODE = M0; M.ESC_KICK = True
    return out


def _sens(variants, cities=None, pols=None, area=None):
    """Run SENS_POLS (or pols) on the SBC-compliant building for each variant {name: dict of model overrides or a
    callable(city) that prepares the model}; every variant runs in a fresh copy of the model state."""
    out = {}
    for name, kv in variants.items():
        row = {}
        for c in (cities or CITIES):
            with patched():
                if callable(kv): kv(c)
                else:
                    for k, v in kv.items(): setattr(M, k, v)
                try:
                    R = sim(c, MAIN, pols or SENS_POLS)
                finally:
                    if callable(kv): M._AMB.pop((c, M.REP), None)      # discard any weather a variant replaced
            row[c] = sens_row(R, area)
            row[c].update(starts_per_unit_h=r2(R[0]["starts_per_unit_h"]))
        out[name] = row
    return out


def b_robustness():
    """Robustness of the conclusions to the idealised inputs and equipment assumptions, SBC-compliant building
    (Case 3): interior doors closed; internal gains -30 % / +30 %; flat part-load COP (all modes at the full-load COP);
    the slab coupled to a constant annual-mean ground temperature without the ISO 13370 edge resistance. For each:
    HVAC intensity, the setpoint lever, the best comfort-feasible pre-cool (0.5-2 K in either window) and the cost of
    1 K and 2 K pre-peak and 2 K night pre-cooling. Also a full-year check of 0.5 and 1 K pre-peak pre-cooling."""
    Gb0, Gd0, C0 = M.GAINb, M.GAINd, M.COOLc.copy()
    variants = {"reference": {}, "doors_closed": dict(K_DOOR=M.K_DOOR * M.DOOR_IS_OPENING),
                "gains_minus30": dict(GAINb=Gb0 * 0.7, GAINd=Gd0 * 0.7), "gains_plus30": dict(GAINb=Gb0 * 1.3, GAINd=Gd0 * 1.3),
                "flat_partload_cop": dict(COOLc=np.array([np.nan, C0[3], C0[3], C0[3]])),
                "ground_constant": dict(GROUND="constant")}
    out = _sens(variants)
    with patched(REP="all", MSCALE=np.ones(12)):
        fy = [[cs, hs, 0, x] for x in (0.0, 0.5, 1.0)]
        R = sim("Riyadh", MAIN, fy); b = np.array([r["bill"] for r in R])
    out["full_year_riyadh_prepeak"] = {str(p[3]): r2(-pct(bb, b[0])) for p, bb in zip(fy, b)}
    return out


def b_passive_control():
    """Rooms without units (baths, maid room, corridor, hall) with and without the controller, SBC-compliant building
    (Case 3): (0) no controller -- every unit off all year; (1) the reference controller with the interior doors closed;
    (2) the reference controller, doors open (the study case); (3) the controller at current practice (22 degC); (4) a
    unit in every bath. For each group of passive zones: annual and July mean, peak, time and degree-hours outside the band."""
    grp_of = {}
    for i in range(M.NZ):
        if M.COND[i] or "shaft" in M.ROOM[i] or M.ROOM[i] == "balcony": continue   # shafts and the enclosed balcony are not rooms
        grp_of[i] = ("baths_exterior" if M.A_EXT[i] > 0 else "baths_interior") if "bath" in M.ROOM[i] else "other_passive"
    groups = {g: [i for i, k in grp_of.items() if k == g] for g in ("baths_exterior", "baths_interior", "other_passive")}
    cz = np.where(M.COND)[0]
    t_all = np.array([('bath' in n) for n in M.ROOM])
    out = {}
    for c in CITIES:
        runs = {}
        runs["no_controller"] = sim(c, MAIN, [[99.0, -50.0, 0, 0]])[0]
        with patched(K_DOOR=M.K_DOOR * M.DOOR_IS_OPENING):
            runs["controller_doors_closed"] = sim(c, MAIN, [M.CODE])[0]
        R = sim(c, MAIN, [M.CODE, M.CURRENT])
        runs["controller"] = R[0]; runs["controller_current_practice"] = R[1]
        with patched():
            M.COND = M.COND | t_all; M.QC = np.where(t_all, M.TON_KW, M.QC); M.QH = M.QC * (M.Q_HEAT / M.Q_COOL)
            M.LATW = np.bincount(M.METER, M.COND * M.AREA / M.A_REF, M.NMET)
            M.CYCLING_CD = np.where(t_all, M._pl["cycling_Cd"]["inverter"], M.CYCLING_CD)
            runs["unit_in_every_bath"] = sim(c, MAIN, [M.CODE])[0]
        ref = runs["controller"]["kwh"]; row = {}
        for tag, r in runs.items():
            g = {}
            for name, ix in groups.items():
                g[name] = dict(n=len(ix), T_mean=r1(r["Vmean"][ix].mean()), T_july=r1(r["Vmean_m"][6][ix].mean()),
                               T_jan=r1(r["Vmean_m"][0][ix].mean()), T_max=r1(r["Vmax"][ix].max()),
                               T_min_month=r1(min(r["Vmean_m"][k][ix].min() for k in range(12))),
                               hot_pct_time=r1(r["zone_hot_frac"][ix].mean() * 100), cold_pct_time=r1(r["zone_cold_frac"][ix].mean() * 100),
                               mean_excess_K=r1(r["zone_hot_Kh"][ix].sum() / max(r["zone_hot_frac"][ix].sum() * 8760, 1e-9)),
                               hot_Kh=round(float(r["zone_hot_Kh"][ix].mean())), cold_Kh=round(float(r["zone_cold_Kh"][ix].mean())))
            row[tag] = dict(groups=g, conditioned_T_max=r1(r["Vmax"][cz].max()),
                            energy_change_pct=r1(-pct(r["kwh"], ref)) if tag != "no_controller" else None)
        out[c] = dict(scenarios=row)
    return out


def b_seasonal_levers():
    """Savings of the two levers by season for the three cases and both cities (SBC-compliant building unless stated):
    setpoint (current practice -> reference) and envelope (pre-code -> SBC at the reference setpoint), on the HVAC
    energy of the case (sensible + latent + standby). Summer = Jun-Sep, winter = Dec-Feb. Under the tier-1 volume tariff
    the bill saving equals the energy saving."""
    out = {}
    try:
        for c in CITIES:
            row = {}
            for sc in M.SCOPES:
                M.set_scope(sc, REF_FLOORS)
                cur, ref = sim(c, MAIN, [M.CURRENT, M.CODE]); pre = sim(c, "noncompliant", [M.CODE])[0]
                def sav(new, old, months=None):
                    a = old["kwh_m"] if months is None else old["kwh_m"][list(months)]
                    b = new["kwh_m"] if months is None else new["kwh_m"][list(months)]
                    return r1(pct(float(np.sum(b)), float(np.sum(a))))
                d = {}
                for tag, mm in (("annual", None), ("summer", SUMMER), ("winter", WINTER)):
                    d[tag] = dict(setpoint_pct=sav(ref, cur, mm), envelope_pct=sav(ref, pre, mm),
                                  kwh_reference=round(float(np.sum(ref["kwh_m"] if mm is None else ref["kwh_m"][list(mm)]))))
                row[sc] = d
            out[c] = row
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def _operative(r, city, env):
    """Hourly operative temperature of every zone, (air + mean radiant)/2, from the recorded air and structure
    temperatures. Inner-surface temperatures: exterior opaque walls and the roof from the sol-air temperature (absorbed
    solar minus the long-wave loss to the sky) through their U-value and an internal surface coefficient of 7.7 W/m2K
    (ISO 6946); windows from the air temperature (absorbed solar neglected); the ground slab from the monthly ground
    temperature; other surfaces at the structure node."""
    e = M.ENVELOPES[env]; Ta, moh, Wa, Iv, ghi = M.ambient(city); dR = M.sky_excess(city); Tg = M.ground_temperature(city)[moh]; hi = 7.7
    sk = M._SKY
    V, Wm = r["V_hourly"], r["W_hourly"]; A = M.AREA; A_int = 2 * A + 4 * np.sqrt(A) * M.H_AIR
    Aw = M.A_EXT_O * (1 - M.glazing()); Ag = M.A_EXT_O * M.glazing()               # (NZ, 4) opaque / glazed
    Tsa = Ta[:, None] + (M.ALPHA * Iv - sk["F_sky_vertical"] * dR[:, None]) / M.H_OUT   # (H, 4) sol-air per orientation
    num = np.zeros_like(V); den = np.zeros(M.NZ)
    for o in range(Aw.shape[1]):
        num += Aw[:, o] * (V + e["U_WALL"] * (Tsa[:, o:o + 1] - V) / hi) + Ag[:, o] * (V + e["U_WIN"] * (Ta[:, None] - V) / hi)
        den += Aw[:, o] + Ag[:, o]
    roof = M.is_roof * A; Tsr = (Ta + (M.ALPHA * ghi - sk["F_sky_roof"] * dR) / M.H_OUT)[:, None]
    num += roof * (V + e["U_ROOF"] * (Tsr - V) / hi); den += roof
    grd = M.is_grnd * A; num += grd * (V + M.U_GRND_ISO * (Tg[:, None] - V) / hi); den += grd
    rest = np.maximum(A_int - den, 0); num += rest * Wm; den += rest
    return (V + num / den) / 2


def b_robustness_extra():
    """Further robustness of the scheduling result and the setpoint lever (SBC-compliant building, Case 3):
    (i) one node per zone instead of two (all capacitance on the air node); (ii) two-node zones with 15 % of the
    capacitance on the air node instead of 5 %; (iii) a latent COP 30 % lower whenever the units run off or in the
    lowest mode (a warm coil dehumidifies less); and (iv) the operative temperature of the conditioned rooms at the
    reference setpoint and at current practice."""
    zm = M.ZONE_MODEL
    variants = {"one_node": dict(TWO_NODE=None), "one_node_cop_indoor_0": dict(TWO_NODE=None, GAMMA_IN=0.0),
                "air_fraction_0.02": dict(TWO_NODE=dict(zm, air_fraction=0.02)),
                "air_fraction_0.15": dict(TWO_NODE=dict(zm, air_fraction=0.15)), "air_fraction_0.30": dict(TWO_NODE=dict(zm, air_fraction=0.30)),
                "k_am_x0.5": dict(TWO_NODE=dict(zm, k_am_W_m2K=zm["k_am_W_m2K"] * 0.5)), "k_am_x2": dict(TWO_NODE=dict(zm, k_am_W_m2K=zm["k_am_W_m2K"] * 2)),
                "latent_lowmode_0.7": dict(LAT_LOWMODE_FACTOR=0.7)}
    out = _sens(variants)
    cz = np.where(M.COND)[0]; top = [i for i in cz if M.is_roof[i]]; op = {}
    with patched(RECORD_HOURLY=True):
        for c in CITIES:
            for pol_tag, pol in (("reference", M.CODE), ("current_practice", M.CURRENT)):
                r = sim(c, MAIN, [pol])[0]; To_all = _operative(r, c, MAIN); To = To_all[:, cz]; Va = r["V_hourly"][:, cz]
                op[f"{c}/{pol_tag}"] = dict(max_operative_C=r1(To.max()), max_air_C=r1(Va.max()),
                                            mean_offset_K=r2((To - Va).mean()), top_floor_max_operative_C=r1(To_all[:, top].max()),
                                            pct_hours_operative_above_24=r1((To > 24.0).mean() * 100),
                                            pct_hours_operative_above_25=r1((To > 25.0).mean() * 100))
    out["operative"] = op
    return out


def b_tenant_savings():
    """What each renter pays and saves (whole building, Case 3): the annual bill of every apartment meter at current
    practice (22 degC) and at the reference setpoint, and the bill the same renter would pay in the pre-code building at
    the reference setpoint."""
    out = {}
    for c in CITIES:
        cur, ref = sim(c, MAIN, [M.CURRENT, M.CODE])
        pre = sim(c, "noncompliant", [M.CODE])[0]
        rows = {}
        for f in range(M.FLOORS):
            for sd in ("A", "B"):
                m = M.apartment_meter(f, sd)
                b0, b1, bp = (float(r["meter_bill"][m]) for r in (cur, ref, pre))
                rows[f"{f + 1}{sd}"] = dict(bill_current=round(b0), bill_reference=round(b1), bill_precode=round(bp),
                                           setpoint_pct=r1(pct(b1, b0)), setpoint_SAR=round(b0 - b1),
                                           envelope_pct=r1(pct(b1, bp)), envelope_SAR=round(bp - b1))
        out[c] = dict(apartments=rows)
    return out


def b_ablation():
    """Ablation of the selected policy (Case 3, SBC-compliant building): theta* = reference setpoint (the highest the
    thermostat's 0.5 K steps allow within the ceiling), units in the lowest mode that holds the room, no pre-cooling,
    SBC envelope. Each variant changes exactly one component and keeps the rest (the heating setpoint stays at the
    reference value throughout); deterministic annual simulation, so one run per variant."""
    out = {}; M0 = M._MODE
    for c in CITIES:
        full = list(M.CODE)
        var = [("full", MAIN, full, M0), ("current_practice", MAIN, [M.CURRENT[0], hs, 0, 0], M0),
               ("highest_mode", MAIN, full, (1e-9, 1e-9)), ("precode_envelope", "noncompliant", full, M0),
               ("night_precool_2K", MAIN, [cs, hs, 2.0, 0], M0), ("prepeak_precool_2K", MAIN, [cs, hs, 0, 2.0], M0)]
        rows = {}
        try:
            for tag, env, pol, mode in var:
                M._MODE = mode; r = sim(c, env, [pol])[0]
                rows[tag] = dict(bill=round(r["bill"]), kwh=round(r["kwh"]), hot_Ch=round(r["cviol_hot"]),
                                 cold_Kh=r2(r.get("sviol_cold", 0.0)), admissible=bool(O.admissible(r)))
        finally:
            M._MODE = M0
        b0 = rows["full"]["bill"]
        for v in rows.values():
            v["delta_pct"] = r1((v["bill"] / b0 - 1) * 100)
        out[c] = dict(setpoint=cs, rows=rows)
    return out


def b_tou():
    """Contrast case outside the Saudi residential tariff: an illustrative time-of-use (TOU) tariff that prices the
    cooling-month energy used in the peak window M.TOU_PEAK_HOURS at r times the off-peak price (0.18 SAR/kWh, VAT
    added). The same lattice of pre-cooling policies (SBC-compliant building, Case 3) is priced for r = 1 (flat), 2, 3, 5,
    with the two-node zones of the base case and with one node per zone. Shows whether the model rewards re-timing when
    the tariff does, i.e. that the volume-tariff result is a property of the tariff, not the model."""
    VAT, p0 = 1.15, 0.18; ratios = [1, 2, 3, 5]; out = {}
    pk = [k for k, g in enumerate(GRID) if list(g[2:]) == [0, 2]][0]
    for tag, two in (("two_node", TWO()), ("one_node", None)):
        with patched(TWO_NODE=two):
            for c in CITIES:
                R = sim(c, MAIN, GRID); feas = adm(R)
                tot = np.array([r["meter_kwh_m"].sum() for r in R]); peak = np.array([r["meter_kwh_peak_m"].sum() for r in R])
                rows = {}
                for rt in ratios:
                    bills = VAT * p0 * ((tot - peak) + rt * peak)
                    k = int(np.argmin(np.where(feas, bills, np.inf)))
                    rows[str(rt)] = dict(best_policy=list(GRID[k][2:]), saving_pct=r2(max(0.0, pct(bills[k], bills[0]))),
                                         saving_SAR=round(float(bills[0] - bills[k])), prepeak2_pct=r1(-pct(bills[pk], bills[0])),
                                         base_bill=round(float(bills[0])))
                out[f"{tag}/{c}"] = dict(peak_share_pct=r1(100 * peak[0] / tot[0]), peak_cut_prepeak2_pct=r1(pct(peak[pk], peak[0])),
                                         energy_prepeak2_pct=r1(-pct(tot[pk], tot[0])), n_infeasible=int((~feas).sum()), rows=rows)
    return out


def _weather_override(city, fn):
    """Replace the cached weather of `city` (current REP) by fn(Ta, moh) -> Ta'; humidity ratio, solar and sky unchanged."""
    key = (city, M.REP); M.ambient(city); Ta, moh, Wa, Iv, ghi = M._AMB[key]
    M._AMB[key] = (fn(Ta, moh), moh, Wa, Iv, ghi); return (Ta, moh, Wa, Iv, ghi)


def _diurnal(k, shift=0.0):
    """Amplify the daily swing of the air temperature about each day's mean by k and add `shift` [K]."""
    def fn(Ta, moh):
        d = Ta.reshape(-1, 24); mu = d.mean(1, keepdims=True); return ((mu + k * (d - mu)) + shift).ravel()
    return fn


RS_SEARCH = {}; ADM = {}


def rs_margin(city):
    """First of the sizing margins of experiments.json (right_sizing.margins, tried in increasing order) at which units
    sized to each zone's peak hourly load keep the hot-side excursion within the comfort tolerance (the catalogue
    units have none); the smallest sufficient margin lies between it and the previous one tried; cached."""
    if city not in RS_SEARCH:
        rows = {}
        for mg in RS["margins"]:
            with patched():
                M.right_size(city, MAIN, margin=mg); r = sim(city, MAIN, [M.CODE])[0]
            rows[str(mg)] = round(float(r["cviol_hot"]), 1)
            ADM[(city, mg)] = O.admissible(r)
            if ADM[(city, mg)]: break
        RS_SEARCH[city] = rows
    ok = [float(k) for k in RS_SEARCH[city] if ADM[(city, float(k))]]
    return min(ok) if ok else float(RS["margins"][-1])


def b_revision():
    """Sensitivity of the setpoint lever and the scheduling result to the equipment, controller and inputs
    (SBC-compliant building, Case 3). Equipment: no cycling loss (Cd = 0); Cd = 0.25 for every unit; the 3-ton units
    as inverter units; no standby power; no capacity derating; the indoor fan running continuously; units right-sized
    to the peak hourly load. Controller: a fine thermostat (0.1 K differential). Envelope and air: wall U-value +20 %
    (thermal bridges); opaque solar absorptance 0.5 and 0.9 (0.7 +- 0.2); infiltration 0.2 and 0.8 ACH; a door schedule (bath doors closed, bedroom doors closed
    23:00-07:00). Weather: a 30 % wider daily swing, and a heat wave (+3 K) with that swing (humidity ratio held
    constant). Also: internal latent gains (ASHRAE residential); the comfort frontier of the fine thermostat; a
    diagnostic humidity balance; and the numerical value of the pre-cooling bound of Theorem 3."""
    out = {}
    names = [M.ROOM[i] for i in range(M.NZ)]
    zone_a = [names[int(np.where(M.DOORS[:, k] > 0)[0][0])] for k in range(M.DOORS.shape[1])]
    zone_b = [names[int(np.where(M.DOORS[:, k] < 0)[0][0])] for k in range(M.DOORS.shape[1])]
    bath = np.array(["bath" in a or "bath" in b for a, b in zip(zone_a, zone_b)])
    bed = np.array([any(w in a or w in b for w in ("bedroom", "guest")) for a, b in zip(zone_a, zone_b)])
    KD0 = M.K_DOOR.copy(); kd_day = np.where(bath, 0.0, KD0); kd_night = np.where(bath | bed, 0.0, KD0)
    inv = M._pl["cycling_Cd"]["inverter"]
    def env_wall(c):
        M.ENVELOPES[MAIN]["U_WALL"] *= EXP["thermal_bridge_wall_factor"]
    def three_inv(c):
        M.ONOFF_ZONES = np.zeros(M.NZ, bool); M.CYCLING_CD = np.where(M.COND, inv, 0.0)
    def rsz(c):
        M.right_size(c, MAIN, margin=rs_margin(c))
    def rsz_fixed(c):
        M.right_size(c, MAIN, margin=rs_margin(c), keep_fixed=True)
    def rsz_tight(c):
        M.right_size(c, MAIN, margin=TIGHT_MARGIN)
    def wx(k, sh):
        def f(c): _weather_override(c, _diurnal(k, sh))
        return f
    variants = {"reference": {}, "cycling_cd0": dict(CYCLING_CD=np.zeros(M.NZ)), "cycling_cd0.25": dict(CYCLING_CD=np.where(M.COND, 0.25, 0.0)),
                "three_ton_inverter": three_inv, "no_standby": dict(STANDBY_KW=0.0), "no_capacity_derating": dict(CAP_DERATE=False),
                "fan_continuous": dict(FAN_CONT_KW=EXP["fan_continuous_kW"]), "right_sized": rsz, "right_sized_keep_fixed": rsz_fixed,
                "right_sized_tight": rsz_tight,
                "fine_thermostat": dict(HYST=0.05), "no_restart_delay": dict(MIN_OFF_MIN=0),
                "escalation_0.2_0.6": dict(_MODE=(0.2, 0.6)), "escalation_0.8_2.0": dict(_MODE=(0.8, 2.0)),
                "low_mode_only": dict(_MODE=(99.0, 99.0), ESC_KICK=False), "no_escalation_kick": dict(ESC_KICK=False),
                "cop_indoor_0": dict(GAMMA_IN=0.0), "cop_indoor_0.03": dict(GAMMA_IN=0.03),
                "wall_U_plus20": env_wall, "absorptance_0.5": dict(ALPHA=0.5), "absorptance_0.9": dict(ALPHA=0.9),
                "ach_0.2": dict(ACH=0.2), "ach_0.8": dict(ACH=0.8),
                "door_schedule": dict(K_DOOR=kd_day, K_DOOR_NIGHT=kd_night),
                "diurnal_x1.3": wx(1.3, 0.0), "heatwave_plus3_diurnal_x1.3": wx(1.3, 3.0)}
    out["variants"] = _sens(variants)
    # right-sized units: the sizes chosen
    rs = {}; cat = float(M.QC[M.COND].sum() / M.TON_KW)
    for c in CITIES:
        with patched():
            M.right_size(c, MAIN, margin=rs_margin(c)); t = M.QC[M.COND] / M.TON_KW
            rs[c] = dict(margin=rs_margin(c), margin_search=RS_SEARCH[c], ton_min=r2(t.min()), ton_max=r2(t.max()),
                         ton_total=r1(t.sum()), ton_total_catalogue=r1(cat))
    out["right_sizing"] = rs
    # internal latent gains: energy only (schedule-independent)
    il = {}
    for c in CITIES:
        r = sim(c, MAIN, [M.CODE])[0]
        Ta_, moh_, Wa_ = M.ambient(c)[:3]; a_ = M.APT_AREA; g = (20 + 0.22 * a_ + 12 * 5) / 1000   # kW per apartment
        m_inf = M.AIR_MASS * (M.AREA[M.COND & (M.METER < M.NMET - 1)].sum() / M.A_REF) / M.FLOORS / 2 * M.ACH / 3600.0   # kg/s per apartment
        inf = m_inf * (Wa_ - M.W_IN) * M.HFG                                        # kW latent by infiltration (negative: drying)
        add_h = (np.clip(inf + g, 0, None) - np.clip(inf, 0, None)) / M.COP_LAT       # extra latent electricity per apartment [kW]
        ann = float((add_h * M.MSCALE[moh_]).sum()) * 2 * M.FLOORS
        il[c] = dict(kw_per_apartment=r3(g), kwh_added=round(ann), energy_pct=r1(100 * ann / r["kwh"]))
    out["internal_latent"] = il
    # humidity diagnostic
    mo = {}
    for c in CITIES:
        for shr in (0.75, 0.65):
            with patched(MOISTURE=dict(SHR=shr, internal=True)):
                r = sim(c, MAIN, [M.CODE])[0]
            cz = M.COND
            mo[f"{c}/SHR{shr}"] = dict(rh60_pct_hours=r1(100 * r["rh60_frac"][cz].mean()), rh60_max_zone_pct=r1(100 * r["rh60_frac"][cz].max()),
                                       coil_share_pct=r1(100 * r["latent_coil_share"]))
    out["moisture"] = mo
    # comfort frontier of a fine thermostat (0.05 K setpoint steps, 0.1 K differential)
    ft = {}; sps = [round(x, 2) for x in np.arange(cs, M.COMFORT[1] + 1e-6, 0.05)]
    for c in CITIES:
        with patched(HYST=0.05):
            R = sim(c, MAIN, [[v, hs, 0, 0] for v in sps])
        k = max(i for i, r in enumerate(R) if O.admissible(r))
        ft[c] = dict(frontier_setpoint=sps[k], extra_pct=r1(pct(R[k]["bill"], R[0]["bill"])),
                     reference_vs_base_pct=r1(pct(R[0]["bill"], sim(c, MAIN, [M.CODE])[0]["bill"])))
    out["fine_thermostat_frontier"] = ft
    # numerical value of the pre-cooling bound (Theorem 3), July, building, mode 1, extra heat neglected
    b3 = {}; Csum = float(M.CZ[M.COND].sum())
    for c in CITIES:
        Ta, moh = M.ambient(c)[:2]; jul = moh == 6; prof = Ta[jul].reshape(-1, 24).mean(0)
        r = sim(c, MAIN, [M.CODE])[0]; e_day = float(r["kwh_m"][6]) / 31
        eta = lambda T, V: 1.0 / (M.COOLc[1] * M.cop_factor(T) * M.cop_indoor(V))
        Tp = prof[13:17].mean(); win = dict(night=np.r_[prof[22:], prof[:6]].mean(), dawn=prof[4:8].mean(), prepeak=prof[10:13].mean())
        for w, Tw in win.items():
            for dl in (1.0, 2.0):
                gain = Csum * dl * (eta(Tp, M.COMFORT[1]) - eta(Tw, M.COMFORT[1] - dl))
                b3[f"{c}/{w}/{dl}K"] = dict(COP_w=r2(1 / eta(Tw, M.COMFORT[1] - dl)), COP_p=r2(1 / eta(Tp, M.COMFORT[1])), bound_kwh_day=r1(gain),
                                            bound_pct_of_july_day=r1(100 * gain / e_day))
    out["theorem3_bound"] = dict(C_conditioned_kWh_per_K=r1(Csum), rows=b3)
    return out


def b_safe_floor():
    """Check of constraint (C3), the 20 degC floor of the safe set, over the whole lattice (Case 3, both envelopes and
    cities): the policies whose rooms fall below the floor (the setpoint is capped at 20.5 degC, but with heating off in
    the cooling months a room pre-cooled at night can keep drifting on a cool night), their largest violation, and
    whether each costs more than no pre-cooling, so that the optimum is the same with (C3) enforced."""
    out = {}
    for env in ENVS:
        for c in CITIES:
            R = sim(c, env, GRID); b0 = R[0]["bill"]
            v = [(GRID[k][2:], r2(r["sviol_cold"]), r2(float(r["Vmin"][M.COND].min())), r["bill"] > b0) for k, r in enumerate(R) if r["sviol_cold"] > 0]
            out[f"{env}/{c}"] = dict(n_violating=len(v), max_violation_Kh=max([x[1] for x in v], default=0.0),
                                     min_room_C=min([x[2] for x in v], default=None), all_costlier=all(x[3] for x in v),
                                     violating_policies=[x[0] for x in v])
    return out


def b_precool_fullyear():
    """The pre-cooling lattice (both windows, 0-3 K in 0.5 K steps) on all 365 days of the TMYx year, whole building
    (Case 3), both cities and envelopes, instead of the 60 sampled days: best comfort-feasible saving and the 1 K and
    2 K pre-peak penalties."""
    out = {}
    with patched(REP="all", MSCALE=np.ones(12)):
        for env in ENVS:
            for c in CITIES:
                R = sim(c, env, GRID); b = np.array([r["bill"] for r in R])
                feas = adm(R); k = argbest(b, feas)
                i1 = GRID.index([cs, hs, 0, 1]); i2 = GRID.index([cs, hs, 0, 2])
                out[f"{env}/{c}"] = dict(best_policy=None if k is None else GRID[k][2:],
                                         saving_pct=None if k is None else r3(pct(b[k], b[0])), bill=round(b[0]),
                                         prepeak1_pct=r2(-pct(b[i1], b[0])), prepeak2_pct=r2(-pct(b[i2], b[0])),
                                         min_increase_pct=r2(min(-pct(bb, b[0]) for bb in b[1:])),
                                         n_admissible=int(feas.sum()), reference_admissible=bool(feas[0]),
                                         hot_Kh=r2(R[0]["cviol_hot"]), cold_Kh=r2(R[0].get("sviol_cold", 0.0)),
                                         max_room_C=r2(float(np.max(R[0]["Vmax"][M.COND]))))
    return out


def b_dawn_setback():
    """The pre-specified dawn setback (experiments.json: 2 K at 03:00, returned to the setpoint in 0.5 K thermostat steps
    by 09:00, in the cooling months), timed like the perfect-foresight schedule, under the realistic controller and
    equipment, on all 365 days; Cases 1 and 3, SBC-compliant and pre-code, both cities. Saving against the reference."""
    ds = EXP["dawn_setback"]; prof = M.staircase(ds["depth_K"], ds["start_h"], ds["end_h"]); out = dict(profile_K=[r2(x) for x in prof])
    try:
        for sc in ("apartment", "building"):
            M.set_scope(sc, REF_FLOORS)
            with patched(REP="all", MSCALE=np.ones(12)):
                for env in ENVS:
                    for c in CITIES:
                        base = sim(c, env, [M.CODE])[0]
                        with patched(SP_HOURLY=prof):
                            r = sim(c, env, [M.CODE])[0]
                        out[f"{sc}/{env}/{c}"] = dict(saving_pct=r2(pct(r["bill"], base["bill"])), hot_Ch=round(r["cviol_hot"]),
                                                      base_hot_Ch=round(base["cviol_hot"]), bill=round(base["bill"]))
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


LB_SCOPES = (("apartment", [(m, "mean") for m in (4, 5, 6, 7, 8)] + [(6, "hottest")]),
             ("floor", [(6, "mean"), (6, "hottest"), (7, "mean")]),
             ("top_floor", [(6, "mean"), (6, "hottest"), (7, "mean")]))


def b_lower_bound(scopes=None):
    """Perfect-foresight benchmark (experiments/optimiser/lower_bound.py): linear program over every unit's heat
    removal at every 15-min step, against the same idealised model holding the ceiling without pre-cooling. Rooms may
    be pre-cooled to 22 degC (the band floor) or 20 degC (the floor of the safe set). With a COP of the outdoor air only
    (gamma = 0) the gap is an upper bound on the saving of any schedule of the idealised model at any gamma >= 0; with
    the engine's indoor COP factor (gamma = model.GAMMA_IN) sequential linear programming from two starts gives the best
    valid schedule found, an achievable saving. Case 1 (one apartment): the mean day of May-Sep, the hottest July day and
    the 31 consecutive days of July as one horizon (bound and search, solved with Clarabel, which is several times faster
    than HiGHS's interior point on this long program); Case 2 (the typical floor) and the
    top floor under the roof: the mean and hottest July day and the mean August day. Resumable: one cache file per scope."""
    from experiments.optimiser import lower_bound as LB
    out = {}
    try:
        for sc, days in LB_SCOPES:
            if scopes is not None and sc not in scopes:
                continue
            ck = ROOT / "results" / "cache" / f"lower_bound_{sc}.json"; ck.parent.mkdir(parents=True, exist_ok=True)
            cache = json.loads(ck.read_text()) if ck.exists() else {}
            def solve_ck(key, **kw):
                if key not in cache:
                    r = LB.solve(**kw)
                    cache[key] = {k: (v if not isinstance(v, (np.floating, np.integer)) else float(v)) for k, v in r.items()}
                    ck.write_text(json.dumps(cache, default=float))
                return cache[key]
            M.set_scope(sc, REF_FLOORS)
            for c in CITIES:
                for vm in (22.0, 20.0):
                    for g in (M.GAMMA_IN, 0.0):
                        rows = {}
                        for m, kind in days:
                            r = solve_ck(f"{sc}/{c}/{vm}/{g}/{m}/{kind}", city=c, env=MAIN, month=m, kind=kind, vmin=vm, gamma=g)
                            rows[f"{m + 1}/{kind}"] = dict(E_hold=r2(r["E_hold"]), E_lp=r2(r["E_lp"]), gap_pct=r2(r["gap_pct"]),
                                                           precool_K_h=r1(r["lp_precool_K_h"]), high_mode_share=r3(r["high_mode_share"]),
                                                           n_solves=r["n_solves"])
                        mean_rows = [(int(k.split("/")[0]) - 1, v) for k, v in rows.items() if k.endswith("mean")]
                        w = np.array([M.DIM[m] for m, _ in mean_rows]); eh = np.array([v["E_hold"] for _, v in mean_rows]); el = np.array([v["E_lp"] for _, v in mean_rows])
                        out[f"{sc}/{c}/vmin{vm}/gamma{g}"] = dict(days=rows, season_gap_pct=r2(100 * (w @ (eh - el)) / (w @ eh)))
                if sc == "apartment":
                    r = solve_ck(f"{sc}/{c}/chained_slp", city=c, env=MAIN, month=6, days=range(31), vmin=22.0, gamma=M.GAMMA_IN, solver="clarabel")
                    rb = solve_ck(f"{sc}/{c}/chained_g0", city=c, env=MAIN, month=6, days=range(31), vmin=22.0, gamma=0.0, solver="clarabel")
                    out[f"apartment/{c}/july_chained"] = dict(E_hold=r2(r["E_hold"]), E_lp=r2(r["E_lp"]), gap_pct=r2(r["gap_pct"]),
                                                              bound_gap_pct=r2(rb["gap_pct"]), n_solves=r["n_solves"], V_profile=r["V_profile"])
                if sc == "apartment" and c == "Riyadh":
                    r = solve_ck(f"{sc}/{c}/20.0/{M.GAMMA_IN}/6/mean", city=c, env=MAIN, month=6, kind="mean", vmin=20.0, gamma=M.GAMMA_IN)
                    out["profile_apartment_Riyadh_july"] = dict(V_profile=r["V_profile"], q_profile=r["q_profile"])
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def b_lower_bound_building():
    """Perfect-foresight bound for the whole building (Case 3, all storeys coupled, roof and ground): gamma = 0, the
    rigorous upper bound on the saving of any schedule of the idealised model, at the same 15-min steps as the other
    cases, for the mean and hottest July day and the mean August day, rooms down to 22 or 20 degC. The 264-node program is
    solved with Clarabel (HiGHS's interior point is very slow on it); the search at gamma = 0.02 (up to 19 solves per day)
    is not run for Case 3. Resumable (results/cache/lower_bound_building.json)."""
    from experiments.optimiser import lower_bound as LB
    out = {}; days = [(6, "mean"), (6, "hottest"), (7, "mean")]
    ck = ROOT / "results" / "cache" / "lower_bound_building.json"; ck.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(ck.read_text()) if ck.exists() else {}
    try:
        M.set_scope("building", REF_FLOORS)
        for c in CITIES:
            for vm in (22.0, 20.0):
                rows = {}
                for m, kind in days:
                    key = f"building/{c}/{vm}/0.0/{m}/{kind}"
                    if key not in cache:
                        r = LB.solve(c, MAIN, month=m, kind=kind, vmin=vm, gamma=0.0, solver="clarabel")
                        cache[key] = {k: (v if not isinstance(v, (np.floating, np.integer)) else float(v)) for k, v in r.items()}
                        ck.write_text(json.dumps(cache, default=float))
                    r = cache[key]
                    rows[f"{m + 1}/{kind}"] = dict(E_hold=r2(r["E_hold"]), E_lp=r2(r["E_lp"]), gap_pct=r2(r["gap_pct"]),
                                                   precool_K_h=r1(r["lp_precool_K_h"]), t_solve_s=r1(r["t_solve_s"]))
                out[f"building/{c}/vmin{vm}/gamma0.0"] = dict(days=rows)
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def b_lower_bound_checks():
    """Checks of the perfect-foresight benchmark (Cases 1 and 2, July mean day, SBC-compliant): the bound (gamma = 0) with
    the door exchange linearised about 0.2 and 1.0 K instead of 0.5 K; and the hold-at-ceiling energy of the linear model against the
    nonlinear engine run with ideal equipment holding the ceiling in the lowest mode (cooling setpoint 24 degC, no
    differential, no cycling loss, standby or capacity derating; all July days, cooling electricity per day)."""
    from experiments.optimiser import lower_bound as LB
    out = {}
    try:
        for sc in ("apartment", "floor"):
            M.set_scope(sc, REF_FLOORS)
            for c in CITIES:
                d = {}
                d["vmin22_dT0.5"] = r2(LB.solve(c, MAIN, month=6, vmin=22.0, gamma=0.0)["gap_pct"])      # the bound (gamma = 0)
                for dt0 in (0.2, 1.0):
                    d[f"vmin22_dT{dt0}"] = r2(LB.solve(c, MAIN, month=6, vmin=22.0, dT0=dt0, gamma=0.0)["gap_pct"])
                r = LB.solve(c, MAIN, month=6, vmin=22.0, gamma=M.GAMMA_IN, method="fixedpoint", iters=1)   # reference at gamma = 0.02 (one solve)
                with patched(REP="all", MSCALE=np.ones(12), HYST=0.0, CYCLING_CD=None, ONOFF_ZONES=None, STANDBY_KW=0.0, CAP_DERATE=False,
                             _MODE=(99.0, 99.0), ESC_KICK=False, MIN_OFF_MIN=0):                                # lowest mode, as the benchmark's hold reference
                    th = sim(c, MAIN, [[M.COMFORT[1], hs, 0, 0]])[0]
                d["E_hold_kwh_day"] = r2(r["E_hold"]); d["engine_hold_kwh_day"] = r2(float(th["kwh_cool_m"][6]) / 31)
                d["engine_vs_linear_pct"] = r1(100 * (d["engine_hold_kwh_day"] / d["E_hold_kwh_day"] - 1))
                out[f"{sc}/{c}"] = d
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def b_comparative():
    """Like-for-like comparison with EnergyPlus (hvac_savings.comparative): storey box in both engines, the room model
    with the same ideal control, and BESTEST-style cases 600/900/600FF/900FF on the Riyadh weather. Needs EnergyPlus."""
    from hvac_savings import comparative as CP
    return CP.main()


def b_equipment_split():
    """Where the compressor electricity goes (whole building, Case 3, SBC-compliant, reference policy): the share of the
    units' annual electricity (cycling losses included, standby and latent excluded) used by the fixed-speed 3-ton units
    of the guest rooms and of the stair, and by the inverter units, with their share of the conditioned floor area,
    runtime fraction and peak hourly load ratio; and the same split with the 3-ton units as inverter units."""
    out = {}
    names = np.array(M.ROOM)
    groups = dict(guest_fixed=M.COND & M.FIXED_SPEED & (names == "guest_room"),
                  stair_fixed=M.COND & M.FIXED_SPEED & (names == "stair"),
                  inverter=M.COND & ~M.FIXED_SPEED)
    a_c = float(M.AREA[M.COND].sum())
    for c in CITIES:
        r = sim(c, MAIN, [M.CODE])[0]; ez = np.asarray(r["kwh_zone"]); tot = float(ez[M.COND].sum())
        with patched(ONOFF_ZONES=np.zeros(M.NZ, bool), CYCLING_CD=np.where(M.COND, M._pl["cycling_Cd"]["inverter"], 0.0)):   # as variant three_ton_inverter
            ri = sim(c, MAIN, [M.CODE])[0]
        ezi = np.asarray(ri["kwh_zone"])
        row = dict(total_unit_kwh=round(tot), total_unit_kwh_all_inverter=round(float(ezi[M.COND].sum())))
        for g, msk in groups.items():
            row[g] = dict(n_units=int(msk.sum()), area_share_pct=r1(100 * float(M.AREA[msk].sum()) / a_c),
                          kwh_share_pct=r1(100 * float(ez[msk].sum()) / tot), kwh=round(float(ez[msk].sum())),
                          kwh_as_inverter=round(float(ezi[msk].sum())),
                          runtime_frac=r3(float(np.asarray(r["util"])[msk].mean() / max(np.asarray(r["peakfr"])[msk].mean(), 1e-9))),
                          peak_load_ratio=r2(float((np.asarray(r["q_hour_max"])[msk] / M.QC[msk]).max())))
        out[c] = row
    return out


def b_joint_favourable():
    """The case most favourable to thermostat pre-cooling, all at once (SBC-compliant building, Case 3): one node per
    zone (the full capacitance on the air), a COP independent of the indoor air (gamma = 0), inverter units held in
    their lowest mode (no escalation, no full-capacity response) and no cycling loss. The whole lattice and the ramped
    pre-peak setbacks (0.5-3 K) are run; reported: the best comfort-feasible saving and policy, the cost of 2 K pre-peak
    and night pre-cooling, and the best ramp."""
    out = {}
    fav = dict(TWO_NODE=None, GAMMA_IN=0.0, _MODE=(99.0, 99.0), ESC_KICK=False, CYCLING_CD=np.zeros(M.NZ))
    for c in CITIES:
        with patched(**fav):
            R = sim(c, MAIN, GRID)
            b = np.array([r["bill"] for r in R])
            feas = adm(R); k = argbest(b, feas)
            i2p = GRID.index([cs, hs, 0, 2]); i2n = GRID.index([cs, hs, 2, 0])
            ramps = []
            for x in PPG[1:]:
                with patched(SP_HOURLY=ramp_profile(x)):
                    r = sim(c, MAIN, [M.CODE])[0]
                ramps.append(dict(depth=x, bill_pct=r2(-pct(r["bill"], b[0])), hot_Ch=round(r["cviol_hot"])))
        out[c] = dict(best_policy=GRID[k][2:], best_saving_pct=r2(pct(b[k], b[0])), n_saving=int(((b < b[0]) & feas).sum()),
                      prepeak2_pct=r2(-pct(b[i2p], b[0])), night2_pct=r2(-pct(b[i2n], b[0])),
                      min_increase_pct=r2(min(-pct(bb, b[0]) for bb in b[1:])), ramps=ramps,
                      best_ramp_pct=min(x["bill_pct"] for x in ramps))
    return out



def _grid_summary(R, b_ref=None):
    """Best comfort-feasible pre-cool of the lattice GRID (R[k] for GRID[k]) and the 2 K pre-peak and night penalties."""
    b = np.array([r["bill"] for r in R]); h = np.array([r["cviol_hot"] for r in R])
    feas = adm(R); k = argbest(b, feas)
    i2p = GRID.index([cs, hs, 0, 2]); i2n = GRID.index([cs, hs, 2, 0])
    return dict(best_policy=GRID[k][2:] if k is not None else None, best_saving_pct=r2(pct(b[k], b[0])) if k is not None else None,
                n_admissible=int(feas.sum()), reference_admissible=bool(feas[0]), prepeak2_pct=r2(-pct(b[i2p], b[0])),
                night2_pct=r2(-pct(b[i2n], b[0])), min_increase_pct=r2(min(-pct(bb, b[0]) for bb in b[1:])),
                bill=round(b[0]), hot_Ch=round(h[0]), **({} if b_ref is None else dict(bill_vs_base_pct=r2(-pct(b[0], b_ref)))))


def start_tau_min():
    """Start-based cycling loss calibrated to the hourly part-load factor at the cyclic rating point (6 min on, 24 min off:
    runtime fraction 0.2, two starts an hour): each start costs the unit's full power for 60*0.2*(1/(1-0.8 Cd)-1)/2 min,
    1.5 min for C_d = 0.25 and 0.82 min for C_d = 0.15."""
    cd = np.broadcast_to(np.asarray(M.CYCLING_CD, float), (M.NZ,))
    return np.where(M.COND, 60.0 * 0.2 * (1.0 / (1.0 - 0.8 * cd) - 1.0) / 2.0, 0.0)


def b_controller_resolution():
    """How the controller's time resolution and the form of the cycling loss move the results (SBC-compliant building,
    Case 3): the base case (3-min step, which also sets the shortest cycle; hourly part-load factor); the 3-min step with a
    start-based cycling loss; a 1-min step, at which the 3-min restart delay spans three steps and binds; and a 1-min step
    with time-based minimum on- and off-times of 3 min and the start-based loss. Lattice and current practice each."""
    out = {}; dt1 = min(EXP["convergence_check_dt_h"])
    for c in CITIES:
        b_base = None; row = {}
        for tag in ("step3_plf", "step3_start", "step1_plf", "step1_minon3_start"):
            ov = {}
            if "step1" in tag: ov.update(DT=dt1, SUB=int(round(1 / dt1)))
            if "start" in tag: ov.update(START_TAU_MIN=start_tau_min(), CYCLING_CD=np.zeros(M.NZ))
            if "minon3" in tag: ov.update(MIN_ON_MIN=3.0)
            with patched(**ov):
                R = sim(c, MAIN, GRID + [M.CURRENT])
                where = {}
                if not O.admissible(R[0]):                       # where and when the reference policy leaves the safe set
                    with patched(RECORD_HOURLY=True):
                        rh = sim(c, MAIN, [M.CODE])[0]
                    moh = M.ambient(c)[1]; Vh = np.asarray(rh["V_hourly"])[:, M.COND]
                    hot_h = (Vh > M.COMFORT[1] + 1e-6).any(1); zh = np.asarray(rh["zone_hot_Kh"])
                    where = dict(hot_months=sorted({int(m) + 1 for m in moh[hot_h]}),
                                 fixed_speed_share_pct=r1(100 * float(zh[M.COND & M.FIXED_SPEED].sum()) / max(float(zh[M.COND].sum()), 1e-9)),
                                 max_room_C=r2(float(np.asarray(rh["Vmax"])[M.COND].max())))
            if b_base is None: b_base = R[0]["bill"]
            g = _grid_summary(R[:-1], b_base); g.update(where)
            g.update(setpoint_pct=r1(pct(R[0]["bill"], R[-1]["bill"])), starts_per_unit_h=r2(R[0]["starts_per_unit_h"]))
            row[tag] = g
        out[c] = row
    return out


def b_humidity_levers():
    """Diagnostic humidity balance (coil removal at a sensible heat ratio while a unit runs, infiltration and internal
    moisture in) for the levers: current practice, the reference setpoint, and 2 K pre-peak and night pre-cooling
    (SBC-compliant building, Case 3): share of cooling-season hours with a conditioned room above 60 % RH and the share
    of the latent load the coil removes."""
    out = {}
    pols = {"current_22C": M.CURRENT, "reference_23.5C": M.CODE, "prepeak_2K": [cs, hs, 0, 2], "night_2K": [cs, hs, 2, 0]}
    for c in CITIES:
        for shr in (0.75, 0.65):
            with patched(MOISTURE=dict(SHR=shr, internal=True)):
                R = sim(c, MAIN, list(pols.values()))
            out[f"{c}/SHR{shr}"] = {k: dict(rh60_pct_hours=r1(100 * r["rh60_frac"][M.COND].mean()),
                                            coil_share_pct=r1(100 * r["latent_coil_share"])) for k, r in zip(pols, R)}
    return out


def b_precool_targeted():
    """Pre-cooling applied only where storage is most likely to pay (SBC-compliant building, Case 3): the units of the
    top floor, under the roof, or the units of the rooms with west windows; the whole lattice each, with the base model
    and, for the top floor, with the choices most favourable to pre-cooling combined (as in joint_favourable)."""
    out = {}
    names = np.array(M.ROOM)
    masks = dict(top_floor=M.COND & (M.FLOOR_OF == M.FLOORS - 1),
                 west_rooms=M.COND & ((M.GLZ0_O * M.A_EXT_O)[:, M.ORIENT.index("W")] > 0))
    fav = dict(TWO_NODE=None, GAMMA_IN=0.0, _MODE=(99.0, 99.0), ESC_KICK=False, CYCLING_CD=np.zeros(M.NZ))
    for c in CITIES:
        row = {}
        for tag, msk in masks.items():
            with patched(PRECOOL_ZONES=msk):
                row[tag] = dict(n_units=int(msk.sum()), **_grid_summary(sim(c, MAIN, GRID)))
        with patched(PRECOOL_ZONES=masks["top_floor"], **fav):
            row["top_floor_favourable"] = _grid_summary(sim(c, MAIN, GRID))
        out[c] = row
    return out


def b_sizing_margin():
    """Comfort against sizing margin (SBC-compliant building, Case 3): units sized by load (all inverter) at each margin of
    right_sizing.curve_margins, with the base controller and with a controller that starts each unit at full capacity;
    hot-side excursion and saving against the catalogue units. Separates the excursion due to the controller's lag at
    start-up from a shortage of capacity."""
    out = {}
    for c in CITIES:
        b0 = sim(c, MAIN, [M.CODE])[0]["bill"]; row = {}
        for tag, ov in (("base_controller", {}), ("full_capacity_start", dict(START_FULL=True))):
            rr = {}
            for mg in RS["curve_margins"]:
                with patched():
                    M.right_size(c, MAIN, margin=mg)                 # sized with the base controller in both cases
                    with patched(**ov):
                        r = sim(c, MAIN, [M.CODE])[0]
                    tons = float((M.QC[M.COND] / M.TON_KW).sum())
                    plr = float((np.asarray(r["q_hour_max"])[M.COND] / M.QC[M.COND]).max())   # with the right-sized QC
                    n075 = int((np.isclose(M.QC[M.COND] / M.TON_KW, 0.75)).sum())
                zh = np.asarray(r["zone_hot_Kh"]); names = np.array(M.ROOM)
                rr[f"{mg:g}"] = dict(hot_Ch=round(float(r["cviol_hot"]), 1), saving_pct=r1(pct(r["bill"], b0)), tons=r1(tons),
                                    peak_load_ratio=r2(plr), n_units_at_min_size=n075, max_room_C=r2(float(np.asarray(r["Vmax"])[M.COND].max())),
                                    guest_room_share_pct=r1(100 * float(zh[M.COND & (names == "guest_room")].sum()) / max(float(zh[M.COND].sum()), 1e-9)))
            row[tag] = rr
        out[c] = row
    return out


def b_lp_complexity():
    """Size and solve time of the optimal-control linear program (experiments/optimiser/lower_bound.py) for the mean July
    day of Case 1, Case 2 and the top floor under the roof (SBC-compliant, Riyadh, rooms down to the band floor): the bound
    (gamma = 0, one solve) and the search with the engine's indoor COP factor (sequential linear programming, several
    solves); and the whole building (Case 3), bound only, with Clarabel. Run on an idle machine: the times
    are wall-clock."""
    from experiments.optimiser import lower_bound as LB
    out = {}
    try:
        for sc in ("apartment", "floor", "top_floor"):
            M.set_scope(sc, REF_FLOORS)
            rb = LB.solve("Riyadh", MAIN, month=6, kind="mean", vmin=22.0, gamma=0.0)
            r = LB.solve("Riyadh", MAIN, month=6, kind="mean", vmin=22.0, gamma=M.GAMMA_IN)
            out[f"{sc}/day"] = dict(n_vars=r["n_vars"], n_constraints=r["n_constraints"], t_bound_s=r2(rb["t_solve_s"]),
                                    n_solves=r["n_solves"], t_solve_s=r2(r["t_solve_s"]), t_per_solve_s=r2(r["t_solve_s"] / r["n_solves"]))
        M.set_scope("building", REF_FLOORS)                                              # Case 3: bound only, hourly steps, Clarabel
        rb = LB.solve("Riyadh", MAIN, month=6, kind="mean", vmin=22.0, gamma=0.0, solver="clarabel")
        out["building/day"] = dict(n_vars=rb["n_vars"], n_constraints=rb["n_constraints"], t_bound_s=r2(rb["t_solve_s"]))
    finally:
        M.set_scope("building", REF_FLOORS)
    return out


def b_tight_frontier():
    """Units sized tightly (TIGHT_MARGIN, the hard instances): no lattice policy at the reference setpoints is
    admissible, so the comfort frontier is sought below the reference cooling setpoint, on the thermostat's grid from
    the band floor up. Reports, per envelope and city, every setpoint's admissibility, the highest admissible cooling
    setpoint and its bill against the catalogue units at the reference policy (sampled year, Case 3)."""
    out = {}; sps = [round(x, 2) for x in np.arange(M.COMFORT[0], cs + 1e-6, M.SP_STEP)]
    for env in ENVS:
        for c in CITIES:
            ref = sim(c, env, [M.CODE])[0]["bill"]
            with patched():
                M.right_size(c, env, margin=TIGHT_MARGIN)
                R = sim(c, env, [[v, hs, 0, 0] for v in sps])
            ok = adm(R); k = max((i for i in range(len(sps)) if ok[i]), default=None)
            out[f"{env}/{c}"] = dict(setpoints=sps, admissible=[bool(x) for x in ok], hot_Kh=[r2(r["cviol_hot"]) for r in R],
                                     cold_Kh=[r2(r.get("sviol_cold", 0.0)) for r in R], bill=[round(r["bill"]) for r in R],
                                     frontier_setpoint=None if k is None else sps[k],
                                     frontier_bill=None if k is None else round(R[k]["bill"]), catalogue_reference_bill=round(ref),
                                     saving_vs_catalogue_pct=None if k is None else r1(pct(R[k]["bill"], ref)))
    return out


def b_floor_guard():
    """Floor guard (low-limit heating outside the heating months) on and off: reference policy of the pre-code and
    SBC-compliant buildings, Case 3, all 365 days. Reports the excursion below the 20 degC floor, the coldest room and
    the heating energy the guard adds."""
    out = {}
    with patched(REP="all", MSCALE=np.ones(12)):
        for env in ENVS:
            for c in CITIES:
                row = {}
                for tag, g in (("with_guard", M.FLOOR_GUARD_SP), ("without_guard", None)):
                    with patched(FLOOR_GUARD_SP=g):
                        r = sim(c, env, [M.CODE])[0]
                    row[tag] = dict(admissible=bool(O.admissible(r)), cold_Kh=r2(r.get("sviol_cold", 0.0)),
                                    min_room_C=r2(r["Vmin"][M.COND].min()), bill=round(r["bill"]), kwh=round(r["kwh"]),
                                    heat_kwh=round(float(np.sum(r["kwh_heat_m"]))))
                row["guard_heat_kwh"] = row["with_guard"]["heat_kwh"] - row["without_guard"]["heat_kwh"]
                row["guard_bill_pct"] = r2(100 * (row["with_guard"]["bill"] / row["without_guard"]["bill"] - 1))
                out[f"{env}/{c}"] = row
    return out


UQ_PARAMS = [  # name, low, high: assumed inputs varied jointly in the uncertainty analysis (uniform, Latin hypercube)
    ("gamma_indoor", 0.0, 0.03), ("cop_slope_outdoor", 0.01, 0.03), ("cop_ratio_low", 1.30, 1.60), ("cop_ratio_mid", 1.10, 1.30),
    ("cd_inverter", 0.05, 0.25), ("cd_fixed", 0.15, 0.35), ("escalation_scale", 0.5, 2.0), ("air_fraction", 0.02, 0.15),
    ("cop_latent", 2.5, 4.0), ("ach", 0.2, 0.8), ("gains_scale", 0.7, 1.3), ("wall_u_factor", 1.0, 1.2),
    ("absorptance", 0.5, 0.9), ("door_scale", 0.5, 1.5), ("derating_slope", 0.005, 0.02),
    ("weather_swing", 0.8, 1.3), ("weather_shift_K", -1.0, 2.0)]


def _uq_apply(x, city):
    """Set the model to the parameter vector x (dict) for `city`; called inside patched()."""
    zm = dict(M.ZONE_MODEL); C3 = M.COOLc[3]
    M.GAMMA_IN = x["gamma_indoor"]; M._cop = dict(M._cop, beta=x["cop_slope_outdoor"]); M._cd = dict(M._cd, beta=x["derating_slope"])
    M.COOLc = np.array([np.nan, C3 * x["cop_ratio_low"], C3 * x["cop_ratio_mid"], C3])
    M.CYCLING_CD = np.where(M.FIXED_SPEED, x["cd_fixed"], x["cd_inverter"]) * M.COND
    M._MODE = (0.4 * x["escalation_scale"], 1.0 * x["escalation_scale"])
    M.TWO_NODE = dict(zm, air_fraction=x["air_fraction"]); M.COP_LAT = x["cop_latent"]; M.ACH = x["ach"]
    M.GAINb = M.GAINb * x["gains_scale"]; M.GAINd = M.GAINd * x["gains_scale"]
    for e in ENVS: M.ENVELOPES[e]["U_WALL"] *= x["wall_u_factor"]
    M.ALPHA = x["absorptance"]; M.K_DOOR = M.K_DOOR * x["door_scale"]
    _weather_override(city, _diurnal(x["weather_swing"], x["weather_shift_K"]))


def _uq_eval(x, city):
    """Levers of one parameter sample: setpoint, envelope, 3-ton inverter, and the pre-cooling results (SENS_POLS)."""
    cop0, cd0 = M._cop, M._cd
    try:
        with patched():
            _uq_apply(x, city)
            try:
                R = sim(city, MAIN, SENS_POLS); rp = sim(city, "noncompliant", [M.CODE])[0]
                M.ONOFF_ZONES = np.zeros(M.NZ, bool); M.CYCLING_CD = np.where(M.COND, x["cd_inverter"], 0.0)
                ri = sim(city, MAIN, [M.CODE])[0]
            finally:
                M._AMB.pop((city, M.REP), None)
    finally:
        M._cop, M._cd = cop0, cd0
    row = sens_row(R); b0 = R[0]["bill"]
    row.update(envelope_pct=r1(pct(b0, rp["bill"])), inverter_pct=r1(pct(ri["bill"], b0)),
               precode_admissible=bool(O.admissible(rp)), inverter_admissible=bool(O.admissible(ri)))
    return row


def b_uncertainty(cities=None):
    """Global uncertainty analysis (SBC-compliant building, Case 3, sampled year): the assumed equipment, controller,
    zone, envelope, air and weather inputs of UQ_PARAMS varied jointly by a Latin hypercube (EXP uncertainty.n_samples,
    fixed seed). For each sample and city: the setpoint, envelope and inverter levers, the best admissible pre-cool and
    the 2 K pre-peak and night costs. Reported: median and 5-95 % range of each output, the share of samples in which any
    pre-cooling policy saves, in which the lever ranking holds, and partial rank correlation coefficients (PRCC)."""
    from scipy.stats import qmc, rankdata
    cfg = EXP["uncertainty"]; n = cfg["n_samples"]
    U = qmc.LatinHypercube(d=len(UQ_PARAMS), seed=cfg["seed"]).random(n)
    X = np.array([[lo + u * (hi - lo) for u, (_, lo, hi) in zip(row, UQ_PARAMS)] for row in U])
    names = [p[0] for p in UQ_PARAMS]
    outs = ["setpoint_pct", "envelope_pct", "inverter_pct", "best_precool_pct", "prepeak2_pct", "night2_pct"]
    res = {}
    for c in (cities or CITIES):
        ck = ROOT / "results" / "cache" / f"uq_{c}_{n}_{cfg['seed']}.jsonl"; ck.parent.mkdir(parents=True, exist_ok=True)
        done = [json.loads(l) for l in ck.read_text().splitlines()] if ck.exists() else []    # resumable: one line per sample
        for x in X[len(done):]:
            done.append(_uq_eval(dict(zip(names, x)), c))
            with open(ck, "a") as fh: fh.write(json.dumps(done[-1], default=float) + "\n")
        rows = done
        # strict safe set: a lever compares two runs, and it counts only where both are admissible (excluded otherwise)
        okp = {"setpoint_pct": lambda r: r["reference_admissible"] and r.get("current_admissible", r["n_admissible"] == len(SENS_POLS)),
               "envelope_pct": lambda r: r["reference_admissible"] and r.get("precode_admissible", True),
               "inverter_pct": lambda r: r["reference_admissible"] and r.get("inverter_admissible", True)}
        Y = {o: np.array([np.nan if (r[o] is None or (o in okp and not okp[o](r))) else r[o] for r in rows], float) for o in outs}
        n_valid = {o: int((~np.isnan(Y[o])).sum()) for o in outs}
        adm_ = np.array([r["reference_admissible"] for r in rows])
        def q(v): v = v[~np.isnan(v)]; return [r2(np.percentile(v, 5)), r2(np.median(v)), r2(np.percentile(v, 95))] if len(v) else None
        prcc = {}
        Rx = np.column_stack([rankdata(X[:, j]) for j in range(X.shape[1])])
        for o in outs:
            ok = ~np.isnan(Y[o]); ry = rankdata(Y[o][ok]); Rk = Rx[ok]; vals = []
            for j in range(Rk.shape[1]):
                Z = np.column_stack([np.ones(ok.sum()), np.delete(Rk, j, 1)])
                ex = Rk[:, j] - Z @ np.linalg.lstsq(Z, Rk[:, j], rcond=None)[0]; ey = ry - Z @ np.linalg.lstsq(Z, ry, rcond=None)[0]
                vals.append(r2(float(np.corrcoef(ex, ey)[0, 1])))
            prcc[o] = dict(zip(names, vals))
        bp = Y["best_precool_pct"]; okb = ~np.isnan(bp)
        both_ = ~np.isnan(Y["envelope_pct"]) & ~np.isnan(Y["setpoint_pct"])
        rank_ok = (Y["envelope_pct"] > Y["setpoint_pct"]) & (Y["setpoint_pct"] > np.where(np.isnan(bp), 0, bp))
        padm = np.array([r.get("precode_admissible", True) for r in rows])
        res[c] = dict(quantiles={o: q(Y[o]) for o in outs}, n_samples=n, n_reference_admissible=int(adm_.sum()),
                      n_precode_admissible=int(padm.sum()), envelope_quantiles_precode_admissible=q(Y["envelope_pct"][padm]),
                      share_precool_saves_pct=r1(100 * float((bp[okb] > 0).mean())) if okb.any() else None,
                      max_precool_saving_pct=r2(float(np.nanmax(bp))) if okb.any() else None,
                      share_ranking_holds_pct=r1(100 * float(rank_ok[both_].mean())) if both_.any() else None,
                      n_ranking_samples=int(both_.sum()), n_valid=n_valid,
                      prepeak2_always_costs=bool(np.all(Y["prepeak2_pct"] > 0)), prcc=prcc,
                      samples=[dict(x=[r3(v) for v in X[i]], **{o: rows[i][o] for o in outs}, admissible=bool(adm_[i]),
                                    precode_admissible=bool(padm[i])) for i in range(n)])
    res["parameters"] = [dict(name=a, low=b, high=c_) for a, b, c_ in UQ_PARAMS]
    return res


def b_factorial():
    """Full 2^4 factorial of the levers (SBC-compliant vs pre-code envelope; catalogue units vs the 3-ton units as
    inverter units; cooling/heating setpoints of current practice vs the reference; a 2 K pre-peak pre-cool vs none),
    Case 3, sampled year, both cities. Main effects, two-way interactions and the Shapley attribution of the bill
    saving from (pre-code, catalogue, current practice, 2 K pre-peak) to (SBC, inverter, reference, none)."""
    from itertools import product, permutations
    inv = M._pl["cycling_Cd"]["inverter"]; out = {}
    pols = [list(M.CURRENT), list(M.CODE), [M.CURRENT[0], M.CURRENT[1], 0, 2.0], [M.CODE[0], M.CODE[1], 0, 2.0]]
    for c in CITIES:
        bill = {}; adm_ = {}
        for env, eq in product(ENVS, ("catalogue", "inverter")):
            with patched():
                if eq == "inverter": M.ONOFF_ZONES = np.zeros(M.NZ, bool); M.CYCLING_CD = np.where(M.COND, inv, 0.0)
                R = sim(c, env, pols)
            for k_, (sp, pc) in enumerate((("current", "none"), ("reference", "none"), ("current", "prepeak2"), ("reference", "prepeak2"))):
                key = (env == MAIN, eq == "inverter", sp == "reference", pc == "none")
                bill[key] = float(R[k_]["bill"]); adm_[key] = bool(O.admissible(R[k_]))
        levers = ["envelope", "equipment", "setpoint", "no_precool"]
        start, end = (False,) * 4, (True,) * 4
        def v(S):                                          # bill with the levers in S switched to their end state
            return bill[tuple(i in S for i in range(4))]
        sh = np.zeros(4); perms = list(permutations(range(4)))
        for p_ in perms:
            S = set()
            for i in p_:
                sh[i] += v(S) - v(S | {i}); S.add(i)
        sh /= len(perms)
        tot = bill[start] - bill[end]
        main = {levers[i]: r1(100 * np.mean([1 - bill[k] / bill[tuple(k[:i]) + (False,) + tuple(k[i + 1:])]
                                             for k in bill if k[i]])) for i in range(4)}
        inter = {}
        for i in range(4):
            for j in range(i + 1, 4):
                d = [bill[k] - bill[tuple(False if t == i else x for t, x in enumerate(k))] - bill[tuple(False if t == j else x for t, x in enumerate(k))]
                     + bill[tuple(False if t in (i, j) else x for t, x in enumerate(k))] for k in bill if k[i] and k[j]]
                inter[f"{levers[i]}x{levers[j]}"] = r2(100 * float(np.mean(d)) / bill[start])
        out[c] = dict(bill_start=round(bill[start]), bill_end=round(bill[end]), saving_total_pct=r1(100 * tot / bill[start]),
                      shapley_SAR={levers[i]: round(sh[i]) for i in range(4)},
                      shapley_share_pct={levers[i]: r1(100 * sh[i] / tot) for i in range(4)},
                      shapley_pct_of_start={levers[i]: r1(100 * sh[i] / bill[start]) for i in range(4)},
                      main_effect_pct=main, interaction_pct_of_start=inter, all_admissible=all(adm_.values()),
                      inadmissible=[str(k) for k, a_ in adm_.items() if not a_],
                      bills={"".join("1" if t else "0" for t in k): round(b_) for k, b_ in bill.items()})
    out["levers"] = ["envelope (pre-code -> SBC)", "equipment (catalogue -> 3-ton inverter)", "setpoint (current practice -> reference)",
                     "pre-cooling (2 K pre-peak -> none)"]
    return out


def b_fullyear_admissibility(cities=None):
    """Admissibility on all 365 days (strict safe set) of every run whose result the paper reports from the sampled
    year: the factorial (both envelopes, catalogue and inverter 3-ton units; current-practice and reference setpoints;
    with and without a 2 K pre-peak pre-cool), units sized by load at the selected margin (inverter throughout, and with
    the fixed-speed zones kept), and the pre-specified dawn setback. Case 3. For each run: admissible, excursion above
    the ceiling and below the floor [K.h/yr]."""
    inv = M._pl["cycling_Cd"]["inverter"]; out = {}
    pols = [list(M.CURRENT), list(M.CODE), [M.CURRENT[0], M.CURRENT[1], 0, 2.0], [M.CODE[0], M.CODE[1], 0, 2.0]]
    names = ["current", "reference", "current_prepeak2", "reference_prepeak2"]
    ds = EXP["dawn_setback"]; prof = M.staircase(ds["depth_K"], ds["start_h"], ds["end_h"])
    row = lambda r: dict(admissible=bool(O.admissible(r)), hot_Kh=r2(r["cviol_hot"]), cold_Kh=r2(r.get("sviol_cold", 0.0)))
    for c in (cities or CITIES):
        d = {}; mg = rs_margin(c)                                    # the margin selected on the sampled year
        for tag, kf in (("right_sized", False), ("right_sized_keep_fixed", True)):
            with patched():
                M.right_size(c, MAIN, margin=mg, keep_fixed=kf)        # units sized as in the paper (sampled year) ...
                with patched(REP="all", MSCALE=np.ones(12)):           # ... then run on all 365 days
                    r = sim(c, MAIN, [M.CODE])[0]
            d[f"{MAIN}/{tag}/reference"] = row(r)
        with patched(REP="all", MSCALE=np.ones(12)):
            for env in ENVS:
                for eq in ("catalogue", "inverter"):
                    with patched():
                        if eq == "inverter": M.ONOFF_ZONES = np.zeros(M.NZ, bool); M.CYCLING_CD = np.where(M.COND, inv, 0.0)
                        R = sim(c, env, pols)
                    for nm, r in zip(names, R): d[f"{env}/{eq}/{nm}"] = row(r)
            with patched(SP_HOURLY=prof):
                d[f"{MAIN}/catalogue/dawn_setback"] = row(sim(c, MAIN, [M.CODE])[0])
        out[c] = dict(runs=d, all_admissible=all(v["admissible"] for v in d.values()),
                      inadmissible=[k for k, v in d.items() if not v["admissible"]])
    return out


BLOCKS = {"stage1": b_stage1, "operations": b_operations, "rooms": b_rooms, "humidity": b_humidity,
          "verification": b_verification, "cop_swing": b_cop_swing, "landscape": b_landscape,
          "setpoint_frontier": b_setpoint_frontier, "party_wall": b_party_wall,
          "wet_neighbours": b_wet_neighbours, "passive_zone": b_passive_zone, "inference": b_inference, "cost_model": b_cost_model, "stock_equivalent": b_stock_equivalent}
for c in CITIES:
    BLOCKS[f"scopes_{c}"] = b_scopes(c)
BLOCKS["running_example"] = b_running_example
BLOCKS.update(seasons_hc=b_seasons_hc, instances=b_instances, train_eval=b_train_eval, complexity=b_complexity,
              precool_decomposition=b_precool_decomposition, robustness=b_robustness,
              tenant_savings=b_tenant_savings, safe_floor=b_safe_floor, passive_control=b_passive_control,
              seasonal_levers=b_seasonal_levers, robustness_extra=b_robustness_extra,
              ablation=b_ablation, tou=b_tou, revision=b_revision, precool_fullyear=b_precool_fullyear,
              lower_bound=b_lower_bound, dawn_setback=b_dawn_setback, lower_bound_checks=b_lower_bound_checks,
              comparative=b_comparative, equipment_split=b_equipment_split, joint_favourable=b_joint_favourable,
              controller_resolution=b_controller_resolution, humidity_levers=b_humidity_levers, precool_targeted=b_precool_targeted,
              sizing_margin=b_sizing_margin, lp_complexity=b_lp_complexity, tight_frontier=b_tight_frontier, floor_guard=b_floor_guard, lower_bound_apartment=lambda: b_lower_bound(["apartment"]), lower_bound_floor=lambda: b_lower_bound(["floor"]), lower_bound_top_floor=lambda: b_lower_bound(["top_floor"]), lower_bound_building=b_lower_bound_building, factorial=b_factorial, uncertainty_Riyadh=lambda: b_uncertainty(["Riyadh"]), uncertainty_Jeddah=lambda: b_uncertainty(["Jeddah"]), fullyear_admissibility_Riyadh=lambda: b_fullyear_admissibility(["Riyadh"]), fullyear_admissibility_Jeddah=lambda: b_fullyear_admissibility(["Jeddah"]))
for c in CITIES:
    BLOCKS[f"scheduling_{c}"] = b_scheduling(c)
for Cz in EXP["thermal_mass_sweep"]:
    BLOCKS[f"mass_{Cz}"] = b_mass(Cz)

def run_block(name, force=False):
    p = PARTS / f"{name}.json"
    if p.exists() and not force:
        return json.load(open(p))
    t0 = time.perf_counter(); r = BLOCKS[name]()
    json.dump(r, open(p, "w"), indent=1, default=float)
    print(f"[{name}] done in {time.perf_counter() - t0:.0f}s", flush=True)
    return r


def merge():
    R = {"_meta": dict(reference_floors=REF_FLOORS, representative_meter=int(M.REP_METER),
                       apartment_area_m2=M.APT_AREA, integration_step_h=M.DT)}
    missing = []
    for name in BLOCKS:
        p = PARTS / f"{name}.json"
        if p.exists(): R[name] = json.load(open(p))
        else: missing.append(name)
    json.dump(R, open(OUT / "results.json", "w"), indent=1, default=float)
    return missing


def main():
    args = sys.argv[1:]
    if "--list" in args:
        for n in BLOCKS: print(("done   " if (PARTS / f"{n}.json").exists() else "missing"), n)
        return
    force = "--force" in args
    names = list(BLOCKS)
    if "--blocks" in args: names = args[args.index("--blocks") + 1].split(",")
    failed = []
    for n in names:
        try:
            run_block(n, force)
        except Exception as e:                       # keep going: every block is independent and resumable
            import traceback; traceback.print_exc(); failed.append(n); print(f"[{n}] FAILED: {e!r}", flush=True)
    missing = merge()
    if failed: print("failed blocks:", ", ".join(failed))
    print(f"results/results.json written ({len(BLOCKS) - len(missing)}/{len(BLOCKS)} blocks)"
          + (f"; missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
