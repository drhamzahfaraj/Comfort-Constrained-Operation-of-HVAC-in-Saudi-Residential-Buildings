"""
SINGLE SOURCE OF TRUTH -- Saudi residential inverter-HVAC simulator.

All physical/economic parameters are loaded from ../../experiments/configs/parameters.json
and climate profiles from ../../experiments/configs/climate.json, so the model is fully
config-driven: editing those files changes the run. Structural machinery
(integration, topology construction, psychrometrics) stays here.

Design principles (integrity):
  * ONE simulate() used for every number in the paper (no duplicated loops).
  * Compliant vs non-compliant = SAME building; ONLY envelope U-values differ.
  * Weather is the MEASURED TMYx hourly record (temperature, humidity, solar radiation).
  * Latent (dehumidification) load is computed INSIDE the model, hour by hour.
  * Solar gain: sol-air on opaque walls/roof and SHGC through windows, per orientation.
  * Control policy theta = (cool_sp, heat_sp, precool, prepeak).
  * Saudi two-tier volume tariff applied per meter and month.
"""
import json
from pathlib import Path
import numpy as np

# ---------------- Load configuration ----------------
_CFG_DIR = Path(__file__).resolve().parents[2] / "experiments" / "configs"
_P = json.load(open(_CFG_DIR / "parameters.json"))
_CL = json.load(open(_CFG_DIR / "climate.json"))

def _arr(x):  # JSON list -> np.array, null -> nan
    return np.array([np.nan if v is None else v for v in x], dtype=float)

# ---------------- Climate: measured TMYx weather ----------------
_WX_DIR = Path(__file__).resolve().parents[2] / "experiments" / "weather"
CITIES = {c: dict(epw=v["epw"], Tg=float(v["Tg"])) for c, v in _CL["cities"].items()}
DIM = _CL["days_in_month"]; Hh = np.arange(24)
REP = _P["numerics"]["REP"]; MSCALE = np.array([DIM[m] / REP for m in range(12)])
_SO = _P["solar"]; ALPHA = _SO["absorptance_opaque"]; H_OUT = _SO["h_out_W_m2K"]
_SKY = _SO["sky_longwave"]; SHGC = _SO["SHGC"]; RHO_G = _SO["ground_reflectance"]
_GA = _SO["glazing_angular"]
ORIENT = ("N", "E", "S", "W"); _GAMMA = {"N": 180.0, "E": -90.0, "S": 0.0, "W": 90.0}   # surface azimuth, south = 0

def _read_epw(city):
    rows = open(_WX_DIR / CITIES[city]["epw"]).read().splitlines()
    loc = rows[0].split(","); lat, lon, tz = float(loc[6]), float(loc[7]), float(loc[8])
    cols = (1, 2, 3, 6, 7, 9, 13, 14, 15, 12)   # month, day, hour, Tdb, Tdew, P, GHI, DNI, DHI, horizontal IR (EPW order)
    d = np.array([[float(r.split(",")[k]) for k in cols] for r in rows[8:8 + 8760]])
    return lat, lon, tz, d   # columns: month, day, hour(1-24), Tdb, Tdew, P[Pa], GHI, DNI, DHI, IR_h

def _vertical_irradiance(lat, lon, tz, month, day, hour, ghi, dni, dhi):
    """Hourly irradiance [W/m2] on N/E/S/W vertical surfaces (isotropic sky, ground reflectance), and the same
    weighted by the angle-dependent transmission of the glazing relative to normal incidence (for window solar gain)."""
    n = np.array([sum(DIM[:int(m) - 1]) + int(dd) for m, dd in zip(month, day)], float)
    B = 2 * np.pi * (n - 1) / 365
    eot = 229.2 * (0.000075 + 0.001868 * np.cos(B) - 0.032077 * np.sin(B) - 0.014615 * np.cos(2 * B) - 0.04089 * np.sin(2 * B))
    dec = np.radians(23.45 * np.sin(np.radians(360 * (284 + n) / 365)))
    st = (hour - 0.5) + (4 * (lon - 15 * tz) + eot) / 60.0          # solar time at mid-hour
    w = np.radians(15 * (st - 12)); ph = np.radians(lat)
    cosz = np.sin(ph) * np.sin(dec) + np.cos(ph) * np.cos(dec) * np.cos(w); up = cosz > 0
    out = {}; glz = {}
    for o in ORIENT:
        g = np.radians(_GAMMA[o])
        cth = (-np.sin(dec) * np.cos(ph) * np.cos(g) + np.cos(dec) * np.sin(ph) * np.cos(g) * np.cos(w)
               + np.cos(dec) * np.sin(g) * np.sin(w))
        beam = dni * np.clip(cth, 0, None) * up; dif = 0.5 * dhi + 0.5 * RHO_G * ghi
        th = np.degrees(np.arccos(np.clip(cth, 0, 1)))
        out[o] = beam + dif
        glz[o] = beam * np.interp(th, _GA["theta_deg"], _GA["ratio"]) + _GA["diffuse_ratio"] * dif
    return np.stack([out[o] for o in ORIENT], 1), np.stack([glz[o] for o in ORIENT], 1)

def ground_temperature(city, depth=None):
    """Monthly undisturbed ground temperature [degC] at the given depth, from the GROUND TEMPERATURES record of the
    TMYx weather file (12 values)."""
    depth = _P["ground"]["epw_depth_m"] if depth is None else depth
    for row in open(_WX_DIR / CITIES[city]["epw"]).read().splitlines()[:8]:
        if row.startswith("GROUND TEMPERATURES"):
            f = row.split(","); n = int(f[1]); k = 2
            for _ in range(n):
                d = float(f[k]); vals = [float(x) for x in f[k + 4:k + 16]]
                if abs(d - depth) < 1e-6: return np.array(vals)
                k += 16
    raise ValueError(f"no ground temperature at {depth} m in the weather file of {city}")

_AMB = {}; _AMB_G = {}; _AMB_SKY = {}
def sky_excess(city, rep=None):
    """Net long-wave emission of a horizontal surface at air temperature to the sky, eps (sigma T_a^4 - IR_h) [W/m2]."""
    rep = REP if rep is None else rep; ambient(city, rep); return _AMB_SKY[(city, rep)]

def glazing_irradiance(city, rep=None):
    """Irradiance on N/E/S/W windows weighted by the glazing's angle-dependent transmission (same days as ambient)."""
    rep = REP if rep is None else rep; ambient(city, rep); return _AMB_G[(city, rep)]

def ambient(city, rep=None):
    """Measured weather for REP representative days per month (evenly spaced through each month of the
    TMYx year; rep='all' uses every day). Returns Ta, month-of-hour, outdoor humidity ratio, the vertical
    irradiance on N/E/S/W surfaces and the global horizontal irradiance, hour by hour."""
    rep = REP if rep is None else rep
    key = (city, rep)
    if key not in _AMB:
        lat, lon, tz, d = _read_epw(city)
        mon, day, hour, T, Td, Pa, ghi, dni, dhi, irh = d.T
        Iv, Ivg = _vertical_irradiance(lat, lon, tz, mon, day, hour, ghi, dni, dhi)
        pw = 0.6108 * np.exp(17.27 * Td / (Td + 237.3)); Wa = 0.622 * pw / (Pa / 1000.0 - pw)
        idx = []
        for m in range(12):
            days = range(1, DIM[m] + 1) if rep == "all" else [int((k + 0.5) * DIM[m] / rep) + 1 for k in range(rep)]
            for dd in days:
                idx.extend(np.where((mon == m + 1) & (day == dd))[0])
        idx = np.array(idx)
        _AMB[key] = (T[idx], (mon[idx] - 1).astype(int), Wa[idx], Iv[idx], ghi[idx]); _AMB_G[key] = Ivg[idx]
        _AMB_SKY[key] = _SKY["emissivity"] * (5.670374e-8 * (T[idx] + 273.15) ** 4 - irh[idx])
    return _AMB[key]

# ---------------- SHARED building ----------------
_b = _P["building"]
WFRAC = _b["WFRAC"]; H_FLR = _b["floor_height_m"]   # WFRAC None: glazing window by window from the drawing (see glazing())
H_AIR = _b.get("clear_height_m", H_FLR)              # clear room height: air volume (infiltration, latent load)
_BLD = json.load(open(_CFG_DIR / "building.json"))
A_REF = _BLD["reference_zone_area_m2"]; A_WALL_REF = _BLD["reference_ext_wall_m2"]
U_PART = _b["U_PART"]; U_SLAB = _b["U_SLAB"]; U_GRND = _b["U_GRND"]
C_ref = _b["C_zone"]  # thermal capacitance of a reference-area zone [kWh/K]
def _u_ground_iso13370():
    """Transmittance of the uninsulated slab on ground after ISO 13370 for the building footprint [W/m2K]."""
    g = _P["ground"]; fx, fy = _BLD["plan"]["footprint_m"]; A = fx * fy; Pm = 2 * (fx + fy); Bp = A / (0.5 * Pm)
    dt = 0.30 + g["lambda_W_mK"] * (g["R_si"] + g["R_f"] + g["R_se"])
    return 2 * g["lambda_W_mK"] / (np.pi * Bp + dt) * np.log(np.pi * Bp / dt + 1)
U_GRND_ISO = float(_u_ground_iso13370())
_pl = _P["plant"]
Q_COOL = _pl["Q_COOL"]; Q_HEAT = _pl["Q_HEAT"]
FR = _arr(_pl["FR"]); COOLc = _arr(_pl["COOLc"]); HEATc = _arr(_pl["HEATc"])
_MODE = _pl["mode_escalation"]
_cop = _pl["cop_ambient"]
def cop_factor(Ta): return np.clip(1 - _cop["beta"] * (Ta - _cop["ref_C"]), _cop["lo"], _cop["hi"])
_copi = _pl["cop_indoor"]
GAMMA_IN = _copi["gamma_per_K"]   # cooling-COP sensitivity to the indoor air temperature [1/K] (0: COP set by the outdoor air only)
def cop_indoor(V): return np.clip(1 - GAMMA_IN * (_copi["ref_C"] - V), _copi["lo"], _copi["hi"])
_g = _P["gains"]; GAINb = _g["GAINb"]; GAINd = _g["GAINd"]
COMFORT = tuple(_P["comfort"]["COMFORT"]); SAFE = tuple(_P["comfort"]["SAFE"])
DT = _P["numerics"]["DT"]; SUB = int(1 / DT); V_INIT = _P["numerics"]["V_init_C"]
_sch = _P["schedule"]
# ventilation / latent
_vl = _P["ventilation_latent"]
ACH = _vl["ACH"]; HFG = _vl["HFG"]; COP_LAT = _vl["COP_LAT"]
AIR_MASS = A_REF * H_AIR * _b["air_density"]  # per reference-area zone
def Psat(T): return 0.6108 * np.exp(17.27 * T / (T + 237.3))
def Wof(T, RH): pv = RH * Psat(T); return 0.622 * pv / (101.325 - pv)
W_IN = Wof(_vl["humidity_target"]["T_C"], _vl["humidity_target"]["RH"])

# ---------------- Envelope states (THE ONLY DIFFERENCE) ----------------
ENVELOPES = {k: {kk: float(vv) for kk, vv in v.items() if not kk.startswith("_")}
             for k, v in _P["envelopes"].items()}

# ---------------- Topology: SBC apartment building (from building.json) ----------------
TON_KW = 3.517
def _plan_geometry():
    """Spaces of one typical floor in absolute coordinates (A, mirrored B, core) and everything derived
    from their rectangles: floor area, exterior wall length per orientation, and shared wall lengths."""
    mx = _BLD["plan"]["mirror_x"]; sp = []
    for sd in ("A", "B"):
        for r in _BLD["apartment"]["rooms"]:
            rects = [[2 * mx - x1, y0, 2 * mx - x0, y1] if sd == "B" else [x0, y0, x1, y1] for x0, y0, x1, y1 in r["rects"]]
            sp.append(dict(r, rects=rects, id=f"{sd}.{r['name']}", side=sd))
    for r in _BLD["core"]["rooms"]:
        sp.append(dict(r, id=f"core.{r['name']}", side="core"))
    edges = []   # (space index, orientation, fixed coordinate, lo, hi)
    for k, p in enumerate(sp):
        for x0, y0, x1, y1 in p["rects"]:
            edges += [(k, "W", x0, y0, y1), (k, "E", x1, y0, y1), (k, "S", y0, x0, x1), (k, "N", y1, x0, x1)]
    opp = dict(W="E", E="W", S="N", N="S"); n = len(sp)
    shared = np.zeros((n, n)); faces = np.zeros((n, 4))
    for k, o, c, lo, hi in edges:
        covered = []
        for k2, o2, c2, lo2, hi2 in edges:
            if o2 == opp[o] and c2 == c and min(hi, hi2) > max(lo, lo2):
                covered.append((max(lo, lo2), min(hi, hi2)))
                if k2 != k: shared[k, k2] += min(hi, hi2) - max(lo, lo2)
        cov = sum(h - l for l, h in covered)
        faces[k, ORIENT.index(o)] += (hi - lo) - cov          # uncovered = exterior
    shared = shared / 2 + shared.T / 2                       # each pair counted from both sides
    area = np.array([sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in p["rects"]) for p in sp], float)
    return sp, area, faces, shared

SCOPES = ("apartment", "floor", "building")      # plus "top_floor": the typical floor under the roof (perfect-foresight benchmark)

def build(floors, scope="building"):
    """Assemble the model at one of three scopes:
      'apartment' -- apartment A alone, one intermediate storey: surfaces shared with the neighbour apartment,
                     the core and the storeys above/below face conditioned space and are adiabatic;
      'floor'     -- the typical floor (both apartments + core), one intermediate storey (adiabatic slabs);
      'top_floor' -- the same floor under the roof (adiabatic floor slab), for the perfect-foresight benchmark;
      'building'  -- the whole complex of `floors` storeys with ground contact and roof."""
    per, a1, faces, shared = _plan_geometry()
    if scope != "building":
        floors = 1
        if scope == "apartment":
            keep = [k for k, p in enumerate(per) if p["side"] == "A"]
            per = [per[k] for k in keep]; a1 = a1[keep]; faces = faces[keep]; shared = shared[np.ix_(keep, keep)]
    npf = len(per); nz = floors * npf
    ix = {(f, p["id"]): f * npf + k for f in range(floors) for k, p in enumerate(per)}
    rep = lambda key: np.array([p[key] for p in per] * floors, dtype=float)
    area = np.tile(a1, floors); aeo = np.tile(faces * H_FLR, (floors, 1)); ext = aeo.sum(1)
    ton = rep("ton"); gf = rep("gain_factor")
    cond = np.array([p["cond"] for p in per] * floors, bool)
    flo = np.repeat(np.arange(floors), npf)
    side = [p["side"] for p in per] * floors
    meter = np.array([2 * fl + (0 if sd == "A" else 1) if sd != "core" else 2 * floors
                      for fl, sd in zip(flo, side)])
    K = np.zeros((nz, nz))
    for f in range(floors):
        o = f * npf
        K[o:o + npf, o:o + npf] += U_PART * shared * H_AIR / 1000          # partitions (from the plan; clear height)
        if f < floors - 1:
            for k in range(npf):                                            # floor slab to the storey above
                K[o + k, o + npf + k] += U_SLAB * a1[k] / 1000; K[o + npf + k, o + k] += U_SLAB * a1[k] / 1000
    # open interior doorways: incidence matrix (+1 at room a, -1 at room b) and coefficient [kW per K^1.5]
    dr = _P["doors"]   # Brown & Solvason (1962): Q = (Cd W/3) sqrt(g H^3 |dT| / T); heat = rho cp Q dT
    kdoor = lambda w_, h_: (dr["open_fraction"] * _b["air_density"] * _b["air_cp_kJkgK"] * dr["Cd"] / 3 * w_
                            * np.sqrt(9.81 * h_ ** 3 / dr["mean_air_temperature_K"]))
    # glazing from the drawing: window widths per orientation (apartment A; W and E swap for the mirrored B)
    wh = _BLD["plan"].get("window_height_m", 1.5); sw = dict(N="N", S="S", E="W", W="E")
    awin1 = np.zeros((len(per), 4))
    for k, p in enumerate(per):
        for o, ws in p.get("windows_m", {}).items():
            o2 = sw[o] if p["side"] == "B" else o
            awin1[k, ORIENT.index(o2)] += sum(ws) * wh
    with np.errstate(invalid="ignore", divide="ignore"):
        glz1 = np.where(faces > 0, awin1 / np.maximum(faces * H_FLR, 1e-9), 0.0)
    assert (glz1 < 0.9).all(), "window wider than its wall"
    idx = {(p["side"], p["name"]): k for k, p in enumerate(per)}; pairs = []
    for f in range(floors):
        for sd in ("A", "B"):
            for d_ in _BLD["apartment"].get("doors", []):
                ra, rb = (d_["a"], d_["b"]) if isinstance(d_, dict) else d_[:2]
                w_ = (d_.get("width_m") if isinstance(d_, dict) else None) or dr["width_m"]
                h_ = (d_.get("height_m") if isinstance(d_, dict) else None) or dr["height_m"]
                if (sd, ra) in idx and (sd, rb) in idx:
                    i, j = f * npf + idx[(sd, ra)], f * npf + idx[(sd, rb)]
                    assert K[i, j] > 0, f"door {ra}-{rb} joins rooms that do not share a wall"
                    pairs.append((i, j, kdoor(w_, h_), isinstance(d_, dict) and d_.get("kind") == "opening"))
    DM = np.zeros((nz, len(pairs)))
    for k, (i, j, _, _) in enumerate(pairs): DM[i, k] = 1.0; DM[j, k] = -1.0
    kd = np.array([q[2] for q in pairs]); is_open = np.array([q[3] for q in pairs], bool)
    return dict(FLOORS=floors, NZ=nz, NPF=npf, DOORS=DM, K_DOOR=kd, DOOR_IS_OPENING=is_open, GLZ0_O=np.tile(glz1, (floors, 1)),
                WWR_DRAWING=float(awin1.sum() / max((faces * H_FLR).sum(), 1e-9)), AREA=area, A_EXT=ext, A_EXT_O=aeo, COND=cond, GAINF=gf,
                QC=ton * TON_KW, QH=ton * TON_KW * (Q_HEAT / Q_COOL), CZ=C_ref * area / A_REF,
                FLOOR_OF=flo, SIDE=side, ROOM=[p["name"] for p in per] * floors,
                METER=meter, NMET=2 * floors + 1, Kc=K, Krow=K.sum(1),
                is_roof=(flo == floors - 1) & (scope in ("building", "top_floor")), is_grnd=(flo == 0) & (scope == "building"), SCOPE=scope,
                is_core=cond & (ext == 0), FLOOR_AREA=area[np.array([p.get('floor_area', True) for p in per] * floors)].sum(), GROSS_AREA=area.sum(), IX=ix, SHARED=shared,
                LATW=np.bincount(meter, cond * area / A_REF, 2 * floors + 1))

SP_OFFSET = 0.0   # scalar or (NZ,) array added to every zone's cooling setpoint; reset by set_scope
HEAT_OFFSET = 0.0 # likewise for the heating setpoint
DEADBAND = _P["thermostat"]["auto_deadband_K"]   # minimum gap between the active cooling and heating setpoints [K]
TWO_NODE = None      # set by set_scope to the two-node zone model of the base case (ZONE_MODEL): air node (air and furniture)
                     # and structure node joined by k_am_W_m2K per m2 of floor; None: one node per zone (sensitivity)
RECORD_HOURLY = False # store the air (and structure) temperature of every zone at the end of every hour
LAT_LOWMODE_FACTOR = None  # None: latent removed at COP_LAT in every mode. A factor f < 1 multiplies the latent COP in
                     # the share of unit-time a meter's units spend off or in the lowest mode (a warm coil dehumidifies less)
TOU_PEAK_HOURS = (13, 17)  # illustrative time-of-use peak window [start, end) in the cooling months; the energy used in it
                     # is reported per meter and month (meter_kwh_peak_m) so that a time-of-use tariff can be applied afterwards
TRACK_MODES = False  # count the time conditioned zones spend in each mode (off, cool 1-3, heat 1-3) per month
CYCLING_CD = None    # set by set_scope: per-zone cycling degradation coefficient Cd; each unit's electricity in every hour is
                     # divided by the part-load factor PLF = 1 - Cd (1 - RTF), RTF being the fraction of the hour it runs
ONOFF_ZONES = None   # set by set_scope: units with a fixed-speed compressor, which run at full capacity whenever they run
K_DOOR_NIGHT = None  # None: door coefficients constant. An array like K_DOOR applies in the night hours DOOR_NIGHT_HOURS
DOOR_NIGHT_HOURS = (23, 7)
_th = _P["thermostat"]; _zm = _P["zone_model"]
HYST = _th["differential_K"] / 2      # half of the thermostat's on/off differential [K] (0: switch exactly at the setpoint)
SP_STEP = _th["setpoint_step_K"]      # resolution of the thermostat setpoint [K]
MIN_OFF_MIN = _th["min_off_min"]     # anti-short-cycle (restart) delay of the compressor [min]
ESC_KICK = True                      # a unit whose room is above its switching point at the start of a step runs at full capacity
START_FULL = False                   # True: a unit that switches on runs at full capacity in its first step (a controller that
                                     # anticipates the load at start-up instead of escalating a step later)
PRECOOL_ZONES = None                 # None: pre-cooling depths apply to every unit; an (NZ,) boolean mask restricts them to those zones
MIN_ON_MIN = 0.0                     # minimum on-time of the compressor [min]; 0: none (a unit may stop in the step after it starts)
START_TAU_MIN = None                 # None: cycling loss through the hourly part-load factor (CYCLING_CD). An (NZ,) array [min]:
                                     # each start costs the unit's full power in its starting mode for this time instead (start-based loss)
STANDBY_KW = _pl["standby_W_per_unit"] / 1000
_cd = _pl["capacity_derating"]
def cap_factor(Ta): return np.clip(1 - _cd["beta"] * (Ta - _cd["ref_C"]), _cd["lo"], _cd["hi"])
CAP_DERATE = True
ZONE_MODEL = dict(air_fraction=_zm["air_fraction"], radiant_fraction=_zm["radiant_fraction"],
                  k_am_W_m2K=1.0 / (1.0 / (_zm["h_is_W_m2K"] * _zm["A_t_per_floor"]) + 1.0 / (_zm["h_ms_W_m2K"] * _zm["A_m_per_floor"])))
HEAT_HOURLY = None   # (24,) offsets of the heating setpoint by hour (a heating setback, e.g. BESTEST case 640); None: constant
FAN_CONT_KW = 0.0    # indoor-fan power of a unit that keeps its fan running while the compressor is off (0: fan auto)
OCC_AVAIL = None     # None: every conditioned room is conditioned at all hours. A (24, NZ) boolean array switches units off
                     # (and excludes the room from the comfort count) in the hours it is False
OCC_COUNT = None     # None: comfort counted whenever the unit is available; else a (24, NZ) array of occupied hours
SP_HOURLY = None     # None, or a (24,) array of cooling-setpoint offsets [K] applied in the cooling months (a setback profile)
SEASONAL_CHANGEOVER = True   # False: automatic heating/cooling in every month (used for the BESTEST-style comparison)
FLOOR_GUARD_SP = _th.get("floor_guard_setpoint_C")   # low-limit heating setpoint in the months without automatic heating
                     # (switch-on at FLOOR_GUARD_SP - HYST); None: no heating outside the heating months
GROUND = "iso13370"  # "iso13370": ISO 13370 slab transmittance and monthly ground temperature; "constant": U_GRND to Tg
MOISTURE = None      # None: latent load priced at COP_LAT with the indoor target assumed met (the model of the paper).
                     # dict(SHR=0.75, internal=True) adds a diagnostic humidity-ratio state per zone: infiltration and
                     # internal moisture in, removal by the coil at the sensible heat ratio SHR while the unit runs;
                     # reports the hours above 60 % RH and the share of the latent load the coil removes (energy unchanged)

def set_scope(scope="building", floors=None):
    """Rebind the module-level model (used by simulate) to a scope and, for 'building', a number of storeys."""
    floors = _BLD["reference_floors"] if floors is None else floors
    globals().update(build(floors, scope)); globals()["SP_OFFSET"] = 0.0; globals()["HEAT_OFFSET"] = 0.0
    _equipment_defaults()
    fi = _BLD["representative_apartment"]["floor_index"] if scope == "building" else 0
    globals()["REP_METER"] = apartment_meter(min(fi, globals()["FLOORS"] - 1), _BLD["representative_apartment"]["side"])

def _equipment_defaults():
    """Per-zone equipment of the base case: compressor type from the catalogue (fixed-speed units are on/off), the
    cycling degradation coefficient of each type, and the two-node zone model."""
    cat = _BLD["catalogue_units"]; cd = _pl["cycling_Cd"]
    ton = QC / TON_KW
    fixed = np.array([cat.get(f"{t:.1f}", {}).get("compressor") == "fixed_speed" for t in ton]) & COND
    globals()["FIXED_SPEED"] = fixed
    globals()["ONOFF_ZONES"] = fixed.copy()
    globals()["CYCLING_CD"] = np.where(fixed, cd["fixed_speed"], cd["inverter"]) * COND
    globals()["TWO_NODE"] = dict(ZONE_MODEL)

def set_floors(floors):
    """Rebind the module-level building (used by simulate) to a building of `floors` storeys."""
    set_scope("building", floors)

def apartment_meter(floor_index, side="A"):
    return 2 * floor_index + (0 if side == "A" else 1)

set_scope("building")
APT_AREA = float(sum(sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in r["rects"]) for r in _BLD["apartment"]["rooms"] if r.get("floor_area", True)))   # floor area for EUI (enclosed balcony excluded)

def glazing():
    """Glazed fraction of each exterior face (zone x orientation): the drawing's windows, or, if WFRAC is set to a
    number, the drawing's windows scaled so that the building's window-to-wall ratio equals WFRAC."""
    return GLZ0_O if WFRAC is None else np.clip(GLZ0_O * (WFRAC / WWR_DRAWING), 0, 0.95)

def _Kenv(env):
    e = ENVELOPES[env]; Awin = (glazing() * A_EXT_O).sum(1)
    k_inf = ACH * AREA * H_AIR * _b["air_density"] * _b["air_cp_kJkgK"] / 3600   # sensible infiltration [kW/K]
    Kfab = (e['U_WALL'] * (A_EXT - Awin) + e['U_WIN'] * Awin) / 1000          # walls and windows [kW/K]
    return Kfab + is_roof * AREA * e['U_ROOF'] / 1000 + k_inf, Kfab, e['U_ROOF'] / 1000

def _latent_hourly(Wa):
    """Latent (dehumidification) electricity per reference-area conditioned zone [kW], hour by hour."""
    return AIR_MASS * ACH * np.clip(Wa - W_IN, 0.0, None) * HFG / 3600.0 / COP_LAT

def solar_drive(city, env):
    """Hourly solar and sky drives of every zone [kW] (sampled days of ambient): (S_opq, S_win, S_sky_win).
    S_opq: sol-air excess of the opaque walls and roof (absorbed solar minus the long-wave loss to the sky) through their
    U-values; S_win: solar through the windows (angle-dependent); S_sky_win: long-wave loss of the windows to the sky."""
    e = ENVELOPES[env]; Ta, moh, Wa, Iv, ghi = ambient(city); dR = sky_excess(city)
    G_O = glazing(); Awin = (G_O * A_EXT_O).sum(1)
    k_opq = e['U_WALL'] * (1 - G_O) * ALPHA / H_OUT / 1000
    k_win = e.get("SHGC", SHGC) * G_O / 1000
    UA_roof = is_roof * AREA * e['U_ROOF'] / 1000; UA_wall = e['U_WALL'] * (A_EXT - Awin) / 1000
    S_opq = (Iv @ (k_opq * A_EXT_O).T + np.outer(ALPHA * ghi / H_OUT, UA_roof)
             - np.outer(dR / H_OUT, _SKY["F_sky_roof"] * UA_roof + _SKY["F_sky_vertical"] * UA_wall))
    S_win = glazing_irradiance(city) @ (k_win * A_EXT_O).T
    S_sky_win = np.outer(dR / H_OUT, _SKY["F_sky_vertical"] * e['U_WIN'] * Awin / 1000)
    return S_opq, S_win, S_sky_win

def simulate_batch(city, env, policies, tariff='volume'):
    """Annual simulation of the current building (see set_scope) for several control policies at once (vectorised
    over policies; identical physics to a one-policy run).

    Zones: two nodes per zone in the base case (TWO_NODE): the air node, on which the thermostat acts, carries the
    windows, infiltration, open doors, the unit and the convective internal gains; the structure node carries the opaque
    walls and roof (sol-air), the ground slab, the partitions and floor slabs to neighbouring zones, the solar gain
    through the windows and the radiant internal gains. Each step is semi-implicit: every node is updated implicitly in
    its own conductances and explicitly in its neighbours' temperatures, which is unconditionally stable.
    Control: each unit is an on/off thermostat with a differential of 2 HYST about its setpoint; while on, an inverter
    unit escalates from the low to the medium and high modes as the room rises above its switch-on point, and a
    fixed-speed unit runs at full capacity. Cycling losses (CYCLING_CD), standby power and capacity derating are applied.
    Bills are computed per meter (one per apartment plus one common meter for the stair and lobby)."""
    pol = np.atleast_2d(np.asarray(policies, float)); P = len(pol)
    cool_sp, heat_sp, precool, prepeak = pol.T
    Ta, moh, Wa, Iv, ghi = ambient(city); Hn = len(Ta)
    if GROUND == "iso13370":
        Tg_m = ground_temperature(city); Ug = U_GRND_ISO
    else:
        Tg_m = np.full(12, CITIES[city]['Tg']); Ug = U_GRND
    lath = _latent_hourly(Wa)
    e = ENVELOPES[env]
    G_O = glazing(); Awin = (G_O * A_EXT_O).sum(1)
    k_inf = ACH * AREA * H_AIR * _b["air_density"] * _b["air_cp_kJkgK"] / 3600   # sensible infiltration [kW/K]
    K_win = e['U_WIN'] * Awin / 1000 + k_inf                                      # air-node exterior conductance
    K_opq = e['U_WALL'] * (A_EXT - Awin) / 1000 + is_roof * AREA * e['U_ROOF'] / 1000   # opaque walls and roof
    S_opq, S_win, S_skw = solar_drive(city, env)
    Kgr = is_grnd * Ug * AREA / 1000
    gain_w = GAINF * AREA / A_REF
    Mhot = np.eye(NMET)[METER]                      # (NZ, NMET) meter incidence
    units_m = COND.astype(float) @ Mhot             # units per meter
    two = bool(TWO_NODE)
    if two:
        a_ = TWO_NODE["air_fraction"]; C_air = a_ * CZ; C_mas = (1 - a_) * CZ
        K_am = TWO_NODE["k_am_W_m2K"] * AREA / 1000; f_rad = TWO_NODE.get("radiant_fraction", 0.5)
        Dm = K_opq + Kgr + Krow + K_am                                              # structure-node own conductance
    else:
        C_air = CZ; f_rad = 0.0
    cdz = CYCLING_CD if CYCLING_CD is not None else None
    Cd = None if cdz is None or (np.ndim(cdz) == 0 and not cdz) else np.broadcast_to(np.asarray(cdz, float), (NZ,))
    if RECORD_HOURLY:
        Vh = np.zeros((P, Hn, NZ)); Wh = np.zeros((P, Hn, NZ))
    if LAT_LOWMODE_FACTOR:
        ncm = np.maximum(units_m, 1)
    if MOISTURE:   # ASHRAE Fundamentals (residential): internal latent gain 20 + 0.22 A + 12 N_oc W per dwelling
        Hm = np.full((P, NZ), W_IN); m_air = AIR_MASS * AREA / A_REF; m_inf = m_air * ACH / 3600.0   # kg, kg/s
        g_lat = np.zeros(NZ)
        if MOISTURE.get("internal", True):
            for mt in range(NMET):
                zz = (METER == mt) & COND
                if zz.any() and mt < NMET - 1:
                    a2 = AREA[zz].sum(); g_lat[zz] = (20 + 0.22 * a2 + 12 * MOISTURE.get("occupants", 5)) / 1000 * AREA[zz] / a2   # kW
        rh60 = np.zeros((P, NZ)); lat_need = np.zeros(P); lat_coil = np.zeros(P); nmh = 0
    ez = np.zeros((P, NZ)); onc = np.zeros((P, NZ)); qs_h = np.zeros((P, NZ)); qhmax = np.zeros((P, NZ)); qh_h = np.zeros((P, NZ)); qhhmax = np.zeros((P, NZ)); qheat = np.zeros(P)
    V = np.full((P, NZ), V_INIT); W = V.copy(); em = np.zeros((P, NMET, 12)); epk = np.zeros((P, NMET, 12)); em_lat = np.zeros((P, NMET, 12)); ecool = np.zeros(P); qcool = np.zeros(P); qzone = np.zeros((P, NZ))
    on_c = np.zeros((P, NZ), bool); on_h = np.zeros((P, NZ), bool)
    min_off = max(int(round(MIN_OFF_MIN / 60 / DT)), 0); offc = np.full((P, NZ), 10 ** 6); starts = np.zeros((P, NZ))
    min_on = max(int(round(MIN_ON_MIN / 60 / DT)), 0); ontc = np.zeros((P, NZ))
    tau_h = None if START_TAU_MIN is None else np.asarray(START_TAU_MIN, float) / 60.0
    cviol = np.zeros(P); sviol = np.zeros(P); svcold = np.zeros(P); chot = np.zeros(P)
    zhot = np.zeros((P, NZ)); zcold = np.zeros((P, NZ)); nhot = np.zeros((P, NZ)); ncold = np.zeros((P, NZ))   # per-zone band excursions
    ezone = np.zeros((P, NZ))                            # annual compressor electricity of each unit (cycling loss included) [kWh]
    util = np.zeros((P, NZ)); peakfr = np.zeros((P, NZ)); nst = 0; peak_kw = np.zeros(P); peak_h = np.zeros(P)
    Vsum = np.zeros((P, NZ)); Vmax = np.full((P, NZ), -99.0); Vmin = np.full((P, NZ), 99.0); Vm = np.zeros((P, 12, NZ)); nm = np.zeros(12)
    ecm = np.zeros((P, 12)); ehm = np.zeros((P, 12)); mcnt = np.zeros((P, 12, 7)); est = np.zeros(P)
    Qd = np.zeros((P, 12, DOORS.shape[1]))             # monthly mean heat flow through each door, a -> b [kW]
    ph = _sch["gain_solar_phase_hr"]; ppk = _sch["prepeak_hours"]
    hsp = heat_sp[:, None] + HEAT_OFFSET
    # seasonal changeover: automatic heating/cooling only in months whose mean air temperature is below the band floor;
    # in the other months the units run in cooling mode, and only there do the pre-cooling features act
    HEAT_MONTH = np.array([(Ta[moh == k].mean() < COMFORT[0] or not SEASONAL_CHANGEOVER) if (moh == k).any() else False for k in range(12)])
    absD = np.abs(DOORS)
    for h in range(Hn):
        hr = h % 24; Tah = Ta[h]; m = int(moh[h]); cf = cop_factor(Tah); capf = cap_factor(Tah) if CAP_DERATE else 1.0
        s_ = max(np.sin((hr - ph) / 24 * 2 * np.pi), 0)
        gains = (GAINb + GAINd * s_) * gain_w
        if two:
            b_air = K_win * Tah - S_skw[h] + (1 - f_rad) * gains
            b_mas = K_opq * Tah + S_opq[h] + Kgr * Tg_m[m] + S_win[h] + f_rad * gains
        else:
            b_air = (K_win + K_opq) * Tah + S_opq[h] + S_win[h] - S_skw[h] + Kgr * Tg_m[m] + gains
            D1 = K_win + K_opq + Kgr + Krow
        if LAT_LOWMODE_FACTOR: hi_cnt = np.zeros((P, NMET))
        cool = cool_sp.copy(); hm_on = HEAT_MONTH[m]
        if not hm_on:
            if hr >= _sch["precool_night_start"] or hr < _sch["precool_morning_end"]:
                cool = np.where(precool > 0, np.maximum(SAFE[0] + HYST, cool_sp - precool), cool)
            if ppk[0] <= hr < ppk[1]: cool = np.where(prepeak > 0, np.maximum(SAFE[0] + HYST, cool_sp - prepeak), cool)
            if SP_HOURLY is not None: cool = np.maximum(SAFE[0] + HYST, cool + SP_HOURLY[hr])
        csp = (cool[:, None] if PRECOOL_ZONES is None else np.where(PRECOOL_ZONES, cool[:, None], cool_sp[:, None])) + SP_OFFSET
        hsp_x = hsp if HEAT_HOURLY is None else hsp + HEAT_HOURLY[hr]
        if hm_on: hsp_h = np.minimum(hsp_x, csp - DEADBAND)
        elif FLOOR_GUARD_SP is not None: hsp_h = np.minimum(FLOOR_GUARD_SP, csp - DEADBAND)   # floor guard (low-limit heating)
        else: hsp_h = np.full_like(csp, -99.0)
        avail = COND if OCC_AVAIL is None else (COND & OCC_AVAIL[hr])
        occ_h = avail if OCC_COUNT is None else (COND & OCC_COUNT[hr])
        ek = np.zeros((P, NMET)); ek += STANDBY_KW * units_m                      # standby power for the hour
        kdh = K_DOOR_NIGHT if (K_DOOR_NIGHT is not None and (hr >= DOOR_NIGHT_HOURS[0] or hr < DOOR_NIGHT_HOURS[1])) else K_DOOR
        ez[:] = 0; onc[:] = 0; qs_h[:] = 0; qh_h[:] = 0
        for _ in range(SUB):
            # door exchange: conductance from the current temperature difference (implicit in the zone's own term)
            if DOORS.shape[1]:
                dTd = V @ DOORS
                g = kdh * np.sqrt(np.abs(dTd))                   # (P, doors) [kW/K]
                qd = g * dTd; Qd[:, m] += qd
                Gd = g @ absD.T                                  # (P, NZ) total door conductance of each zone
                inflow = Gd * V - qd @ DOORS.T                   # sum over doors of g * V_neighbour
            else:
                Gd = 0.0; inflow = 0.0
            if two:
                den = C_air / DT + K_win + K_am + Gd; V0 = (C_air / DT * V + b_air + inflow + K_am * W) / den
            else:
                den = C_air / DT + D1 + Gd; V0 = (C_air / DT * V + b_air + inflow + V @ Kc) / den
            # thermostat with hysteresis, resolved within the step: a unit switches on when the room would pass its
            # upper switching point (csp + HYST) and off when it reaches its lower one (csp - HYST), running for the
            # fraction phi of the step that brings the room exactly there (HYST = 0: switch at the setpoint)
            up, lo = csp + HYST, csp - HYST
            hlo, hup = hsp_h - HYST, hsp_h + HYST
            prev_c, prev_h = on_c, on_h
            ready = offc >= min_off                              # compressor restart delay (anti-short-cycle timer)
            hold = ontc < min_on                                 # minimum on-time not yet reached: the unit keeps running
            on_c = np.where(prev_c, (V > lo) | hold, (V0 > up) & ready) & avail
            on_h = np.where(prev_h, (V < hup) | hold, (V0 < hlo) & ready) & avail & ~on_c
            new_st = (on_c & ~prev_c) | (on_h & ~prev_h); starts += new_st
            mag = np.where(on_c, V0 - up, np.where(on_h, hlo - V0, 0.0))
            mode = np.where(on_c | on_h, 1 + (mag >= _MODE[0]) + (mag >= _MODE[1]), 0)
            if ESC_KICK:                                         # the room is already past its switching point: full capacity
                kick = (on_c & prev_c & (V > up + 1e-9)) | (on_h & prev_h & (V < hlo - 1e-9))
                mode = np.where(kick, 3, mode)
            if START_FULL: mode = np.where(new_st, 3, mode)       # a unit that starts runs its first step at full capacity
            if min_on > 0:                                       # held on by the minimum on-time: an inverter unit modulates down
                fr0 = FR[mode]; full0 = np.where(on_c, -QC * capf * fr0, np.where(on_h, QH * fr0, 0.0)) / den
                over = hold & ((prev_c & on_c & (V0 + full0 < lo)) | (prev_h & on_h & (V0 + full0 > hup)))
                mode = np.where(over, 1, mode)
            gin = cop_indoor(V)                                  # indoor-temperature factor of the cooling COP (air at the start of the step)
            if ONOFF_ZONES is not None: mode = np.where(ONOFF_ZONES & (mode > 0), 3, mode)
            fr = FR[mode]
            qc_ = QC * capf
            full = np.where(on_c, -qc_ * fr, np.where(on_h, QH * fr, 0.0)) / den
            with np.errstate(divide="ignore", invalid="ignore"):
                phc = np.where(prev_c, np.where((V0 + full < lo) & ~hold, (lo - V0) / full, 1.0), (up - V0) / full)
                phh = np.where(prev_h, np.where((V0 + full > hup) & ~hold, (hup - V0) / full, 1.0), (hlo - V0) / full)
            phi = np.nan_to_num(np.clip(np.where(on_c, phc, np.where(on_h, phh, 0.0)), 0.0, 1.0))
            V = V0 + phi * full
            if two:
                W = (C_mas / DT * W + b_mas + W @ Kc + K_am * V) / (C_mas / DT + Dm)
            on_c = on_c & ~(prev_c & (V0 + full < lo) & ~hold); on_h = on_h & ~(prev_h & (V0 + full > hup) & ~hold)
            offc = np.where(on_c | on_h, 0, offc + 1); ontc = np.where(on_c | on_h, ontc + 1, 0)
            frp = fr * phi                                       # delivered fraction of capacity over the step
            if LAT_LOWMODE_FACTOR: hi_cnt += ((mode >= 2) & COND) @ Mhot
            act = phi > 0
            cm = act & (mode > 0) & (full < 0); hm = act & (mode > 0) & (full > 0)
            with np.errstate(invalid="ignore", divide="ignore"):
                copc = np.where(hm, HEATc[mode], COOLc[mode] * cf * gin)
                pz = np.where(act, np.where(cm, qc_, QH) * frp / np.where(act, copc, 1.0), 0.0)
            if FAN_CONT_KW and not hm_on:
                ek += ((~act) & avail).astype(float) @ Mhot * FAN_CONT_KW * DT
            if tau_h is not None:                                # start-based cycling loss: full power of the starting mode for tau
                with np.errstate(invalid="ignore", divide="ignore"):
                    pst = np.where(new_st & (mode > 0), np.where(cm, qc_, QH) * FR[mode] / np.where(act, copc, 1.0), 0.0) * tau_h
                pst = np.nan_to_num(pst); ek += pst @ Mhot; ez += pst
                ecool += np.where(cm, pst, 0.0).sum(1) * MSCALE[m]; ecm[:, m] += np.where(cm, pst, 0.0).sum(1) * MSCALE[m]
                ehm[:, m] += np.where(hm, pst, 0.0).sum(1) * MSCALE[m]
            ek += (pz @ Mhot) * DT
            ez += pz * DT; onc += phi * (mode > 0); qs_h += np.where(cm, qc_ * frp, 0.0) * DT; qh_h += np.where(hm, QH * frp, 0.0) * DT
            qheat += np.where(hm, QH * frp, 0.0).sum(1) * DT * MSCALE[m]
            ecool += np.where(cm, pz, 0.0).sum(1) * DT * MSCALE[m]
            ecm[:, m] += np.where(cm, pz, 0.0).sum(1) * DT * MSCALE[m]; ehm[:, m] += np.where(hm, pz, 0.0).sum(1) * DT * MSCALE[m]
            if TRACK_MODES:
                mi = np.where(cm, mode, np.where(hm, mode + 3, 0))[:, COND]
                for k in range(7): mcnt[:, m, k] += (mi == k).sum(1)
            qcool += np.where(cm, qc_ * frp, 0.0).sum(1) * DT * MSCALE[m]     # delivered sensible cooling
            qzone += np.where(cm, qc_ * frp, 0.0) * DT * MSCALE[m]
            peak_kw = np.maximum(peak_kw, pz.sum(1))
            util += frp; peakfr = np.maximum(peakfr, fr); nst += 1
            Vsum += V; Vmax = np.maximum(Vmax, V); Vmin = np.minimum(Vmin, V); Vm[:, m] += V; nm[m] += 1
            zo = np.clip(V - COMFORT[1], 0, None); zu = np.clip(COMFORT[0] - V, 0, None)
            zhot += zo * DT; zcold += zu * DT; nhot += zo > 0; ncold += zu > 0
            Va = np.where(occ_h, V, np.nan)
            with np.errstate(invalid="ignore"):
                hot = np.nansum(np.clip(Va - COMFORT[1], 0, None), 1)
                cold = np.nansum(np.clip(COMFORT[0] - Va, 0, None), 1)
                svc = np.nansum(np.clip(SAFE[0] - Va, 0, None), 1)
                sv = np.nansum(np.clip(Va - SAFE[1], 0, None), 1) + svc
            chot += hot * DT; cviol += (hot + cold) * DT; sviol += sv * DT; svcold += svc * DT
        if Cd is not None:                               # part-load factor of each unit over the hour
            rtf = onc / SUB; plf = 1.0 - Cd * (1.0 - rtf)
            extra = np.where(onc > 0, ez * (1.0 / plf - 1.0), 0.0)
            ek += extra @ Mhot; sc0 = MSCALE[m]; ez = ez + extra
            if hm_on: ehm[:, m] += extra.sum(1) * sc0
            else:                                        # cooling months: a unit that heated this hour ran its floor guard
                xh = np.where(qh_h > 0, extra, 0.0).sum(1); xc = extra.sum(1) - xh
                ehm[:, m] += xh * sc0; ecm[:, m] += xc * sc0; ecool += xc * sc0
        ezone += ez * MSCALE[m]
        qhhmax = np.maximum(qhhmax, qh_h); qhmax = np.maximum(qhmax, qs_h)   # largest hourly mean sensible cooling of each unit [kW]
        peak_h = np.maximum(peak_h, ez.sum(1))          # largest hourly mean electrical power of all units [kW]
        if MOISTURE and not hm_on:                       # diagnostic humidity balance over the hour (cooling months)
            shr = MOISTURE.get("SHR", 0.75)
            need = m_inf * np.clip(Wa[h] - Hm, 0, None) * HFG + g_lat
            coil = qs_h * (1 - shr) / shr
            Hm = Hm + (m_inf * (Wa[h] - Hm) * 3600 + (g_lat - coil) * 3600 / HFG) / m_air
            Hm = np.maximum(Hm, 0.004)
            pv = Hm * 101.325 / (0.622 + Hm); rh = pv / Psat(V)
            rh60 += (rh > 0.60) & COND; nmh += 1
            lat_need += (need * COND).sum(1); lat_coil += (np.minimum(coil, need) * COND).sum(1)
        lat = lath[h] * LATW
        if LAT_LOWMODE_FACTOR:
            share_hi = hi_cnt / (SUB * ncm)
            lat = lat * (share_hi + (1 - share_hi) / LAT_LOWMODE_FACTOR)
        if RECORD_HOURLY:
            Vh[:, h] = V; Wh[:, h] = W if two else V
        sc = MSCALE[m]
        est += STANDBY_KW * units_m.sum() * sc
        em[:, :, m] += ek * sc; em_lat[:, :, m] += lat * sc
        if (not hm_on) and TOU_PEAK_HOURS[0] <= hr < TOU_PEAK_HOURS[1]: epk[:, :, m] += (ek + lat) * sc
    tot = em + em_lat                                   # (policy, meter, month)
    _v = _P["tariff"]["volume"]
    if tariff == 'volume':
        mb = ((_v["tier1_price"] * np.minimum(tot, _v["threshold_kwh"])
               + _v["tier2_price"] * np.maximum(tot - _v["threshold_kwh"], 0)).sum(2)) * _v["vat"]
    else:
        raise ValueError("only the Saudi volume tariff is modelled")
    ms = MSCALE[moh].mean(); n = max(nst, 1)
    return [dict(bill=mb[p].sum(), kwh=tot[p].sum(), kwh_m=tot[p].sum(0), kwh_sens=em[p].sum(),
                 kwh_lat=em_lat[p].sum(), kwh_standby=est[p], starts_zone=starts[p] * ms, starts_per_unit_h=(starts[p][COND].sum() / max(COND.sum(), 1)) / (n * DT), kwh_cool=ecool[p], kwh_cool_m=ecm[p], kwh_heat_m=ehm[p], mode_counts_m=mcnt[p], cool_th=qcool[p], heat_th=qheat[p], cool_th_zone=qzone[p], kwh_zone=ezone[p], meter_kwh=tot[p].sum(1), meter_kwh_m=tot[p], meter_kwh_peak_m=epk[p], meter_bill=mb[p],
                 cviol=cviol[p] * ms, cviol_hot=chot[p] * ms, sviol=sviol[p] * ms, sviol_cold=svcold[p] * ms,
                 Vmean=Vsum[p] / n, Vmax=Vmax[p], Vmin=Vmin[p], Vmean_m=Vm[p] / np.maximum(nm, 1)[:, None], door_q_m=Qd[p] / np.maximum(nm, 1)[:, None],
                 util=util[p] / n, peakfr=peakfr[p], peak_kw=peak_kw[p], peak_hourly_kw=peak_h[p], q_hour_max=qhmax[p], qh_hour_max=qhhmax[p],
                 zone_hot_Kh=zhot[p] * ms, zone_cold_Kh=zcold[p] * ms, zone_hot_frac=nhot[p] / n, zone_cold_frac=ncold[p] / n,
                 **(dict(V_hourly=Vh[p], W_hourly=Wh[p]) if RECORD_HOURLY else {}),
                 **(dict(rh60_frac=rh60[p] / max(nmh, 1), latent_coil_share=lat_coil[p] / max(lat_need[p], 1e-9)) if MOISTURE else {})) for p in range(P)]


OCCUPANCY = {   # occupied hours [start, end) of each conditioned room type (assumed household routine, see the paper)
    "master_bedroom": [(21, 24), (0, 8)], "bedroom_2": [(21, 24), (0, 8)], "bedroom_3": [(21, 24), (0, 8)],
    "living": [(13, 24)], "living_open": [(13, 24)], "kitchen": [(11, 14), (18, 21)], "guest_room": [(18, 23)],
    "stair": [(0, 24)]}

def occupancy(lead_h=0):
    """(24, NZ) boolean arrays (units on, rooms occupied) for the occupancy routine OCCUPANCY; each unit starts
    lead_h hours before its room is occupied (a fixed optimal-start lead)."""
    occ = np.zeros((24, NZ), bool)
    for z, r in enumerate(ROOM):
        for a, b in OCCUPANCY.get(r, []):
            occ[a:b, z] = True
    on = occ.copy()
    for k in range(1, lead_h + 1):
        on |= np.roll(occ, -k, axis=0)
    return on & COND, occ & COND

def staircase(depth, start, end, step=None):
    """(24,) setpoint offsets of a setback of `depth` K that begins at hour `start` and returns to the setpoint in
    equal thermostat steps by hour `end` (each step SP_STEP)."""
    step = SP_STEP if step is None else step; n = max(int(round(depth / step)), 1); hrs = (end - start) % 24
    off = np.zeros(24)
    for k in range(hrs):
        lvl = depth - step * np.floor(k * n / hrs)
        off[(start + k) % 24] = -max(lvl, 0.0)
    return off

def right_size(city, env="compliant", margin=2.0, step_ton=0.25, min_ton=0.75, keep_fixed=False):
    """Replace the catalogue units of the current scope by units sized to the zone's largest hourly sensible cooling
    load at the reference setpoint (times `margin`, divided by the capacity derating at the hottest hour), rounded up
    to `step_ton` with a minimum of `min_ton`. With keep_fixed=False every right-sized unit is an inverter unit; with
    keep_fixed=True the zones of the fixed-speed units keep a fixed-speed on/off unit of the new size, so that the
    effect of size can be separated from that of the compressor type. Returns the old QC/QH."""
    old = dict(QC=QC.copy(), QH=QH.copy())
    r = simulate(city, env, CODE)
    Ta = ambient(city)[0]; capf = cap_factor(Ta.max()) if CAP_DERATE else 1.0
    need = r["q_hour_max"] * margin / capf / TON_KW
    ton = np.where(COND, np.maximum(min_ton, np.ceil(need / step_ton) * step_ton), 0.0)
    globals()["QC"] = ton * TON_KW; globals()["QH"] = ton * TON_KW * (Q_HEAT / Q_COOL)
    if not keep_fixed:
        fixed = np.zeros(NZ, bool); globals()["FIXED_SPEED"] = fixed; globals()["ONOFF_ZONES"] = fixed
        globals()["CYCLING_CD"] = np.where(COND, _pl["cycling_Cd"]["inverter"], 0.0)
    return old

def simulate(city, env, policy, tariff='volume'):
    """One-policy run (thin wrapper: the batched engine is the single source of truth)."""
    return simulate_batch(city, env, [policy], tariff)[0]

# baselines (from config)
CURRENT = list(_P["policies"]["CURRENT"])
CODE = list(_P["policies"]["CODE"])

if __name__ == '__main__':
    for env in ['compliant', 'noncompliant']:
        for city in ['Riyadh', 'Jeddah']:
            cur = simulate(city, env, CURRENT); cod = simulate(city, env, CODE)
            print('%-13s %-7s bldg setpoint_saving=%.1f%%  bldg EUI=%.0f  apt EUI=%.0f' % (
                env, city, (1 - cod['bill'] / cur['bill']) * 100, cod['kwh'] / FLOOR_AREA / 0.70,
                cod['meter_kwh'][REP_METER] / APT_AREA / 0.70))
