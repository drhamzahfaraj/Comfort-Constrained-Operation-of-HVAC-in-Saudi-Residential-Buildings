"""
comparative.py -- like-for-like comparisons of the room model's engine with EnergyPlus.

Two tests, both on the measured TMYx weather of the study (all 365 days), both comparing DELIVERED heat (ideal
control, no equipment model), so that they test the building physics of the engine and not the equipment:

1. Storey box.  The engine of model.py run on exactly the geometry EnergyPlus is given in energyplus.py: one
   well-mixed zone per storey (the main body of the plan, the drawing's window-to-wall ratio on every face, the
   storey's capacitance and internal gains), with the same ground (ISO 13370 slab, monthly EPW ground temperature).
   Comparing it with EnergyPlus isolates the difference between the two engines; comparing it with the room model
   isolates the effect of resolving every room.  Also reports an annual heat-gain breakdown (solar through windows,
   internal gains, infiltration) for both engines.

2. BESTEST-style cases 600, 900, 600FF and 900FF (ANSI/ASHRAE Standard 140 geometry, constructions, gains,
   infiltration and set points) run in both engines on the Riyadh weather instead of the standard's Denver file:
   annual heating and cooling, peak hourly loads, and the free-floating minimum, mean and maximum temperatures.
   (A comparative test between two programs on the same inputs, not the standard's acceptance test.)

    python -m hvac_savings.comparative               # runs EnergyPlus (ENERGYPLUS_DIR) and the engine -> results/comparative.json
    python -m hvac_savings.comparative --parse-only  # re-reads the stored EnergyPlus outputs (no EnergyPlus needed)
"""
import os, json, math, subprocess
from contextlib import contextmanager
from pathlib import Path
import numpy as np
from hvac_savings import model as M
from hvac_savings import energyplus as E

ROOT = Path(__file__).resolve().parents[2]
_KEYS = ["FLOOR_GUARD_SP", "COP_LAT", "FLOORS", "NZ", "NPF", "DOORS", "K_DOOR", "DOOR_IS_OPENING", "GLZ0_O", "WWR_DRAWING", "AREA", "A_EXT", "A_EXT_O",
         "COND", "GAINF", "QC", "QH", "CZ", "FLOOR_OF", "SIDE", "ROOM", "METER", "NMET", "Kc", "Krow", "is_roof", "is_grnd",
         "SCOPE", "is_core", "FLOOR_AREA", "GROSS_AREA", "IX", "SHARED", "LATW", "REP_METER", "FIXED_SPEED", "ONOFF_ZONES",
         "CYCLING_CD", "TWO_NODE", "HYST", "CAP_DERATE", "STANDBY_KW", "W_IN", "REP", "MSCALE", "SEASONAL_CHANGEOVER",
         "ENVELOPES", "GAINb", "GAINd", "H_AIR", "ALPHA", "U_GRND_ISO", "ACH", "WFRAC", "RECORD_HOURLY", "SP_OFFSET", "HEAT_OFFSET",
         "DEADBAND", "OCC_AVAIL", "OCC_COUNT", "SP_HOURLY", "FAN_CONT_KW", "K_DOOR_NIGHT", "LAT_LOWMODE_FACTOR",
         "MOISTURE", "GROUND", "COOLc", "U_GRND", "_MODE", "DT", "SUB", "cop_factor", "TRACK_MODES", "SP_STEP",
         "GAMMA_IN", "MIN_OFF_MIN", "ESC_KICK", "HEAT_HOURLY", "MIN_ON_MIN", "START_TAU_MIN", "PRECOOL_ZONES", "START_FULL"]


@contextmanager
def patched(**over):
    """Temporarily replace module-level state of model.py; everything is restored on exit."""
    saved = {k: getattr(M, k) for k in _KEYS if hasattr(M, k)}
    saved["ENVELOPES"] = {k: dict(v) for k, v in M.ENVELOPES.items()}
    try:
        for k, v in over.items(): setattr(M, k, v)
        yield
    finally:
        for k, v in saved.items(): setattr(M, k, v)


def _ideal(nz, q_kw):
    """Ideal control: switch exactly at the set point (no differential), no cycling, standby or derating, sensible only."""
    return dict(HYST=0.0, CAP_DERATE=False, STANDBY_KW=0.0, CYCLING_CD=None, ONOFF_ZONES=None, FIXED_SPEED=np.zeros(nz, bool),
                QC=np.full(nz, q_kw), QH=np.full(nz, q_kw), W_IN=1.0, REP="all", MSCALE=np.ones(12), SEASONAL_CHANGEOVER=False,
                SP_OFFSET=0.0, HEAT_OFFSET=0.0, OCC_AVAIL=None, OCC_COUNT=None, SP_HOURLY=None, FAN_CONT_KW=0.0,
                MIN_OFF_MIN=0, ESC_KICK=False, HEAT_HOURLY=None)


def _zone_dict(n, area, aeo, glz, C, gainf, roof, grnd, Kc, floors):
    return dict(FLOORS=floors, NZ=n, NPF=1, DOORS=np.zeros((n, 0)), K_DOOR=np.zeros(0), DOOR_IS_OPENING=np.zeros(0, bool),
                GLZ0_O=glz, AREA=area, A_EXT=aeo.sum(1), A_EXT_O=aeo, COND=np.ones(n, bool), GAINF=gainf, CZ=C,
                FLOOR_OF=np.arange(n), SIDE=["box"] * n, ROOM=["storey"] * n, METER=np.zeros(n, int), NMET=1, Kc=Kc,
                Krow=Kc.sum(1), is_roof=roof, is_grnd=grnd, SCOPE="box", is_core=np.zeros(n, bool), FLOOR_AREA=float(area.sum()),
                GROSS_AREA=float(area.sum()), IX={}, SHARED=None, LATW=np.zeros(1), REP_METER=0, WFRAC=None)


# ----------------------------------------------------------------------------------------------- 1. storey box
def box_overrides():
    """The storey-box geometry of energyplus.idf() expressed as model.py state."""
    fl = M.FLOORS; npf = M.NPF; LX, LY, H = E.LX, E.LY, M.H_FLR
    area = np.full(fl, LX * LY); aeo = np.tile([LX * H, LY * H, LX * H, LY * H], (fl, 1))
    glz = np.full((fl, 4), E.WWR)
    C = np.full(fl, float(M.CZ[:npf].sum()))
    gsum = float((M.GAINF[:npf] * M.AREA[:npf]).sum()); gainf = np.full(fl, gsum / (LX * LY))
    Kc = np.zeros((fl, fl))
    for k in range(fl - 1):
        Kc[k, k + 1] = Kc[k + 1, k] = M.U_SLAB * LX * LY / 1000
    roof = np.arange(fl) == fl - 1; grnd = np.arange(fl) == 0
    d = _zone_dict(fl, area, aeo, glz, C, gainf, roof, grnd, Kc, fl)
    d.update(_ideal(fl, 500.0)); d["TWO_NODE"] = dict(M.ZONE_MODEL)
    return d


def _breakdown(city, env, r):
    """Annual heat gains of the current (box) model from its recorded hourly air temperatures [kWh_th]."""
    e = M.ENVELOPES[env]; Ta, moh, Wa, Iv, ghi = M.ambient(city)
    G_O = M.glazing(); k_win = e.get("SHGC", M.SHGC) * G_O / 1000
    solar = float((M.glazing_irradiance(city) @ (k_win * M.A_EXT_O).T).sum())
    k_inf = M.ACH * M.AREA * M.H_AIR * M._b["air_density"] * M._b["air_cp_kJkgK"] / 3600
    dq = (Ta[:, None] - r["V_hourly"]) * k_inf
    s_ = np.maximum(np.sin((np.arange(len(Ta)) % 24 - M._sch["gain_solar_phase_hr"]) / 24 * 2 * np.pi), 0)
    gains = float(((M.GAINb + M.GAINd * s_)[:, None] * (M.GAINF * M.AREA / M.A_REF)).sum())
    return dict(window_solar=round(solar), internal=round(gains), infiltration_gain=round(float(np.clip(dq, 0, None).sum())),
                infiltration_loss=round(float(np.clip(-dq, 0, None).sum())))


def box_engine(city, env):
    """Engine of model.py on the storey box: delivered sensible cooling and heating [kWh_th/m2 yr] and gains."""
    with patched(**box_overrides()):
        M.RECORD_HOURLY = True
        r = M.simulate(city, env, M.CODE)
        a = M.FLOOR_AREA
        return dict(cool_th=round(r["cool_th"] / a, 1), heat_th=round(r["heat_th"] / a, 1), gains=_breakdown(city, env, r))


SETBACKS = {"prepeak_2K": (0, 2.0), "night_2K": (2.0, 0)}   # (night depth, pre-peak depth) [K], as the thermostat policies


def setback_schedules(city, night, prepeak):
    """Hourly EnergyPlus setpoints that reproduce the engine's controller on the storey box: in the cooling months (no
    heating, as the engine's seasonal changeover) the cooling setpoint follows the policy's night (22-06 h) and
    pre-peak (10-13 h) setbacks; in the heating months the reference dual setpoints hold."""
    Ta, moh = M.ambient(city)[:2]
    heat_m = {k + 1 for k in range(12) if Ta[moh == k].mean() < M.COMFORT[0]}
    cs_, hs_ = M.CODE[0], M.CODE[1]; sch = M._sch
    day = np.full(24, cs_)
    for h in range(24):
        if (h >= sch["precool_night_start"] or h < sch["precool_morning_end"]) and night: day[h] = max(M.SAFE[0] + M.HYST, cs_ - night)
        if sch["prepeak_hours"][0] <= h < sch["prepeak_hours"][1] and prepeak: day[h] = max(M.SAFE[0] + M.HYST, cs_ - prepeak)
    cool = {m: (np.full(24, cs_) if m in heat_m else day) for m in range(1, 13)}
    heat = {m: (np.full(24, hs_) if m in heat_m else np.full(24, 10.0)) for m in range(1, 13)}
    return cool, heat


def setback_study(env, city):
    """Extra heat removed by 2 K pre-peak and 2 K night setbacks on the storey box, EnergyPlus against the engine, both
    under ideal control with the seasonal changeover; delivered sensible cooling and heating [kWh_th/m2 yr]."""
    out = {}
    with patched(**{**box_overrides(), "SEASONAL_CHANGEOVER": True, "RECORD_HOURLY": False}):
        cs_, hs_ = M.CODE[0], M.CODE[1]
        pols = [[cs_, hs_, 0, 0]] + [[cs_, hs_, n, p] for n, p in SETBACKS.values()]
        R = M.simulate_batch(city, env, pols); a = M.FLOOR_AREA
        sched = {k: setback_schedules(city, *v) for k, v in {"base": (0, 0), **SETBACKS}.items()}
    eng = {k: dict(cool_th=round(r["cool_th"] / a, 2), heat_th=round(r["heat_th"] / a, 2)) for k, r in zip(["base"] + list(SETBACKS), R)}
    ep = {k: E.run_box(env, city, setp=sched[k], suffix=f"_sb_{k}") for k in sched}
    for k in SETBACKS:
        d_e = 100 * (eng[k]["cool_th"] / eng["base"]["cool_th"] - 1); d_p = 100 * (ep[k]["cool_th"] / ep["base"]["cool_th"] - 1)
        out[k] = dict(engine_cool_th=eng[k]["cool_th"], ep_cool_th=ep[k]["cool_th"], engine_delta_pct=round(d_e, 2), ep_delta_pct=round(d_p, 2),
                      engine_heat_th=eng[k]["heat_th"], ep_heat_th=ep[k]["heat_th"])
    out["base"] = dict(engine_cool_th=eng["base"]["cool_th"], ep_cool_th=ep["base"]["cool_th"], engine_heat_th=eng["base"]["heat_th"], ep_heat_th=ep["base"]["heat_th"])
    return out


def rooms_engine(city, env):
    """The room model (132 zones) with the same ideal control: delivered sensible cooling per m2 of gross floor."""
    with patched(**{k: v for k, v in _ideal(M.NZ, 500.0).items() if k not in ("QC", "QH")}):
        M.QC = np.where(M.COND, 500.0, 0.0); M.QH = M.QC.copy()
        r = M.simulate(city, env, M.CODE); a = M.GROSS_AREA
        return dict(cool_th=round(r["cool_th"] / a, 1), heat_th=round(r["heat_th"] / a, 1))


# ----------------------------------------------------------------------------------------------- 2. BESTEST-style
BT = dict(LX=8.0, LY=6.0, H=2.7, win=12.0, gain_W=200.0, rad=0.6, ach=0.5, heat=20.0, cool=27.0,
          U={"600": dict(U_WALL=0.514, U_ROOF=0.318, U_WIN=3.0, SHGC=0.789, U_FLOOR=0.039),
             "900": dict(U_WALL=0.512, U_ROOF=0.318, U_WIN=3.0, SHGC=0.789, U_FLOOR=0.039)})


def bestest_capacitance(case):
    """Interior capacitance of the case [kWh/K]: layers inside the insulation (ISO 13790 effective mass) plus air."""
    walls = 2 * (BT["LX"] + BT["LY"]) * BT["H"] - BT["win"]; A = BT["LX"] * BT["LY"]
    roof = A * 0.010 * 950 * 840
    if case == "600":
        c = walls * 0.012 * 950 * 840 + roof + A * 0.025 * 650 * 1200
    else:
        c = walls * 0.100 * 1400 * 1000 + roof + A * 0.080 * 1400 * 1000
    air = A * BT["H"] * 1.2 * 1005
    return (c + air) / 3.6e6


def bestest_overrides(case):
    A = BT["LX"] * BT["LY"]; H = BT["H"]
    aeo = np.array([[BT["LX"] * H, BT["LY"] * H, BT["LX"] * H, BT["LY"] * H]])
    glz = np.array([[0.0, 0.0, BT["win"] / (BT["LX"] * H), 0.0]])
    d = _zone_dict(1, np.array([A]), aeo, glz, np.array([bestest_capacitance(case)]), np.array([M.A_REF / A]),
                   np.array([True]), np.array([True]), np.zeros((1, 1)), 1)
    d.update(_ideal(1, 50.0))
    u = BT["U"][case]; am = 2.5 if case == "600" else 3.0      # ISO 13790 Table 12: light / heavy class
    zm = M._P["zone_model"]
    k_am = 1.0 / (1.0 / (zm["h_is_W_m2K"] * zm["A_t_per_floor"]) + 1.0 / (zm["h_ms_W_m2K"] * am))
    d.update(TWO_NODE=dict(air_fraction=zm["air_fraction"], radiant_fraction=BT["rad"], k_am_W_m2K=k_am),
             ENVELOPES={"bestest": dict(U_WALL=u["U_WALL"], U_ROOF=u["U_ROOF"], U_WIN=u["U_WIN"], SHGC=u["SHGC"])},
             GAINb=BT["gain_W"] / 1000, GAINd=0.0, H_AIR=H, ALPHA=0.6, U_GRND_ISO=u["U_FLOOR"], ACH=BT["ach"],
             DEADBAND=1.0)
    return d


SETBACK_640 = np.array([-10.0 if (h >= 23 or h < 7) else 0.0 for h in range(24)])   # heating 10 degC 23-07 h, 20 degC otherwise


def bestest_engine(city, case, free=False):
    sb = case in ("640", "940"); case = {"640": "600", "940": "900"}.get(case, case)
    with patched(**{**bestest_overrides(case), "HEAT_HOURLY": SETBACK_640 if sb else None}):
        pol = [99.0, -50.0, 0, 0] if free else [BT["cool"], BT["heat"], 0, 0]
        r = M.simulate(city, "bestest", pol)
        if free:
            return dict(T_min=round(float(r["Vmin"][0]), 1), T_mean=round(float(r["Vmean"][0]), 1), T_max=round(float(r["Vmax"][0]), 1))
        return dict(heat_MWh=round(r["heat_th"] / 1000, 3), cool_MWh=round(r["cool_th"] / 1000, 3),
                    peak_heat_kW=round(float(r["qh_hour_max"][0]), 2), peak_cool_kW=round(float(r["q_hour_max"][0]), 2))


def bestest_idf(case, city, free=False):
    """EnergyPlus input of the BESTEST-style case (Standard 140 constructions and inputs; Riyadh weather and EPW ground).
    Cases 640/940: 600/900 with the heating setback of Standard 140 (10 degC from 23:00 to 07:00)."""
    sb = case in ("640", "940"); case = {"640": "600", "940": "900"}.get(case, case)
    Tg = M.ground_temperature(city)
    L, W, H = BT["LX"], BT["LY"], BT["H"]
    def wall(name, cons, p1, p2):
        (x1, y1), (x2, y2) = p1, p2
        return (f"BuildingSurface:Detailed, {name}, Wall, {cons}, Z, , Outdoors, , SunExposed, WindExposed, 0.5, 4,\n"
                f"    {x1},{y1},{H}, {x1},{y1},0, {x2},{y2},0, {x2},{y2},{H};\n")
    wc = "W600" if case == "600" else "W900"; fc = "F600" if case == "600" else "F900"
    geo = wall("SWall", wc, (0, 0), (L, 0)) + wall("EWall", wc, (L, 0), (L, W)) + wall("NWall", wc, (L, W), (0, W)) + wall("WWall", wc, (0, W), (0, 0))
    geo += f"BuildingSurface:Detailed, Floor, Floor, {fc}, Z, , Ground, , NoSun, NoWind, 0, 4,\n    {L},0,0, 0,0,0, 0,{W},0, {L},{W},0;\n"
    geo += f"BuildingSurface:Detailed, Roof, Roof, R600, Z, , Outdoors, , SunExposed, WindExposed, 0, 4,\n    0,{W},{H}, 0,0,{H}, {L},0,{H}, {L},{W},{H};\n"
    for k, x0 in enumerate((0.5, 4.5)):
        geo += (f"FenestrationSurface:Detailed, Win{k}, Window, GLZ, SWall, , , , , 4,\n"
                f"    {x0},0,2.2, {x0},0,0.2, {x0 + 3},0,0.2, {x0 + 3},0,2.2;\n")
    hvac = "" if free else (
        "ZoneControl:Thermostat, ZTh, Z, CtrlType, ThermostatSetpoint:DualSetpoint, DualSP;\n"
        "ThermostatSetpoint:DualSetpoint, DualSP, HeatSet, CoolSet;\n"
        "ZoneHVAC:IdealLoadsAirSystem, ZIdeal, , Zsup, , , 50, 13, 0.0156, 0.0077, NoLimit, , , NoLimit, , , , , ConstantSensibleHeatRatio, 1.0, None, , , , , ;\n"
        "ZoneHVAC:EquipmentConnections, Z, Zeq, Zsup, , Zair, Zret;\n"
        "ZoneHVAC:EquipmentList, Zeq, SequentialLoad, ZoneHVAC:IdealLoadsAirSystem, ZIdeal, 1, 1, , ;\n")
    out = ("Output:Variable, *, Zone Mean Air Temperature, Hourly;\n" if free else
           "Output:Variable, *, Zone Ideal Loads Supply Air Sensible Cooling Energy, Hourly;\n"
           "Output:Variable, *, Zone Ideal Loads Supply Air Sensible Heating Energy, Hourly;\n")
    heatset = (f"Schedule:Compact, HeatSet, Temp, Through: 12/31, For: AllDays, Until: 07:00, 10.0, Until: 23:00, {BT['heat']}, Until: 24:00, 10.0;"
               if sb else f"Schedule:Constant, HeatSet, Temp, {BT['heat']};")
    return f"""
Version, 26.1;
Timestep, 6;
Building, BESTEST{case}, 0, Country, 0.04, 0.4, FullExterior, 25, 6;
SimulationControl, No, No, No, No, Yes, No, 1;
RunPeriod, Year, 1, 1, , 12, 31, , , , , , , ;
GlobalGeometryRules, UpperLeftCorner, CounterClockWise, World;
Site:GroundTemperature:BuildingSurface, {', '.join(f'{t:.2f}' for t in Tg)};
ScheduleTypeLimits, Frac, 0, 1, Continuous;
ScheduleTypeLimits, Temp, -60, 200, Continuous;
ScheduleTypeLimits, Any, , , Continuous;
Schedule:Constant, Always1, Frac, 1.0;
Schedule:Constant, CoolSet, Temp, {BT['cool']};
{heatset}
Schedule:Constant, CtrlType, Any, 4;
Material, Plaster12, Smooth, 0.012, 0.16, 950, 840, 0.9, 0.6, 0.6;
Material, Plaster10, Smooth, 0.010, 0.16, 950, 840, 0.9, 0.6, 0.6;
Material, FG66, Rough, 0.066, 0.04, 12, 840, 0.9, 0.6, 0.6;
Material, FG1118, Rough, 0.1118, 0.04, 12, 840, 0.9, 0.6, 0.6;
Material, Siding, Rough, 0.009, 0.14, 530, 900, 0.9, 0.6, 0.6;
Material, Deck, Rough, 0.019, 0.14, 530, 900, 0.9, 0.6, 0.6;
Material, Timber, Smooth, 0.025, 0.14, 650, 1200, 0.9, 0.6, 0.6;
Material, Ins1003, Rough, 1.003, 0.04, 10, 1400, 0.9, 0.6, 0.6;
Material, Ins1007, Rough, 1.007, 0.04, 10, 1400, 0.9, 0.6, 0.6;
Material, Foam615, Rough, 0.0615, 0.04, 10, 1400, 0.9, 0.6, 0.6;
Material, Block100, Rough, 0.100, 0.51, 1400, 1000, 0.9, 0.6, 0.6;
Material, Slab80, Rough, 0.080, 1.13, 1400, 1000, 0.9, 0.6, 0.6;
Construction, W600, Siding, FG66, Plaster12;
Construction, R600, Deck, FG1118, Plaster10;
Construction, F600, Ins1003, Timber;
Construction, W900, Siding, Foam615, Block100;
Construction, F900, Ins1007, Slab80;
WindowMaterial:SimpleGlazingSystem, SG, {BT['U'][case]['U_WIN']:.3f}, {BT['U'][case]['SHGC']:.3f}, 0.8;
Construction, GLZ, SG;
Zone, Z, 0, 0,0,0, 1, 1, {H}, {L * W * H};
{geo}
ZoneInfiltration:DesignFlowRate, ZInf, Z, Always1, AirChanges/Hour, , , , {BT['ach']}, 1, 0, 0, 0;
OtherEquipment, ZGain, None, Z, Always1, EquipmentLevel, {BT['gain_W']:.0f}, , , 0, {BT['rad']}, 0;
{hvac}{out}"""


def _run_ep(idf_text, city, tag):
    d = ROOT / "results" / "energyplus" / tag; d.mkdir(parents=True, exist_ok=True)
    di = ROOT / "experiments" / "energyplus" / tag; di.mkdir(parents=True, exist_ok=True)
    p = di / "in.idf"
    if E.parse_only():
        if p.read_text() != idf_text:
            raise RuntimeError(f"stored IDF for {tag} differs from the current configs; rerun EnergyPlus")
    else:
        p.write_text(idf_text)
        for fn in ("eplusout.eso", "eplusout.err"):
            if (d / fn).exists(): (d / fn).unlink()
        subprocess.run([E.EP, "-w", E.WX[city], "-d", str(d), "-r", str(p)], capture_output=True, text=True)
    err = (d / "eplusout.err").read_text()
    if "Completed Successfully" not in err:
        raise RuntimeError(f"EnergyPlus failed for {tag}; see {d}/eplusout.err")
    return E.read_eso(d / "eplusout.eso")


def bestest_ep(city, case, free=False):
    s = _run_ep(bestest_idf(case, city, free), city, f"bestest_{case}{'FF' if free else ''}")
    if free:
        T = np.array(next(v for k, v in s.items() if "Mean Air Temperature" in k))
        return dict(T_min=round(float(T.min()), 1), T_mean=round(float(T.mean()), 1), T_max=round(float(T.max()), 1))
    c = np.array(next(v for k, v in s.items() if "Cooling" in k)) / 3.6e6; h = np.array(next(v for k, v in s.items() if "Heating" in k)) / 3.6e6
    return dict(heat_MWh=round(h.sum() / 1000, 3), cool_MWh=round(c.sum() / 1000, 3), peak_heat_kW=round(float(h.max()), 2),
                peak_cool_kW=round(float(c.max()), 2))


def main():
    out = {"box": {}, "bestest": {}}
    for env in ("compliant", "noncompliant"):
        for city in ("Riyadh", "Jeddah"):
            ep = E.run_box(env, city)
            out["box"][f"{env}/{city}"] = dict(energyplus=ep, engine_box=box_engine(city, env), engine_rooms=rooms_engine(city, env))
            print(env, city, out["box"][f"{env}/{city}"], flush=True)
    city = "Riyadh"
    for case in ("600", "900", "640", "940"):
        for free in ((False, True) if case in ("600", "900") else (False,)):
            tag = case + ("FF" if free else "")
            out["bestest"][tag] = dict(energyplus=bestest_ep(city, case, free), engine=bestest_engine(city, case, free))
            print(tag, out["bestest"][tag], flush=True)
    out["setback"] = {}
    for env in ("compliant", "noncompliant"):
        for city in ("Riyadh", "Jeddah"):
            out["setback"][f"{env}/{city}"] = setback_study(env, city)
            print("setback", env, city, out["setback"][f"{env}/{city}"], flush=True)
    json.dump(out, open(ROOT / "results" / "comparative.json", "w"), indent=1)
    return out


if __name__ == "__main__":
    import sys
    E.PARSE_ONLY = "--parse-only" in sys.argv
    main()
