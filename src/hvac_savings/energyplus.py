"""
energyplus.py
-------------
EnergyPlus inputs and runner for the like-for-like comparison (comparative.py): the complex as one well-mixed zone
per storey (main body of the plan, the drawing's window-to-wall ratio on every face), with the envelope, infiltration,
internal gains, capacitance and ground (ISO 13370 slab, monthly EPW ground temperature) of model.py, under ideal loads
at the reference set points.

Requires EnergyPlus (tested with v26.1). Set the install path via ENERGYPLUS_DIR, e.g.
    export ENERGYPLUS_DIR=/opt/EnergyPlus-26.1.0
    python -m hvac_savings.comparative
Download EnergyPlus: https://github.com/NREL/EnergyPlus/releases
"""
import subprocess, os, math, json
from pathlib import Path
from hvac_savings import model as M
ROOT=Path(__file__).resolve().parents[2]
EPDIR=os.environ.get("ENERGYPLUS_DIR","/opt/EnergyPlus-26.1.0")
EP=os.path.join(EPDIR,"energyplus")
WXDIR=os.path.join(ROOT,"experiments","weather")
WX={"Riyadh":os.path.join(WXDIR,"SAU_RI_Riyadh.AB.404380_TMYx.epw"),
    "Jeddah":os.path.join(WXDIR,"SAU_MK_Jeddah-Abdulaziz.Intl.AP.410240_TMYx.epw")}
# All envelope, window, infiltration and gain inputs are read from the SAME configs as model.py.
ENV={k: dict(Uw=v["U_WALL"], Ur=v["U_ROOF"], Uwin=v["U_WIN"], shgc=v.get("SHGC", M.SHGC)) for k, v in M.ENVELOPES.items()}
LX=float(M._BLD['plan']['main_body_m'][0]); LY=float(M._BLD['plan']['main_body_m'][1]); HF=M.H_FLR; WWR=M.WFRAC if M.WFRAC is not None else M.WWR_DRAWING; RF=0.17   # footprint of building.json; film R for U-correction
def rmat(U):  # NoMass resistance so U_eff (incl films) ~ target
    return max(1.0/U - RF, 0.02)
def wall_verts(P1,P2,z0):
    (x1,y1),(x2,y2)=P1,P2
    return [(x1,y1,z0+HF),(x1,y1,z0),(x2,y2,z0),(x2,y2,z0+HF)]
def win_verts(P1,P2,frac,z0):
    (x1,y1),(x2,y2)=P1,P2
    dx,dy=x2-x1,y2-y1; ln=math.hypot(dx,dy); ux,uy=dx/ln,dy/ln; m=0.5
    wln=ln-2*m; hh=frac*ln*HF/wln; zc=z0+HF/2; zb,zt=zc-hh/2,zc+hh/2
    a=(x1+ux*m,y1+uy*m); b=(x2-ux*m,y2-uy*m)
    return [(a[0],a[1],zt),(a[0],a[1],zb),(b[0],b[1],zb),(b[0],b[1],zt)]
def vtx(vs): return ",\n    ".join("%.4f,%.4f,%.4f"%(x,y,z) for x,y,z in vs)+";"
CITY='Riyadh'
def compact(name, by_month):
    """Schedule:Compact text from {month (1-12): 24 hourly values}; equal consecutive hours are merged."""
    import calendar
    lines = [f"Schedule:Compact, {name}, Temp,"]
    for m in range(1, 13):
        v = by_month[m]; lines.append(f"    Through: {m}/{calendar.monthrange(2021, m)[1]}, For: AllDays,")
        h = 0
        while h < 24:
            k = h
            while k + 1 < 24 and abs(v[k + 1] - v[h]) < 1e-9: k += 1
            lines.append(f"    Until: {k + 1:02d}:00, {v[h]:.2f},"); h = k + 1
    return "\n".join(lines).rstrip(",") + ";"


def idf(env, floors, setp=None):
    """setp: None (constant setpoints of the reference policy) or (cool_by_month, heat_by_month) hourly setpoints."""
    """One well-mixed thermal zone per storey (27 m x 12 m x floor height), stacked; slab-coupled."""
    e=ENV[env]; corners=[(0,0),(LX,0),(LX,LY),(0,LY)]
    # internal gains per storey = the model's per-storey totals (same W/m2 and room gain factors)
    npf=M.NPF; gsum=float((M.GAINF[:npf]*M.AREA[:npf]).sum()/M.A_REF)
    base_w=M.GAINb*1000*gsum; diu_w=M.GAINd*1000*gsum
    # internal thermal mass matched to the room model: C_ref [kWh/K] per A_REF [m2] of floor, as 0.2 m concrete
    c_kJ=float(M.CZ[:npf].sum())*3600.0; mass_area=c_kJ/(0.2*2300*0.88)
    geo=""; zones=""; hvac=""
    for k in range(floors):
        z0=k*HF; Z=f"F{k}"
        zones+=f"Zone, {Z}, 0,0,0,0, 1, 1, {HF}, {LX*LY*M.H_AIR:.1f};\n"   # air volume at the clear height (as model.py)

        for i in range(4):
            P1=corners[i]; P2=corners[(i+1)%4]
            geo+=f"BuildingSurface:Detailed, {Z}W{i}, Wall, EXTWALL, {Z}, , Outdoors, , SunExposed, WindExposed, 0.5, 4,\n    {vtx(wall_verts(P1,P2,z0))}\n"
            geo+=f"FenestrationSurface:Detailed, {Z}G{i}, Window, GLAZING, {Z}W{i}, , , , , 4,\n    {vtx(win_verts(P1,P2,WWR,z0))}\n"
        fl=[(LX,0,z0),(0,0,z0),(0,LY,z0),(LX,LY,z0)]; ce=[(0,LY,z0+HF),(0,0,z0+HF),(LX,0,z0+HF),(LX,LY,z0+HF)]
        if k==0: geo+=f"BuildingSurface:Detailed, {Z}Flr, Floor, GRNDFLOOR, {Z}, , Ground, , NoSun, NoWind, 1.0, 4,\n    {vtx(fl)}\n"
        else:    geo+=f"BuildingSurface:Detailed, {Z}Flr, Floor, SLAB, {Z}, , Surface, F{k-1}Ceil, NoSun, NoWind, 0, 4,\n    {vtx(fl)}\n"
        if k==floors-1: geo+=f"BuildingSurface:Detailed, {Z}Rf, Roof, ROOF, {Z}, , Outdoors, , SunExposed, WindExposed, 0, 4,\n    {vtx(ce)}\n"
        else:           geo+=f"BuildingSurface:Detailed, {Z}Ceil, Ceiling, SLAB, {Z}, , Surface, F{k+1}Flr, NoSun, NoWind, 0, 4,\n    {vtx(ce)}\n"
        hvac+=f"""ZoneInfiltration:DesignFlowRate, {Z}Inf, {Z}, Always1, AirChanges/Hour, , , , {M.ACH}, , , , ;
InternalMass, {Z}Mass, MASS, {Z}, , {mass_area:.1f};
ElectricEquipment, {Z}Base, {Z}, Always1, EquipmentLevel, {base_w:.0f}, , , 0.0, {M.ZONE_MODEL['radiant_fraction']}, 0.0, ;
ElectricEquipment, {Z}Diu, {Z}, DiurnalYr, EquipmentLevel, {diu_w:.0f}, , , 0.0, {M.ZONE_MODEL['radiant_fraction']}, 0.0, ;
ZoneControl:Thermostat, {Z}Th, {Z}, CtrlType, ThermostatSetpoint:DualSetpoint, DualSP;
ZoneHVAC:IdealLoadsAirSystem, {Z}Ideal, , {Z}sup, , , 50, 13, 0.0156, 0.0077, NoLimit, , , NoLimit, , , , , ConstantSensibleHeatRatio, 1.0, None, , , , , ;
ZoneHVAC:EquipmentConnections, {Z}, {Z}eq, {Z}sup, , {Z}air, {Z}ret;
ZoneHVAC:EquipmentList, {Z}eq, SequentialLoad, ZoneHVAC:IdealLoadsAirSystem, {Z}Ideal, 1, 1, , ;
"""
    hrs=[max(math.sin((h-8)/24*2*math.pi),0.0) for h in range(24)]
    sched="Schedule:Day:Interval, DiurnalDay, Frac, No,\n"+"".join(f"    {h+1}:00, {hrs[h]:.3f},\n" for h in range(24))
    sched=sched.rstrip(",\n")+";"
    setpoints=(f"Schedule:Constant, CoolSet, Temp, {M.CODE[0]};\nSchedule:Constant, HeatSet, Temp, {M.CODE[1]};" if setp is None
               else compact("CoolSet", setp[0]) + "\n" + compact("HeatSet", setp[1]))
    return f"""
Version, 26.1;
Timestep, 6;
Building, ApartmentBlock, 0, City, 0.04, 0.4, FullExterior, 25, 6;
SimulationControl, No, No, No, Yes, Yes, No, 1;
RunPeriod, Year, 1, 1, , 12, 31, , , , , , , ;
GlobalGeometryRules, UpperLeftCorner, CounterClockWise, World;
Site:GroundTemperature:BuildingSurface, {', '.join(f'{t:.2f}' for t in M.ground_temperature(CITY))};
ScheduleTypeLimits, Frac, 0, 1, Continuous;
ScheduleTypeLimits, Temp, -60, 200, Continuous;
ScheduleTypeLimits, Any, , , Continuous;
Schedule:Constant, Always1, Frac, 1.0;
{sched}
Schedule:Week:Daily, DiurnalWk, DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay,DiurnalDay;
Schedule:Year, DiurnalYr, Frac, DiurnalWk, 1,1,12,31;
{setpoints}
Schedule:Constant, CtrlType, Any, 4;
Material:NoMass, MW, MediumRough, {rmat(e['Uw']):.4f}, 0.9, 0.7, 0.7;
Material:NoMass, MR, MediumRough, {rmat(e['Ur']):.4f}, 0.9, 0.7, 0.7;
Material:NoMass, MG, Rough, {rmat(M.U_GRND_ISO):.4f}, 0.9, 0.7, 0.7;
Material:NoMass, MS, Rough, {max(1.0/M.U_SLAB - 2*0.17, 0.02):.4f}, 0.9, 0.7, 0.7;
Construction, EXTWALL, MW;
Construction, ROOF, MR;
Construction, GRNDFLOOR, MG;
Construction, SLAB, MS;
Material, CONC, MediumRough, 0.2, 1.95, 2300, 880, 0.9, 0.7, 0.7;
Construction, MASS, CONC;
WindowMaterial:SimpleGlazingSystem, SG, {e['Uwin']:.3f}, {e['shgc']:.2f}, 0.7;
Construction, GLAZING, SG;
{zones}{geo}{hvac}
ThermostatSetpoint:DualSetpoint, DualSP, HeatSet, CoolSet;
Output:Variable, *, Zone Ideal Loads Supply Air Sensible Cooling Energy, RunPeriod;
Output:Variable, *, Zone Ideal Loads Supply Air Sensible Heating Energy, RunPeriod;
Output:Variable, *, Enclosure Windows Total Transmitted Solar Radiation Energy, RunPeriod;
Output:Variable, *, Zone Infiltration Sensible Heat Gain Energy, RunPeriod;
Output:Variable, *, Zone Infiltration Sensible Heat Loss Energy, RunPeriod;
Output:Variable, *, Zone Electric Equipment Total Heating Energy, RunPeriod;
OutputControl:Table:Style, Comma;
Output:Table:SummaryReports, AllSummary;
"""
PARSE_ONLY = False   # True: re-read the stored EnergyPlus outputs (no EnergyPlus install needed)
def parse_only():
    """Parse the stored outputs instead of running EnergyPlus: when asked to, or when EnergyPlus is not installed."""
    return PARSE_ONLY or not os.path.exists(EP)


def read_eso(path):
    """All series of an .eso file: {variable label: [values in report order]} (J for energies)."""
    lines = open(path).read().splitlines(); ids = {}
    k = next(i for i, l in enumerate(lines) if l.startswith("End of Data Dictionary"))
    for l in lines[:k]:
        q = l.split(",")
        if len(q) >= 4 and q[0].isdigit() and int(q[0]) > 6:
            ids[q[0]] = f"{q[2].strip()}:{q[3].split('[')[0].strip()}"
    out = {}
    for l in lines[k + 1:]:
        q = l.split(",")
        if q[0] in ids and len(q) >= 2:
            try: out.setdefault(ids[q[0]], []).append(float(q[1]))
            except ValueError: pass
    return out


def run_box(env, city, setp=None, suffix=""):
    """EnergyPlus storey box under ideal loads: delivered sensible cooling/heating [kWh_th per m2 of floor and year] and
    the annual heat-gain breakdown [kWh_th]."""
    global CITY; CITY = city
    tag = f"{env}_{city}{suffix}"; d = os.path.join(ROOT, "results", "energyplus", tag); os.makedirs(d, exist_ok=True)
    di = os.path.join(ROOT, "experiments", "energyplus", tag); os.makedirs(di, exist_ok=True)
    idfp = os.path.join(di, "in.idf"); text = idf(env, M.FLOORS, setp)
    if parse_only():
        if open(idfp).read() != text:
            raise RuntimeError(f"stored IDF for {tag} differs from the current configs; rerun EnergyPlus")
    else:
        open(idfp, "w").write(text)
        for fn in ("eplusout.eso", "eplusout.err"):
            if os.path.exists(os.path.join(d, fn)): os.remove(os.path.join(d, fn))
        subprocess.run([EP, "-w", WX[city], "-d", d, "-r", idfp], capture_output=True, text=True)
    if "Completed Successfully" not in open(os.path.join(d, "eplusout.err")).read():
        raise RuntimeError(f"EnergyPlus failed for {tag}; see {d}/eplusout.err")
    s = read_eso(os.path.join(d, "eplusout.eso")); kwh = lambda key: sum(sum(v) for k, v in s.items() if key in k) / 3.6e6
    area = LX * LY * M.FLOORS
    return dict(cool_th=round(kwh("Sensible Cooling") / area, 1), heat_th=round(kwh("Sensible Heating") / area, 1),
                gains=dict(window_solar=round(kwh("Transmitted Solar")), internal=round(kwh("Electric Equipment")),
                           infiltration_gain=round(kwh("Infiltration Sensible Heat Gain")),
                           infiltration_loss=round(kwh("Infiltration Sensible Heat Loss"))))
