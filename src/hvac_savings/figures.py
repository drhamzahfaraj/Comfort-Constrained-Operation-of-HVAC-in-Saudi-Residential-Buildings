#!/usr/bin/env python3
"""
figures.py -- regenerates every data-driven figure of the paper into figures/.
Reads results/results.json (run `hvac-reproduce` first), results/comparative.json (EnergyPlus
comparison, `hvac-benchmark`), and the geometry in experiments/configs/building.json.
fig_weather.png is computed from the TMYx weather files in experiments/weather.
"""
import os, json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from hvac_savings import model as M
ROOT = Path(__file__).resolve().parents[2]
FIG = str(ROOT / "figures"); os.makedirs(FIG, exist_ok=True)
RES = str(ROOT / "results")
C0, C1, C2, C3 = "#0072B2", "#E69F00", "#009E73", "#D55E00"
CITIES = ["Riyadh", "Jeddah"]
RJ = lambda: json.load(open(os.path.join(RES, "results.json")))
plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Liberation Sans", "Arial", "Helvetica", "DejaVu Sans"],
                     "font.size": 7.5, "axes.titlesize": 8, "axes.labelsize": 7.5, "legend.fontsize": 6.5,
                     "xtick.labelsize": 7, "ytick.labelsize": 7, "pdf.fonttype": 42, "ps.fonttype": 42})
TEXTW, COLW = 7.0, 3.4        # printed widths [in]: page width and column width of the two-column paper
def _clean(ax): ax.spines[["top", "right"]].set_visible(False)
def _save(name, rect=None):
    """Vector PDF (used by the paper, journal requirement) and a 600-dpi PNG preview, both at printed size."""
    plt.tight_layout(rect=rect) if rect else plt.tight_layout(); base = os.path.join(FIG, os.path.splitext(name)[0])
    plt.savefig(base + ".pdf"); plt.savefig(base + ".png", dpi=600); plt.close()


def fig_building():
    """(a) Typical-floor plan drawn from the model geometry (building.json); (b) section of the reference building."""
    B = json.load(open(ROOT / "experiments" / "configs" / "building.json")); sp, area, faces, _ = M._plan_geometry()
    W, Dp = B["plan"]["footprint_m"]
    fig, ax = plt.subplots(1, 2, figsize=(11, 7.2), gridspec_kw=dict(width_ratios=[1.45, 1]))
    a = ax[0]
    lab = dict(master_bedroom="Master\nbed", bedroom_2="Bed 2", bedroom_3="Bed 3", master_bath="Bath", bath_2="Bath",
               bath_3="Bath", bath_4="Bath", maid_room="Maid", corridor="", entrance_hall="Hall", living="Living",
               living_open="Living\n(open part)", guest_room="Guest\nroom", kitchen="Kitchen", vent_shaft="", shaft="", stair="Stair")
    for p in sp:
        core = p["side"] == "core"; void = p["name"] in ("vent_shaft", "shaft")
        fc = "#dddddd" if void else ("#dfe9f3" if core else ("#fdf1dc" if p["cond"] else "#ffffff"))
        for x0, y0, x1, y1 in p["rects"]:
            a.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fc=fc, ec="k", lw=0.9,
                                  hatch=None if (p["cond"] or void) else "///"))
        x0, y0, x1, y1 = max(p["rects"], key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        t = lab.get(p["name"], p["name"])
        if t: a.text((x0 + x1) / 2, (y0 + y1) / 2, t, ha="center", va="center",
                     bbox=None if (p["cond"] or core) else dict(fc="white", ec="none", pad=0.4))
    a.annotate("N", xy=(W + 0.9, Dp - 0.3), xytext=(W + 0.9, Dp - 2.6), ha="center",
               arrowprops=dict(arrowstyle="-|>", color="k"))
    a.set_xlim(-0.5, W + 1.8); a.set_ylim(-0.6, Dp + 0.4); a.set_aspect("equal"); a.axis("off")
    a.set_title(f"(a) Zone model of the typical floor ({W:g} m $\\times$ {Dp:g} m)")
    from matplotlib.patches import Patch
    a.legend(handles=[Patch(fc="#fdf1dc", ec="k", label="room with its own split unit"),
                      Patch(fc="white", ec="k", hatch="///", label="no unit (baths, maid room, corridor, hall)"),
                      Patch(fc="#dfe9f3", ec="k", label="conditioned stair (common meter)"),
                      Patch(fc="#dddddd", ec="k", label="ventilation shaft")], loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=2, frameon=False)
    b = ax[1]; F = B["reference_floors"]; hw = W / 2
    for k in range(F):
        for x0, w, t in ((0, hw - 1.2, "A"), (hw - 1.2, 2.4, ""), (hw + 1.2, hw - 1.2, "B")):
            b.add_patch(Rectangle((x0, k * 3), w, 3, fc="#fdf1dc" if t else "#dfe9f3", ec="k", lw=0.9))
            if t: b.text(x0 + w / 2, k * 3 + 1.5, f"Apt {k + 1}{t}", ha="center", va="center")
    b.plot([-1, W + 1], [0, 0], color="#8B5A2B", lw=3); b.text(hw, -0.9, "ground contact", ha="center")
    b.plot([0, W], [F * 3, F * 3], color=C3, lw=3); b.text(hw, F * 3 + 0.5, "roof", ha="center")
    b.text(W + 0.6, 1.5, "ground", va="center"); b.text(W + 0.6, F * 3 - 1.5, "top", va="center")
    b.text(W + 0.6, F * 1.5, "intermediate", va="center")
    b.set_xlim(-1.5, W + 7); b.set_ylim(-1.6, F * 3 + 1.4); b.set_aspect("equal"); b.axis("off")
    b.set_title(f"(b) Section: {F}-storey reference building")
    plt.savefig(os.path.join(FIG, "fig_building.png"), dpi=200, bbox_inches="tight"); plt.close()


def fig_layouts():
    """The building analysed in the three cases, drawn from the model geometry (experiments/configs/building.json), the same
    rectangles from which the simulator derives areas, shared walls and exterior faces: (a) Case 1, apartment A with
    its room sizes; (b) Case 2, the typical floor; (c) Case 3, a section of the four-storey building."""
    from matplotlib.patches import Patch
    B = json.load(open(ROOT / "experiments" / "configs" / "building.json")); sp, area, faces, _ = M._plan_geometry()
    W, Dp = B["plan"]["footprint_m"]; F = B["reference_floors"]; hw = W / 2
    short = dict(master_bedroom="Master bed", bedroom_2="Bed 2", bedroom_3="Bed 3", master_bath="Bath", bath_2="Bath",
                 bath_3="Bath", bath_4="Bath", maid_room="Maid", corridor="", entrance_hall="Hall", living="Living",
                 living_open="Living\n(open)", guest_room="Guest", kitchen="Kitchen", vent_shaft="", shaft="", stair="Stair")
    COND_C, CORE_C, VOID_C = "#fdf1dc", "#dfe9f3", "#d9d9d9"
    def draw(a, side_filter, dims, fs):
        for p in sp:
            if not side_filter(p): continue
            core = p["side"] == "core"; void = p["name"] in ("vent_shaft", "shaft")
            fc = VOID_C if void else (CORE_C if core else (COND_C if p["cond"] else "white"))
            for x0, y0, x1, y1 in p["rects"]:
                a.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fc=fc, ec="k", lw=0.6,
                                      hatch=None if (p["cond"] or void) else "////"))
            x0, y0, x1, y1 = max(p["rects"], key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
            t = short.get(p["name"], p["name"])
            if dims and t and len(p["rects"]) == 1 and not void:
                t += f"\n{x1 - x0:g}$\\times${y1 - y0:g}"
            if t:
                a.text((x0 + x1) / 2, (y0 + y1) / 2, t, ha="center", va="center", fontsize=fs, linespacing=1.0,
                       bbox=None if (p["cond"] or core) else dict(fc="white", ec="none", pad=0.15))
    fig, ax = plt.subplots(1, 3, figsize=(TEXTW, 4.3), gridspec_kw=dict(width_ratios=[1.0, 1.75, 1.45]))
    a = ax[0]; draw(a, lambda p: p["side"] == "A", True, 5.6)
    for p in sp:                                   # the core next to apartment A, clipped at the party line, for context
        if p["side"] != "core": continue
        for x0, y0, x1, y1 in p["rects"]:
            if x0 < hw: a.add_patch(Rectangle((x0, y0), min(x1, hw) - x0, y1 - y0, fc=VOID_C if p["name"] != "stair" else CORE_C,
                                              ec="0.5", lw=0.4, alpha=0.6))
    a.text(0.65, 13.0, "balcony", rotation=90, ha="center", va="center", fontsize=5, color="0.35")
    a.set_xlim(-0.3, hw + 0.3); a.set_ylim(-0.4, Dp + 0.4); a.set_aspect("equal"); a.axis("off")
    a.set_title("(a) Case 1: apartment A (m)", fontsize=7)
    b = ax[1]; draw(b, lambda p: True, False, 6.0)
    b.plot([hw, hw], [-0.4, Dp + 0.4], ls=":", color="0.4", lw=0.7)
    b.text(hw / 2, -0.9, "A (west)", ha="center", fontsize=6); b.text(hw * 1.5, -0.9, "B (east)", ha="center", fontsize=6)
    b.annotate("N", xy=(W + 0.9, Dp - 0.2), xytext=(W + 0.9, Dp - 3.0), ha="center", fontsize=6,
               arrowprops=dict(arrowstyle="-|>", color="k", lw=0.7))
    b.set_xlim(-0.3, W + 1.8); b.set_ylim(-1.5, Dp + 0.4); b.set_aspect("equal"); b.axis("off")
    b.set_title(f"(b) Case 2: typical floor ({W:g} m $\\times$ {Dp:g} m)", fontsize=7)
    c = ax[2]; hs_ = 3.0; cw = 2.4
    for k in range(F):
        for x0, w, t in ((0, hw - cw / 2, "A"), (hw - cw / 2, cw, ""), (hw + cw / 2, hw - cw / 2, "B")):
            rep = (t == B["representative_apartment"]["side"] and k == B["representative_apartment"]["floor_index"])
            c.add_patch(Rectangle((x0, k * hs_), w, hs_, fc=(COND_C if t else CORE_C), ec="k", lw=1.4 if rep else 0.6))
            if t: c.text(x0 + w / 2, k * hs_ + hs_ / 2, f"{k + 1}{t}", ha="center", va="center", fontsize=6,
                         fontweight="bold" if rep else "normal")
    c.plot([-0.8, W + 0.8], [0, 0], color="#8B5A2B", lw=2.5); c.text(hw, -1.0, "ground contact", ha="center", fontsize=6)
    c.plot([0, W], [F * hs_, F * hs_], color=C3, lw=2.5); c.text(hw, F * hs_ + 0.6, "roof", ha="center", fontsize=6)
    for y_, t in ((hs_ / 2, "ground"), (F * hs_ - hs_ / 2, "top"), (F * hs_ / 2, "intermediate")):
        c.text(W + 0.5, y_, t, va="center", fontsize=6)
    c.set_xlim(-1.0, W + 6.5); c.set_ylim(-2.0, F * hs_ + 1.5); c.set_aspect("equal"); c.axis("off")
    for x_ in ax: x_.set_anchor("N")
    c.set_title(f"(c) Case 3: {F}-storey building (section)", fontsize=7)
    fig.legend(handles=[Patch(fc=COND_C, ec="k", lw=0.6, label="room with its own split unit"),
                        Patch(fc="white", ec="k", lw=0.6, hatch="////", label="no unit (baths, maid room, corridor, hall)"),
                        Patch(fc=CORE_C, ec="k", lw=0.6, label="stair core (common meter)"),
                        Patch(fc=VOID_C, ec="k", lw=0.6, label="ventilation shaft")],
               loc="lower center", ncol=4, frameon=False, fontsize=6, handlelength=1.6, columnspacing=1.2)
    _save("fig_layouts.png", rect=(0, 0.06, 1, 1))


def weather_stats():
    """Monthly statistics of the measured TMYx weather used by the model."""
    out = {}
    for c in CITIES:
        lat, lon, tz, d = M._read_epw(c); mon, day, hour, T, Td, Pa = d[:, 0], d[:, 1], d[:, 2], d[:, 3], d[:, 4], d[:, 5]
        ghi = d[:, 6]; es = lambda t: 0.6108 * np.exp(17.27 * t / (t + 237.3)); rh = 100 * es(Td) / es(T)
        rows = []
        for m in range(1, 13):
            k = mon == m; days = np.unique(day[k])
            tmax = np.mean([T[k & (day == dd)].max() for dd in days]); tmin = np.mean([T[k & (day == dd)].min() for dd in days])
            rows.append(dict(mean=float(T[k].mean()), tmax=float(tmax), tmin=float(tmin), rh=float(rh[k].mean()),
                             ghi=float(ghi[k].sum() / 1000 / len(days))))
        out[c] = rows
    return out


def fig_weather():
    """Annual weather of both cities from the measured TMYx years: temperature, humidity, solar radiation."""
    W = weather_stats(); m = np.arange(1, 13); col = {"Riyadh": C3, "Jeddah": C0}
    fig, ax = plt.subplots(1, 3, figsize=(TEXTW, 2.7))
    a = ax[0]; a.axhspan(22, 24, color=C2, alpha=0.22, label="comfort band 22\u201324 \u00b0C")
    for c in CITIES:
        r = W[c]
        a.fill_between(m, [x["tmin"] for x in r], [x["tmax"] for x in r], color=col[c], alpha=0.15)
        a.plot(m, [x["mean"] for x in r], "o-", color=col[c], ms=4, label=c)
    a.set_ylabel("dry-bulb temperature [\u00b0C]"); a.set_title("(a) Air temperature")
    for c in CITIES:
        ax[1].plot(m, [x["rh"] for x in W[c]], "o-", color=col[c], ms=4, label=c)
        ax[2].bar(m + (-0.2 if c == "Riyadh" else 0.2), [x["ghi"] for x in W[c]], 0.4, color=col[c], label=c)
    
    ax[1].set_ylabel("relative humidity [%]"); ax[1].set_ylim(0, 100); ax[1].set_title("(b) Relative humidity")
    ax[2].set_ylabel("global horizontal irradiation [kWh/m$^2$ per day]"); ax[2].set_title("(c) Solar radiation")
    for a in ax:
        a.set_xticks(m); a.set_xticklabels(list("JFMAMJJASOND")); a.set_xlabel("month"); _clean(a)
    from matplotlib.patches import Patch
    h = [plt.Line2D([], [], color=col[c], marker="o", ms=4, label=c) for c in CITIES]
    h += [Patch(color="#888888", alpha=0.3, label="daily min\u2013max (shaded, panel a)"), Patch(color=C2, alpha=0.3, label="comfort band 22\u201324 \u00b0C")]
    fig.legend(handles=h, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 1.0))
    ax[2].set_ylim(0, 8.6)
    _save("fig_weather.png", rect=(0, 0, 1, 0.9))


def fig_hierarchy():
    """Savings by lever for the SBC-compliant building in the three cases, both cities."""
    R = RJ(); groups, setp, vol, env = [], [], [], []
    for c in CITIES:
        S = R[f"scopes_{c}"]
        for sc, lab in (("apartment", "Case 1: apartment"), ("floor", "Case 2: floor"), ("building", "Case 3: building")):
            x = S[sc]["compliant"]; groups.append(f"{c}\n{lab}")
            setp.append(x["setpoint_pct"])
            vol.append(x["volume"]["bldg_pct"]); env.append(S[sc]["envelope_pct"])
    xs = np.arange(len(groups)); w = 0.26
    fig, ax = plt.subplots(figsize=(TEXTW, 3.0))
    for off, v, col, lab in ((-1, setp, C1, "Setpoint 22 \u2192 23.5 \u00b0C"),
                             (0, vol, C3, "Best thermostat pre-cooling (0.5 K steps)"),
                             (1, env, C0, "SBC envelope vs pre-code (comparison)")):
        ax.bar(xs + off * w, v, w, color=col, lw=0.6, label=lab)
        for xi, vi in zip(xs + off * w, v): ax.text(xi, vi + 0.8, f"{vi:.1f}", ha="center", fontsize=5.5)
    ax.set_xticks(xs); ax.set_xticklabels(groups); ax.set_ylabel("saving [%]")
    _clean(ax); ax.legend(ncol=2, frameon=False, loc="upper left"); ax.set_ylim(0, max(env) * 1.38)
    _save("fig_hierarchy.png")


def fig_floors():
    """Apartment HVAC energy by storey in the four-storey building (both cities, both envelopes)."""
    R = RJ(); s1 = R["stage1"]["per_configuration"]
    fig, a = plt.subplots(figsize=(6.5, 4.2)); pos = np.arange(1, len(s1["compliant/Riyadh"]["apartment_kwh_by_floor"]) + 1)
    for c, col in zip(CITIES, (C3, C0)):
        a.plot(pos, s1[f"compliant/{c}"]["apartment_kwh_by_floor"], "o-", color=col, label=f"{c}, SBC-compliant")
        a.plot(pos, s1[f"noncompliant/{c}"]["apartment_kwh_by_floor"], "s--", color=col, alpha=0.7, label=f"{c}, pre-code envelope")
    a.set_xticks(pos); a.set_xticklabels(["ground"] + [str(p) for p in pos[1:-1]] + ["top"])
    a.set_xlabel("storey of the apartment"); a.set_ylabel("apartment HVAC energy [kWh/yr]")
    a.set_title("Apartment energy by storey in the four-storey building"); _clean(a); a.legend(frameon=False)
    _save("fig_floors.png")


def fig_monthly():
    R = RJ(); op = R["operations"]["seasonal"]; thr = M._P["tariff"]["volume"]["threshold_kwh"]
    fig, ax = plt.subplots(figsize=(COLW, 2.7)); m = range(1, 13)
    for c, col in zip(CITIES, (C0, C1)):
        ax.plot(m, op[c]["apartment"]["monthly"], "o-", color=col, label=f"{c}: representative apartment")
    ax.axhline(thr, ls="--", color=C3, lw=1); ax.text(1, thr * 0.93, "tier-2 threshold (6,000 kWh/month)", color=C3)
    ax.set_ylim(0, thr * 1.08); ax.set_xticks(list(m)); ax.set_xlabel("month"); ax.set_ylabel("HVAC energy [kWh/month]")
    ax.set_title("Monthly load of the representative apartment (SBC-compliant): tier 1 all year")
    _clean(ax); ax.legend(frameon=False); _save("fig_monthly.png")


def fig_humidity():
    R = RJ()["stage1"]["per_configuration"]; x = np.arange(2)
    lat = [R[f"compliant/{c}"]["latent_kwh"] for c in CITIES]
    tot = [R[f"compliant/{c}"]["building"]["kwh"] for c in CITIES]; sens = [t - l for t, l in zip(tot, lat)]
    fig, ax = plt.subplots(figsize=(COLW, 2.6))
    ax.bar(x, sens, 0.5, label="Sensible", color=C0); ax.bar(x, lat, 0.5, bottom=sens, label="Latent (dehumidification)", color=C2)
    ax.set_xticks(x); ax.set_xticklabels(CITIES); ax.set_ylabel("building HVAC energy [kWh/yr]")
    from matplotlib.ticker import FuncFormatter; ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _clean(ax); ax.legend(frameon=False); _save("fig_humidity.png")


def fig_landscape():
    """Cost landscape over the cooling setpoint and the pre-peak depth (SBC-compliant Jeddah building, volume tariff).
    Costs are shown relative to the comfort-feasible optimum; policies that break the ceiling are hatched."""
    L = RJ().get("landscape")
    if not L: print("  (skip fig_landscape)"); return
    vc, pk, B = np.array(L["setpoints"]), np.array(L["prepeak"]), np.array(L["bill"]); F = np.array(L["feasible"])
    best = B[F].min(); pc = (B / best - 1) * 100
    fig, ax = plt.subplots(figsize=(COLW, 2.9))
    im = ax.contourf(vc, pk, np.where(F, pc, np.nan), levels=20, cmap="viridis_r"); cb = fig.colorbar(im, ax=ax)
    cb.set_label("annual cost above the feasible optimum [%]")
    ax.contourf(vc, pk, np.where(F, np.nan, 1.0), levels=[0.5, 1.5], colors="none", hatches=["////"])
    ax.scatter([L["optimum"][0]], [L["optimum"][1]], marker="o", s=38, color="white", ec="k", lw=1.0, zorder=5, clip_on=False)
    ax.annotate("cheapest feasible point:\nhighest feasible setpoint,\nno pre-cooling", xy=L["optimum"], xytext=(22.1, 0.8),
                color="w", fontsize=6.5, arrowprops=dict(arrowstyle="->", color="w", lw=0.8, shrinkB=4))
    ax.set_xlabel("cooling setpoint [\u00b0C]"); ax.set_ylabel(r"pre-peak depth $\delta_{\mathrm{peak}}$ [K]")
    ax.text(23.875, 2.0, "breaks the ceiling", rotation=90, ha="center", va="center", fontsize=6)
    _save("fig_landscape.png")


def fig_leap():
    """Figure: mean electrical power of the running-example room for each schedule of the schematic figure: the
    thermostat cycle at two setpoints in mode 1 and at the reference in mode 3, and the deeper cycles from the ceiling
    that let the room fall to V_low or to V_min (pre-cooling), from the exact affine dynamics of the lumped room
    (results/parts/running_example.json)."""
    X = RJ()["running_example"]; B = X["band_cycles"]; cyc = {c["cycle"]: c for c in B["cycles"]}
    lv = {d["level"]: d for d in X["levels_mode1"]}; m3 = [d for d in X["leaps"] if d["mode"] == 3][0]
    NAVY, GRN = "#2c2c7a", "#1b7a3a"
    cur, ref = lv["current practice"]["setpoint_C"], lv["reference setpoint"]["setpoint_C"]
    labs = ["%.1f\nlow" % cur, "%.1f\nlow" % ref, "%.1f\nhigh" % ref, "to $V_{\\mathrm{low}}$\nlow", "to $V_{\\min}$\nlow"]
    vals = [lv["current practice"]["mean_power_kW"], lv["reference setpoint"]["mean_power_kW"],
            m3["mean_power_kW"], cyc["comfort band"]["mean_power_kW"], cyc["safe set"]["mean_power_kW"]]
    cols = ["#999999", C1, C3, GRN, NAVY]
    top = max(vals) * 1000 * 1.35
    fig, b = plt.subplots(figsize=(COLW, 2.75))
    b.bar(range(5), [v * 1000 for v in vals], color=cols, width=0.72)
    for k, v in enumerate(vals): b.text(k, v * 1000 + top * 0.012, f"{v * 1000:.0f} W", ha="center", fontsize=6.3)
    b.axvline(2.5, ymax=0.97, color="#bbbbbb", lw=0.6, ls=":")
    b.text(1.0, top * 0.92, "thermostat cycle\nat the setpoint", ha="center", fontsize=6.0, color="#555555")
    b.text(3.5, top * 0.92, "deeper cycles\nfrom the ceiling", ha="center", fontsize=6.0, color="#555555")
    b.set_ylim(0, top); b.set_xticks(range(5)); b.set_xticklabels(labs, fontsize=6.0)
    b.set_ylabel("mean electrical power [W]"); b.set_xlabel("setpoint [\u00b0C] and mode, or depth of the cycle")
    _clean(b)
    _save("fig_leap.png")


def fig_epcompare():
    """Delivered sensible cooling under ideal control: EnergyPlus and this engine on the same storey-box geometry, and
    this engine on the room-resolved plan (results/comparative.json)."""
    p = os.path.join(RES, "comparative.json")
    if not os.path.exists(p): print("  (skip fig_epcompare: run python -m hvac_savings.comparative first)"); return
    cmp = json.load(open(p))["box"]; cfgs = ["compliant/Riyadh", "compliant/Jeddah", "noncompliant/Riyadh", "noncompliant/Jeddah"]
    lab = ["SBC\nRiyadh", "SBC\nJeddah", "Pre-code\nRiyadh", "Pre-code\nJeddah"]
    ep = [cmp[c]["energyplus"]["cool_th"] for c in cfgs]; bx = [cmp[c]["engine_box"]["cool_th"] for c in cfgs]
    rm = [cmp[c]["engine_rooms"]["cool_th"] for c in cfgs]
    x = np.arange(4); w = 0.27
    fig, ax = plt.subplots(figsize=(COLW, 2.8))
    for off, v, col, l in ((-w, ep, C1, "EnergyPlus 26.1, storey box"), (0, bx, C0, "this engine, storey box"),
                           (w, rm, C2, "this engine, room-resolved plan")):
        ax.bar(x + off, v, w, label=l, color=col)
        for xi, vi in zip(x + off, v): ax.text(xi, vi + 2, f"{vi:.0f}", ha="center", fontsize=5.8)
    ax.set_xticks(x); ax.set_xticklabels(lab); ax.set_ylabel("delivered sensible cooling [kWh/m$^2$yr]")
    ax.set_ylim(0, max(ep + bx + rm) * 1.35); _clean(ax); ax.legend(frameon=False, loc="upper left"); _save("fig_epcompare.png")


def fig_pipeline():
    """Figure: the methodology pipeline, from the inputs to the outputs, with the section that describes each stage."""
    from matplotlib.patches import FancyBboxPatch
    W, H = 100, 36.5
    fig = plt.figure(figsize=(TEXTW, TEXTW * H / W * 0.36 / 0.35 * 0.98)); ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-0.6, W + 0.6); ax.set_ylim(-0.4, H + 0.4); ax.axis("off")
    def box(x, y, w, h, title, body, fc, ec):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2", fc=fc, ec=ec, lw=0.9))
        ax.text(x + 0.9, y + h - 0.9, title, ha="left", va="top", fontsize=6.5, fontweight="bold", color=ec)
        ax.text(x + 0.9, y + h - 4.3, body, ha="left", va="top", fontsize=6.0, linespacing=1.25, color="#222222")
    def seg(pts, ls="-", col="#444444"):
        xs, ys = zip(*pts); ax.plot(xs[:-1] + (xs[-1],), ys[:-1] + (ys[-1] + 0.9,), ls=ls, color=col, lw=1.0, solid_capstyle="butt")
        ax.annotate("", xy=pts[-1], xytext=(xs[-1], ys[-1] + 1.0), arrowprops=dict(arrowstyle="-|>", color=col, lw=1.0, mutation_scale=8))
    def arrow(x0, y0, x1, y1, col="#444444"):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="-|>", color=col, lw=1.0, mutation_scale=8))
    G, B, O, Gr, P, K = (("#f2f2f2", "#555555"), ("#e6f0f8", "#0b5d8f"), ("#fdf0e0", "#b35900"),
                         ("#e6f4ee", "#00704f"), ("#f1ecf7", "#5b3d8a"), ("#fbe9e4", "#a33a17"))
    h = 14.6; top, bot = H - h - 0.6, 0.6
    X = (0.6, 25.9, 51.1, 76.3); w = 23.0
    box(X[0], top, w, h, "Inputs (Sec. 4)", f"TMYx weather: Riyadh, Jeddah\nfloor plan: {M.NZ} zones, {int(M.COND.sum())} units,\n   {M.NMET} meters\nSBC 602 and pre-code envelopes\nunit catalogue; SASO 2663 COP\ntwo-tier volume tariff\ncomfort band [22, 24] °C", *G)
    box(X[1], top, w, h, "1  Room-resolved model (Sec. 5)", "two-node rooms coupled through\n   walls, slabs and open doors\nheat pumps: thermostat, modes,\n   restart delay, cycling losses\nlatent load; per-meter bill", *B)
    box(X[2], top, w, h, "2  Formal problem (Sec. 5)", "multi-mode system: timed actions,\n   leaps, infinite schedules,\n   limit-average cost\nminimise the annual bill\n   s.t. rooms in [20, 24] °C\npolicy: cooling setpoint, pre-cooling", *B)
    box(X[3], top, w, h, "3  Theory (Sec. 6)", "optimal leap: highest level,\n   lowest mode (or two-mode mix)\ninfinite-horizon optimum\npre-cooling bound\nsources of saving\n→ conditions to check", *O)
    box(X[0], bot, w, h, "4  Optimisation, evaluation (Sec. 7)", "Alg. 2: optimal control (LP)\n   of the idealised model\nAlg. 1: annual simulation,\n   per-meter bill\n49 thermostat settings compared:\nselect on 60 days, evaluate on 365", *Gr)
    box(X[1], bot, w, h, "5  Verification & validation (Sec. 8)", "time-step convergence\nheat balance of passive rooms\nBESTEST-style 600/900/640/940\nEnergyPlus on the same storeys,\n   with and without setbacks\nstock energy use (EUI)", *P)
    box(X[2], bot, w, h, "6  Experiments (Sec. 9)", "one lever at a time: setpoint,\n   mode, envelope, equipment,\n   scheduling\n3 cases × 2 cities × 2 seasons\nsensitivity analysis", *Gr)
    box(X[3], bot, w, h, "Outputs (Secs. 9–11)", "saving of each lever by case,\n   city and season\nSAR a year per apartment\ncomfort in rooms without units\nwhich lever pays under a\n   volume tariff", *K)
    for k in range(3):
        arrow(X[k] + w + 0.45, top + h / 2, X[k + 1] - 0.45, top + h / 2)
        arrow(X[k] + w + 0.45, bot + h / 2, X[k + 1] - 0.45, bot + h / 2)
    ym1, ym2 = top - 1.6, top - 3.1
    seg([(X[2] + 6, top - 0.35), (X[2] + 6, ym1), (X[0] + 11.5, ym1), (X[0] + 11.5, bot + h + 0.35)])
    ax.text((X[0] + 11.5 + X[2] + 6) / 2, ym1, " policy θ and constraints (C1)–(C5) ", fontsize=5.9, color="#444444",
            ha="center", va="center", bbox=dict(fc="white", ec="none", pad=0.3))
    seg([(X[3] + 11.5, top - 0.35), (X[3] + 11.5, ym2), (X[2] + 11.5, ym2), (X[2] + 11.5, bot + h + 0.35)], ls=(0, (3, 2)), col=O[1])
    ax.text((X[2] + X[3]) / 2 + 11.5, ym2, " conditions checked ", fontsize=5.9, color=O[1], ha="center", va="center",
            bbox=dict(fc="white", ec="none", pad=0.3))
    plt.savefig(os.path.join(FIG, "fig_pipeline.pdf")); plt.savefig(os.path.join(FIG, "fig_pipeline.png"), dpi=600); plt.close()


def fig_defs():
    """Schematic (no scale, no values) of the definitions on one temperature axis: V_min, V_low, the setpoint V_0 and
    V_high = V_max; a run of timed actions under the ceiling and a pre-cooling excursion of depth delta to V_min.
    Blue: the unit cools, (m, t_on); red: the unit is off and the room warms, (0, t_off)."""
    from matplotlib.lines import Line2D
    fig, a = plt.subplots(figsize=(COLW, 3.0))
    GREY, GRN, NAVY = "#555555", "#1b7a3a", "#2c2c7a"; COOL, WARM = "#1f5fbf", "#c0392b"
    Vmin, Vlo, V0, Vhi = 0.0, 1.35, 2.95, 3.9
    a.axhspan(Vlo, Vhi, color="#e8f4e8", zorder=0); a.axhspan(Vmin, Vlo, color="#eeeef8", zorder=0)
    a.fill_between([0, 10], Vhi, Vhi + 0.38, color="none", hatch="////", edgecolor="#bdbdbd", lw=0)
    for y in (Vmin, Vlo): a.axhline(y, color=GREY, lw=0.8)
    a.axhline(V0, color=GREY, lw=0.6, ls=":"); a.axhline(Vhi, color="#222222", lw=1.6)
    a.set_yticks([Vmin, Vlo, V0, Vhi])
    a.set_yticklabels(["$V_{\\min}$", "$V_{\\mathrm{low}}$", "$V_0$", "$V_{\\mathrm{high}}=V_{\\max}$"], fontsize=8)
    a.tick_params(axis="y", length=0); a.set_xticks([]); a.set_xlim(0, 10); a.set_ylim(-0.32, Vhi + 0.4); a.set_xlabel("time")
    for s in ("top", "right", "bottom"): a.spines[s].set_visible(False)
    box = dict(fc="white", ec="none", pad=0.3)
    a.text(7.4, Vhi + 0.2, "not allowed", ha="center", va="center", fontsize=6.4, color=GREY, bbox=box)
    # run: V0 -> V1 (ceiling) -> V2 (setpoint) -> V3 (ceiling) -> V4 (V_min: pre-cooling by delta) -> V5 (ceiling)
    xs = [0.45, 2.25, 3.05, 4.85, 6.05, 9.55]; ys = [V0, Vhi, V0, Vhi, Vmin, Vhi]
    for k in range(5):
        col = WARM if ys[k + 1] > ys[k] else COOL
        a.plot(xs[k:k + 2], ys[k:k + 2], color=col, lw=1.6, ls="--" if k >= 3 else "-", solid_capstyle="round")
    for k, (x, y) in enumerate(zip(xs, ys)):
        a.plot([x], [y], "o", ms=4.2, mfc="white", mec="#222222", mew=0.8, zorder=5, clip_on=False)
        ty = y + 0.2 if y == Vhi else y - 0.24
        a.text(x, ty, "$V_%d$" % k, ha="center", va="center", fontsize=7, bbox=box if y == Vhi else None, zorder=6)
    a.annotate("", xy=(9.92, V0), xytext=(9.92, Vmin), arrowprops=dict(arrowstyle="<->", color=GREY, lw=0.7, shrinkA=0, shrinkB=0))
    a.text(9.8, 0.75, "$\\delta$", fontsize=7, color=GREY, ha="right", va="center")
    a.annotate("", xy=(0.15, Vhi), xytext=(0.15, V0), arrowprops=dict(arrowstyle="<->", color=GREY, lw=0.7, shrinkA=0, shrinkB=0))
    a.text(0.25, (V0 + Vhi) / 2 + 0.12, "$\\Delta$", fontsize=7, color=GREY, ha="left", va="center")
    a.text(0.25, Vlo + 0.22, "comfort band", fontsize=6.4, color=GRN, ha="left", va="center")
    a.text(0.25, Vlo - 0.25, "safe set only", fontsize=6.4, color=NAVY, ha="left", va="center")
    h = [Line2D([], [], color=COOL, lw=1.6, label="$(m,t_{\\mathrm{on}})$: unit cools in mode $m$"),
         Line2D([], [], color=WARM, lw=1.6, label="$(0,t_{\\mathrm{off}})$: unit off, room warms"),
         Line2D([], [], color="#222222", lw=1.6, label="thermostat run from $V_0$"),
         Line2D([], [], color="#222222", lw=1.6, ls="--", label="pre-cooling: setpoint lowered by $\\delta$")]
    a.legend(handles=h, loc="upper center", bbox_to_anchor=(0.45, -0.1), ncol=2, frameon=False, fontsize=6.0,
             handlelength=2.2, columnspacing=1.0)
    plt.tight_layout()
    plt.savefig(os.path.join(FIG, "fig_defs.pdf")); plt.savefig(os.path.join(FIG, "fig_defs.png"), dpi=600); plt.close()


def main():
    for fn in (fig_pipeline, fig_defs, fig_weather, fig_building, fig_layouts, fig_hierarchy, fig_floors, fig_monthly, fig_humidity,
               fig_leap, fig_landscape, fig_epcompare):
        fn(); print(fn.__name__)
    print("Figures written to figures/")


if __name__ == "__main__":
    main()
