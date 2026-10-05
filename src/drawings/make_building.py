"""4-storey residential building (2 mirrored apartments per floor) -> layered DXF drawing set.
Units: metres.  Plans (GF, 1F, 2F, 3F, Roof), Section A-A, South Elevation.

Vertical design (SBC 201 / SBC 304 based, see title block):
  floor-to-floor 3.40 = 0.10 floor finish + 0.30 ribbed (hordi) RC slab + 3.00 clear ceiling height
  roof: 0.30 slab + 0.20 insulation/waterproofing/screed, parapet 1.20
  stair: 20 risers x 0.170, treads 0.280, 2 flights of 10 risers, flight width 1.10, landing 1.30
"""
import math
from pathlib import Path
import ezdxf
OUT = Path(__file__).resolve().parent   # DXF files are written next to this script
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import box, MultiPolygon, LineString
from shapely.ops import unary_union
from shapely import affinity

# ============ plan geometry (same as approved floor layout) ============
EXT, FIRE, PRT = 0.30, 0.25, 0.15
W, H = 19.15, 28.00
CX = W / 2
def mrect(r):
    x1, y1, x2, y2 = r
    return (W - x2, y1, W - x1, y2)

rooms = {
    "master":  ((0.30, 22.70, 5.30, 27.70), "MASTER BED R.", True),
    "ensuite": ((0.30, 21.05, 2.80, 22.55), "MASTER BATH", True),
    "lobby":   ((2.95, 21.05, 5.30, 22.55), "LOBBY", True),
    "bed_l":   ((0.30, 16.90, 4.30, 20.90), "BED R.", True),
    "corr_w":  ((4.45, 16.90, 5.45, 20.90), "", False),
    "bed_top": ((5.45, 23.70, 9.45, 27.70), "BED R.", True),
    "corr":    ((5.45, 16.90, 7.20, 23.55), "CORRIDOR", False),
    "bath_c":  ((7.45, 21.55, 9.45, 23.55), "BATH", True),
    "maid":    ((7.45, 19.40, 9.45, 21.40), "MAID R.", True),
    "circ":    ((7.45, 16.90, 9.45, 19.25), "", False),
    "balcony": ((0.30, 11.75, 1.80, 16.75), "BALCONY", True),
    "living":  ((2.10, 11.75, 7.10, 16.75), "LIVING ROOM", True),
    "dining":  ((7.25, 11.75, 9.45, 16.75), "DINING", True),
    "kitchen": ((0.30, 7.60, 4.30, 11.60), "KITCHEN", True),
    "passage": ((4.45, 7.60, 7.20, 11.60), "HALL", False),
    "bath_s":  ((7.45, 9.60, 9.45, 11.60), "BATH", True),
    "bath_e":  ((0.30, 5.45, 2.30, 7.45), "BATH", True),
    "hall":    ((2.45, 5.45, 7.20, 7.45), "ENTRANCE", False),
    "guest":   ((0.30, 0.30, 7.20, 5.30), "GUEST ROOM", True),
}
core = (7.45, 0.30, W - 7.45, 9.35)

doors = [
    (3.80, 22.625, 0.90, "h", +1), (2.875, 21.30, 0.70, "v", -1), (5.375, 21.30, 0.90, "v", -1),
    (5.65, 23.625, 0.90, "h", +1), (7.325, 22.15, 0.80, "v", +1), (7.325, 19.90, 0.80, "v", +1),
    (4.375, 19.90, 0.90, "v", -1), (4.375, 8.30, 0.90, "v", -1), (7.325, 10.20, 0.80, "v", +1),
    (2.375, 6.05, 0.70, "v", -1), (3.70, 5.375, 0.90, "h", -1),
    (7.325, 5.60, 1.00, "v", -1),   # apartment entrance (fire-rated, self-closing)
]
openings = [
    box(1.75, 12.75, 2.15, 15.75), box(4.45, 16.70, 9.45, 16.95), box(7.15, 16.90, 7.50, 19.25),
    box(7.05, 11.75, 7.30, 16.80), box(4.45, 11.55, 7.20, 11.80), box(4.45, 7.40, 7.20, 7.65),
]
windows = [
    (0.0, 21.35, EXT, 22.25), (0.0, 18.00, EXT, 19.80), (0.0, 12.25, EXT, 16.25),
    (0.0, 8.70, EXT, 10.50), (0.0, 6.05, EXT, 6.85), (0.0, 1.80, EXT, 3.80),
    (1.80, H - EXT, 3.80, H), (6.45, H - EXT, 8.45, H), (1.80, 0.0, 3.80, EXT),
]
core_window = (CX - 1.0, 0.0, CX + 1.0, EXT)
main_door = (CX - 0.75, 0.0, CX + 0.75, EXT)

# stair (plan)
RISER_N, TREAD = 20, 0.28
FLIGHT_W, LAND = 1.10, 1.30
ST_Y0 = 9.35 - LAND - (RISER_N // 2 - 1) * TREAD     # foot of flights = 5.53
FL_L = (CX - 0.05 - FLIGHT_W, ST_Y0, CX - 0.05, 9.35 - LAND)
FL_R = (CX + 0.05, ST_Y0, CX + 0.05 + FLIGHT_W, 9.35 - LAND)

# vertical
FTF, FIN, SLAB = 3.40, 0.10, 0.30
CLEAR = FTF - FIN - SLAB
NGL = -0.45
FLOORS = ["GROUND", "FIRST", "SECOND", "THIRD"]
FFL = [i * FTF for i in range(4)]
ROOF_SLAB_TOP = 4 * FTF - FIN          # 13.50
ROOF_FIN = ROOF_SLAB_TOP + 0.20        # 13.70
PARAPET = ROOF_FIN + 1.20              # 14.90
SR_TOP = ROOF_FIN + 3.20               # stair room roof top 16.90
SILL, HEAD = 0.90, 2.40

# ============ DXF ============
doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.M
doc.header["$INSUNITS"] = 6
doc.header["$MEASUREMENT"] = 1
doc.linetypes.add("DASH2", pattern=[0.6, 0.4, -0.2])
doc.linetypes.add("CENTER2", pattern=[1.4, 1.0, -0.2, 0.0, -0.2])
LAY = {"A-WALL": (7, 50), "A-WALL-PATT": (8, 0), "A-DOOR": (3, 18), "A-GLAZ": (4, 18),
       "A-FLOR-STRS": (6, 18), "A-ANNO-TEXT": (7, 25), "A-ANNO-DIMS": (1, 18), "A-AREA": (7, 9),
       "A-ANNO-TTLB": (7, 35), "A-REF": (8, 13), "A-SECT-CUT": (7, 50), "A-SECT-BEYOND": (8, 13),
       "A-ELEV": (7, 25), "A-ELEV-GRND": (7, 70), "A-ANNO-LEVL": (5, 18), "A-ANNO-SYMB": (1, 25)}
for n, (c, lw) in LAY.items():
    doc.layers.add(n, color=c, lineweight=lw)
for n in ("A-ANNO-TEXT", "A-AREA", "A-ANNO-TTLB", "A-ANNO-LEVL"):
    doc.layers.get(n).rgb = (0, 0, 0)
doc.layers.get("A-REF").dxf.linetype = "DASH2"
msp = doc.modelspace()

class V:
    """view with an origin offset"""
    def __init__(self, ox, oy):
        self.ox, self.oy = ox, oy
    def p(self, x, y):
        return (x + self.ox, y + self.oy)
    def line(self, a, b, layer, **kw):
        msp.add_line(self.p(*a), self.p(*b), dxfattribs={"layer": layer, **kw})
    def poly(self, pts, layer, close=True, **kw):
        msp.add_lwpolyline([self.p(*q) for q in pts], close=close, dxfattribs={"layer": layer, **kw})
    def rect(self, r, layer, **kw):
        x1, y1, x2, y2 = r
        self.poly([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], layer, **kw)
    def solid(self, r, color=8):
        x1, y1, x2, y2 = r
        h = msp.add_hatch(color=color, dxfattribs={"layer": "A-WALL-PATT"})
        h.paths.add_polyline_path([self.p(x1, y1), self.p(x2, y1), self.p(x2, y2), self.p(x1, y2)], is_closed=True)
    def text(self, s, x, y, h, layer="A-ANNO-TEXT", align=TextEntityAlignment.MIDDLE_CENTER, rot=0):
        t = msp.add_text(s, height=h, rotation=rot, dxfattribs={"layer": layer, "style": "OpenSans"})
        t.set_placement(self.p(x, y), align=align)
    def arc90(self, c, p1, p2, layer):
        a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0])) % 360
        a2 = math.degrees(math.atan2(p2[1] - c[1], p2[0] - c[0])) % 360
        r = math.dist(c, p1)
        s, e = (a1, a2) if round((a2 - a1) % 360) == 90 else (a2, a1)
        msp.add_arc(self.p(*c), r, s, e, dxfattribs={"layer": layer})
    def hdim(self, a, b, yref, base, txt=None):
        d = msp.add_linear_dim(base=self.p(a, base), p1=self.p(a, yref), p2=self.p(b, yref), dimstyle="EZDXF",
                               text=txt or "<>", override=OVR, dxfattribs={"layer": "A-ANNO-DIMS"})
        d.render()
    def vdim(self, a, b, xref, base, txt=None):
        d = msp.add_linear_dim(base=self.p(base, a), p1=self.p(xref, a), p2=self.p(xref, b), angle=90,
                               dimstyle="EZDXF", text=txt or "<>", override=OVR, dxfattribs={"layer": "A-ANNO-DIMS"})
        d.render()
    def level(self, x, y, label, side=+1):
        """level marker: triangle on line, text to the side"""
        self.poly([(x, y), (x - 0.18, y + 0.25), (x + 0.18, y + 0.25)], "A-ANNO-SYMB")
        self.line((x - 0.5, y), (x + side * 2.4, y), "A-ANNO-SYMB")
        self.text(label, x + side * 0.35, y + 0.18, 0.2, "A-ANNO-LEVL",
                  align=TextEntityAlignment.BOTTOM_LEFT if side > 0 else TextEntityAlignment.BOTTOM_RIGHT)

OVR = {"dimtxt": 0.18, "dimasz": 0.12, "dimexo": 0.08, "dimdec": 2, "dimzin": 0, "dimlfac": 1.0,
       "dimtad": 1, "dimtih": 0, "dimtoh": 0}

def add_wall_poly(v, poly):
    poly = affinity.translate(poly, v.ox, v.oy)
    for g in (poly.geoms if isinstance(poly, MultiPolygon) else [poly]):
        if g.area < 1e-4:
            continue
        for ring in [g.exterior, *g.interiors]:
            msp.add_lwpolyline(list(ring.coords), close=True, dxfattribs={"layer": "A-WALL"})
        h = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-PATT"})
        h.paths.add_polyline_path(list(g.exterior.coords), is_closed=True, flags=1)
        for i in g.interiors:
            h.paths.add_polyline_path(list(i.coords), is_closed=True, flags=16)

def door_gap(x, y, w, d, s):
    return box(x - 0.2, y, x + 0.2, y + w) if d == "v" else box(x, y - 0.2, x + w, y + 0.2)

def plan_walls(ground):
    spaces = [box(*r[0]) for r in rooms.values()] + [box(*mrect(r[0])) for r in rooms.values()] + [box(*core)]
    gaps = []
    for dd in doors:
        g = door_gap(*dd); gaps += [g, box(*mrect(g.bounds))]
    for o in openings:
        gaps += [o, box(*mrect(o.bounds))]
    wins = windows + [mrect(w) for w in windows]
    gaps += [box(*w) for w in wins] + [box(*(main_door if ground else core_window))]
    walls = box(0, 0, W, H).difference(unary_union(spaces)).difference(unary_union(gaps))
    return walls, wins

def draw_stair_plan(v, kind):
    """kind: 'ground' | 'typical' | 'top'"""
    L = "A-FLOR-STRS"
    for fl in (FL_L, FL_R):
        x1, y1, x2, y2 = fl
        v.rect(fl, L)
        for k in range(RISER_N // 2):
            yy = y1 + k * TREAD
            v.line((x1, yy), (x2, yy), L)
    v.rect((CX - 0.05, ST_Y0, CX + 0.05, 9.35 - LAND), L)           # central balustrade / divider
    v.line((FL_L[0], 9.35 - LAND), (FL_R[2], 9.35 - LAND), L)        # landing edge
    xm_l, xm_r = (FL_L[0] + FL_L[2]) / 2, (FL_R[0] + FL_R[2]) / 2
    # left flight: up (north) from this floor
    if kind == "top":
        v.line((xm_r, ST_Y0 + 0.15), (xm_r, 9.35 - LAND - 0.2), L)
        v.poly([(xm_r - 0.12, 9.35 - LAND - 0.45), (xm_r, 9.35 - LAND - 0.2), (xm_r + 0.12, 9.35 - LAND - 0.45)], L, close=False)
        v.text("DN", xm_r, ST_Y0 - 0.25, 0.18)
        v.text("LANDING", CX, 9.35 - LAND / 2, 0.16)
        return
    v.line((xm_l, ST_Y0 + 0.15), (xm_l, 9.35 - LAND - 0.2), L)
    v.poly([(xm_l - 0.12, 9.35 - LAND - 0.45), (xm_l, 9.35 - LAND - 0.2), (xm_l + 0.12, 9.35 - LAND - 0.45)], L, close=False)
    v.text("UP", xm_l, ST_Y0 - 0.25, 0.18)
    if kind == "ground":
        v.line((xm_r, 9.35 - LAND - 0.2), (xm_r, ST_Y0 + 0.4), L)
        v.poly([(xm_r - 0.12, ST_Y0 + 0.65), (xm_r, ST_Y0 + 0.4), (xm_r + 0.12, ST_Y0 + 0.65)], L, close=False)
        v.text("UP", xm_r, ST_Y0 - 0.25, 0.18)
    else:
        v.line((xm_r, ST_Y0 + 0.15), (xm_r, 9.35 - LAND - 0.2), L)
        v.poly([(xm_r - 0.12, 9.35 - LAND - 0.45), (xm_r, 9.35 - LAND - 0.2), (xm_r + 0.12, 9.35 - LAND - 0.45)], L, close=False)
        v.text("DN", xm_r, ST_Y0 - 0.25, 0.18)
    # cut (break) line across the flights
    v.line((FL_L[0], 7.0), (FL_R[2], 7.4), L)
    v.text("LANDING", CX, 9.35 - LAND / 2, 0.16)

def draw_plan(v, idx):
    ground = idx == 0
    walls, wins = plan_walls(ground)
    add_wall_poly(v, walls)
    # doors
    for (x, y, w, d, s) in doors:
        if d == "v":
            hinge, leaf, closed = (x, y), (x + s * w, y), (x, y + w)
        else:
            hinge, leaf, closed = (x, y), (x, y + s * w), (x + w, y)
        for m in (False, True):
            f = (lambda q: (W - q[0], q[1])) if m else (lambda q: q)
            v.line(f(hinge), f(leaf), "A-DOOR")
            v.arc90(f(hinge), f(leaf), f(closed), "A-DOOR")
    for r in (openings[0].bounds, mrect(openings[0].bounds)):
        x1, y1, x2, y2 = r
        xm, ym = (x1 + x2) / 2, (y1 + y2) / 2
        v.line((xm - 0.04, y1), (xm - 0.04, ym + 0.1), "A-DOOR")
        v.line((xm + 0.04, ym - 0.1), (xm + 0.04, y2), "A-DOOR")
    # windows
    for (x1, y1, x2, y2) in wins + ([] if ground else [core_window]):
        v.rect((x1, y1, x2, y2), "A-GLAZ")
        for f in (0.4, 0.6):
            if (x2 - x1) < (y2 - y1):
                xx = x1 + f * (x2 - x1); v.line((xx, y1), (xx, y2), "A-GLAZ")
            else:
                yy = y1 + f * (y2 - y1); v.line((x1, yy), (x2, yy), "A-GLAZ")
    if ground:   # main entrance double door + external steps (NGL -0.45 -> ±0.00, 3 x 0.15)
        x1, _, x2, _ = main_door
        xm = (x1 + x2) / 2
        for hx, sgn in ((x1, +1), (x2, -1)):
            v.line((hx, EXT), (hx, EXT + 0.75), "A-DOOR")
            v.arc90((hx, EXT), (hx, EXT + 0.75), (hx + sgn * 0.75, EXT), "A-DOOR")
        for k in range(4):
            v.line((CX - 1.4, -k * 0.30), (CX + 1.4, -k * 0.30), "A-FLOR-STRS")
        v.line((CX - 1.4, 0), (CX - 1.4, -0.9), "A-FLOR-STRS"); v.line((CX + 1.4, 0), (CX + 1.4, -0.9), "A-FLOR-STRS")
        v.text("3 STEPS 0.15", CX, -1.25, 0.16)
        v.text("MAIN ENTRANCE", CX, 1.0, 0.2)
    draw_stair_plan(v, "ground" if ground else "typical")
    v.text("STAIR LOBBY", CX, 3.2, 0.22)
    # labels
    shift = {"master": (0, -1.2), "bed_top": (0, -0.9), "living": (0.6, -0.1), "guest": (0, -0.4),
             "kitchen": (0.3, 0.2), "bed_l": (0.9, 0)}
    for k, (r, name, show) in rooms.items():
        if not name:
            continue
        x1, y1, x2, y2 = r
        w, h = x2 - x1, y2 - y1
        th = 0.17 if min(w, h) < 2.6 else 0.28
        dx, dy = shift.get(k, (0, 0))
        for cx in ((x1 + x2) / 2 + dx, W - ((x1 + x2) / 2 + dx)):
            cy = (y1 + y2) / 2 + dy
            v.text(name, cx, cy + th * 0.9, th)
            if show:
                v.text(f"{w:.2f} x {h:.2f}", cx, cy - th * 0.4, th * 0.8)
    # section cut line A-A at x = 9.0
    sx = 9.0
    v.line((sx, -1.6), (sx, H + 1.6), "A-ANNO-SYMB", linetype="CENTER2")
    for yy in (-1.6, H + 1.6):
        v.poly([(sx, yy), (sx + 0.9, yy)], "A-ANNO-SYMB", close=False)
        v.poly([(sx + 0.9, yy), (sx + 0.6, yy + 0.15), (sx + 0.6, yy - 0.15)], "A-ANNO-SYMB")
        v.text("A", sx - 0.35, yy, 0.35, "A-ANNO-SYMB")
    # dims
    xs = [0, 0.3, 7.2, 7.45, W - 7.45, W - 7.2, W - 0.3, W]
    for a, b in zip(xs, xs[1:]):
        v.hdim(a, b, 0, -2.2)
    v.hdim(0, W, 0, -3.0)
    ys = [0, 0.3, 5.3, 5.45, 7.45, 7.6, 11.6, 11.75, 16.75, 16.9, 20.9, 21.05, 22.55, 22.7, 27.7, 28.0]
    for a, b in zip(ys, ys[1:]):
        v.vdim(a, b, 0, -1.0)
    v.vdim(0, H, 0, -2.0)
    # title
    v.text(f"{FLOORS[idx]} FLOOR PLAN", CX, -4.3, 0.55)
    v.text(f"FFL {'±0.00' if idx == 0 else f'+{FFL[idx]:.2f}'}   |   Scale 1:100   |   2 apartments", CX, -5.1, 0.25)

def draw_roof_plan(v):
    outer, inner = box(0, 0, W, H), box(0.30, 0.30, W - 0.30, H - 0.30)
    sr_out = box(7.20, 0.0, W - 7.20, 9.60)
    sr_in = box(*core)
    # parapet ring
    add_wall_poly(v, outer.difference(inner).union(sr_out.difference(sr_in)).difference(box(CX - 0.5, 9.30, CX + 0.5, 9.65)))
    # roof-access door
    v.line((CX - 0.5, 9.475), (CX - 0.5, 10.475), "A-DOOR"); v.arc90((CX - 0.5, 9.475), (CX - 0.5, 10.475), (CX + 0.5, 9.475), "A-DOOR")
    draw_stair_plan(v, "top")
    v.text("STAIR ROOM", CX, 3.2, 0.22)
    v.text(f"(roof FFL +{ROOF_FIN:.2f}, top +{SR_TOP:.2f})", CX, 2.7, 0.16)
    v.text("ROOF", 4.0, 16.0, 0.4); v.text("ROOF", W - 4.0, 16.0, 0.4)
    v.text("1.5% slope to drains", 4.0, 15.2, 0.2); v.text("1.5% slope to drains", W - 4.0, 15.2, 0.2)
    for (x, y) in [(0.9, 0.9), (W - 0.9, 0.9), (0.9, H - 0.9), (W - 0.9, H - 0.9), (0.9, 14.0), (W - 0.9, 14.0)]:
        msp.add_circle(v.p(x, y), 0.15, dxfattribs={"layer": "A-ANNO-SYMB"})
    v.text("RD = roof drain", CX, H - 1.0, 0.16)
    v.text(f"Parapet 0.30 thk, top +{PARAPET:.2f} (1.20 above roof finish)", CX, 22.0, 0.2)
    v.hdim(0, W, 0, -3.0); v.vdim(0, H, 0, -2.0)
    v.text("ROOF PLAN", CX, -4.3, 0.55)
    v.text(f"Roof finish +{ROOF_FIN:.2f}   |   Scale 1:100", CX, -5.1, 0.25)

# ---------------- Section A-A (cut at plan x = 9.0, looking east) ----------------
def draw_section(v):
    sx = 9.0
    walls, _ = plan_walls(False)
    cut = walls.intersection(LineString([(sx, -1), (sx, H + 1)]))
    segs = []
    for g in getattr(cut, "geoms", [cut]):
        ys = [c[1] for c in g.coords]
        segs.append((min(ys), max(ys)))
    segs = [s for s in segs if s[1] - s[0] > 0.01 and s[0] > 0.31]      # interior + north walls
    north = [s for s in segs if s[0] >= H - 0.31]
    inner = [s for s in segs if s not in north]
    C, B = "A-SECT-CUT", "A-SECT-BEYOND"

    # ground
    v.line((-3, NGL), (H + 3, NGL), "A-ELEV-GRND")
    v.text("NGL", -2.4, NGL + 0.15, 0.2)
    v.rect((0, -0.25, H, -0.10), C); v.solid((0, -0.25, H, -0.10))                 # slab on grade
    for k in range(3):                                                             # entrance steps
        v.rect((-0.30 * (k + 1), NGL, -0.30 * k, NGL + 0.15 * (3 - k)), C)
    # floor slabs
    for i in range(1, 4):
        top = FFL[i] - FIN
        for (a, b) in ((0, ST_Y0), (9.35, H)):
            r = (a, top - SLAB, b, top); v.rect(r, C); v.solid(r)
            v.line((a, top), (b, top), C); v.line((a, FFL[i]), (b, FFL[i]), B)
    # roof slab + layers
    r = (0, ROOF_SLAB_TOP - SLAB, ST_Y0, ROOF_SLAB_TOP); v.rect(r, C); v.solid(r)
    r = (9.35, ROOF_SLAB_TOP - SLAB, H, ROOF_SLAB_TOP); v.rect(r, C); v.solid(r)
    v.rect((9.60, ROOF_SLAB_TOP, H - 0.30, ROOF_FIN), C)
    v.text("roof insulation + waterproofing + screed 0.20", 18.0, ROOF_FIN + 0.35, 0.17)
    v.rect((0.30, ROOF_SLAB_TOP, ST_Y0, ROOF_FIN), C)
    # stair-room roof
    r = (0, SR_TOP - 0.20, 9.60, SR_TOP); v.rect(r, C); v.solid(r)

    for i in range(4):
        f = FFL[i]
        floor_top = f
        ceil = f + FTF - FIN - SLAB if i < 3 else ROOF_SLAB_TOP - SLAB
        # interior + north walls
        for (a, b) in inner:
            r = (a, floor_top, b, ceil); v.rect(r, C); v.solid(r)
        for (a, b) in north:
            r = (a, floor_top, b, ceil); v.rect(r, C); v.solid(r)
        # south exterior wall of stair lobby with window / main door
        if i == 0:
            r = (0, HEAD, EXT, ceil); v.rect(r, C); v.solid(r)
            v.rect((0, 0, EXT, HEAD), "A-DOOR")
        else:
            for r in ((0, f, EXT, f + SILL), (0, f + HEAD, EXT, ceil)):
                v.rect(r, C); v.solid(r)
            v.rect((0, f + SILL, EXT, f + HEAD), "A-GLAZ")
            v.line((0.15, f + SILL), (0.15, f + HEAD), "A-GLAZ")
        # stairs
        rise_total = (FFL[i + 1] if i < 3 else ROOF_FIN) - f
        rr = rise_total / RISER_N
        land = f + rise_total / 2
        # cut flight (left, going north)
        pts, y, z = [(ST_Y0, f)], ST_Y0, f
        for k in range(RISER_N // 2):
            z += rr; pts.append((y, z))
            if k < RISER_N // 2 - 1:
                y += TREAD; pts.append((y, z))
        pts.append((9.35 - LAND, z))
        ang = math.atan2(rise_total / 2, 9.35 - LAND - ST_Y0)
        t = 0.15 / math.cos(ang)
        pts += [(9.35 - LAND, land - t), (ST_Y0, f - t)]
        v.poly(pts, C); hh = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-PATT"})
        hh.paths.add_polyline_path([v.p(*q) for q in pts], is_closed=True)
        # landing slab (cut)
        r = (9.35 - LAND, land - 0.20, 9.35, land); v.rect(r, C); v.solid(r)
        # return flight beyond (going south, seen)
        y, z = 9.35 - LAND, land
        bp = [(y, z)]
        for k in range(RISER_N // 2):
            z += rr; bp.append((y, z))
            if k < RISER_N // 2 - 1:
                y -= TREAD; bp.append((y, z))
        bp.append((ST_Y0, z))
        v.poly(bp, B, close=False)
        v.line((9.35 - LAND, land - 0.2), (ST_Y0, land + rise_total / 2 - 0.15 / math.cos(ang)), B)
        # handrail line 0.90 above nosing on cut flight
        v.line((ST_Y0, f + 0.9 + rr), (9.35 - LAND, land + 0.9), B)
        # room names
        names = {"bath_s": "BATH", "dining": "DINING", "maid": "MAID R.", "bath_c": "BATH", "bed_top": "BED R."}
        for k, nm in names.items():
            y1, y2 = rooms[k][0][1], rooms[k][0][3]
            v.text(nm, (y1 + y2) / 2, f + 1.6, 0.2)
        v.text("STAIR / LOBBY", 2.6, f + 1.6, 0.2)
        v.text(f"{FLOORS[i]} FLOOR", 14.3, f + 2.3, 0.22)
    # parapets (south and north exterior walls above roof)
    for r in ((H - EXT, ROOF_SLAB_TOP, H, PARAPET),):
        v.rect(r, C); v.solid(r)
    for r in ((0, ROOF_SLAB_TOP, EXT, SR_TOP - 0.2), (9.35, ROOF_SLAB_TOP, 9.60, SR_TOP - 0.2)):  # stair room walls
        v.rect(r, C); v.solid(r)
    v.rect((9.35, ROOF_FIN, 9.60, ROOF_FIN + 2.10), "A-DOOR")      # roof access door opening (in north wall of stair room)
    v.text("STAIR ROOM", 4.6, ROOF_FIN + 1.4, 0.2)
    v.text("ROOF", 18.0, ROOF_FIN + 1.0, 0.25)
    # ground floor walls also go down to slab on grade (already from FFL 0)
    # levels
    for i, f in enumerate(FFL):
        v.level(H + 1.2, f, f"FFL {'±0.00' if f == 0 else f'+{f:.2f}'}")
    v.level(H + 1.2, ROOF_FIN, f"ROOF +{ROOF_FIN:.2f}")
    v.level(H + 1.2, PARAPET, f"PARAPET +{PARAPET:.2f}")
    v.level(-1.4, SR_TOP, f"STAIR ROOM +{SR_TOP:.2f}", side=-1)
    v.level(H + 1.2, NGL, f"NGL {NGL:.2f}")
    # vertical dimension chains: floor-to-floor and one detailed storey
    chain = [NGL, 0] + FFL[1:] + [ROOF_FIN, PARAPET]
    for a, b in zip(chain, chain[1:]):
        v.vdim(a, b, H, H + 4.6)
    v.vdim(NGL, PARAPET, H, H + 5.6)
    # detail chain for typical storey (first floor)
    f1 = FFL[1]
    det = [f1, f1 + CLEAR, f1 + CLEAR + SLAB, f1 + FTF]
    for a, b in zip(det, det[1:]):
        v.vdim(a, b, 0, -3.2)
    v.text("clear ceiling", -4.4, f1 + 1.5, 0.17, align=TextEntityAlignment.MIDDLE_RIGHT)
    v.text("RC slab", -4.4, f1 + CLEAR + 0.15, 0.17, align=TextEntityAlignment.MIDDLE_RIGHT)
    v.text("floor finish", -4.4, f1 + FTF - 0.05, 0.17, align=TextEntityAlignment.MIDDLE_RIGHT)
    v.text("SECTION A-A", H / 2, -2.3, 0.55)
    v.text("Scale 1:100   |   looking east, cut through stair core", H / 2, -3.1, 0.25)

# ---------------- South elevation ----------------
def draw_elevation(v):
    E = "A-ELEV"
    v.line((-3, NGL), (W + 3, NGL), "A-ELEV-GRND")
    v.rect((0, NGL, W, PARAPET), E)
    v.rect((7.20, PARAPET, W - 7.20, SR_TOP), E)       # stair room above parapet
    v.line((7.20, ROOF_FIN), (7.20, PARAPET), "A-SECT-BEYOND"); v.line((W - 7.20, ROOF_FIN), (W - 7.20, PARAPET), "A-SECT-BEYOND")
    v.line((0, 0), (W, 0), E)                          # plinth line
    for f in FFL[1:]:
        v.line((0, f - FIN - SLAB), (W, f - FIN - SLAB), E)   # slab band
        v.line((0, f - FIN), (W, f - FIN), E)
    v.line((0, ROOF_SLAB_TOP - SLAB), (W, ROOF_SLAB_TOP - SLAB), E)
    v.line((0, ROOF_FIN), (W, ROOF_FIN), E)
    v.rect((-0.05, PARAPET - 0.10, W + 0.05, PARAPET), E)    # coping
    def win(x1, x2, z1, z2):
        v.rect((x1, z1, x2, z2), "A-GLAZ")
        v.rect((x1 + 0.06, z1 + 0.06, x2 - 0.06, z2 - 0.06), "A-GLAZ")
        xm = (x1 + x2) / 2
        v.line((xm, z1 + 0.06), (xm, z2 - 0.06), "A-GLAZ")
        v.rect((x1 - 0.05, z1 - 0.08, x2 + 0.05, z1), E)     # sill
    for i, f in enumerate(FFL):
        for (x1, x2) in ((1.80, 3.80), (W - 3.80, W - 1.80)):
            win(x1, x2, f + SILL, f + HEAD)
        if i > 0:
            win(CX - 1.0, CX + 1.0, f + SILL, f + HEAD)
    # main entrance
    v.rect((CX - 0.75, 0, CX + 0.75, HEAD), "A-DOOR")
    v.line((CX, 0), (CX, HEAD), "A-DOOR")
    v.rect((CX - 1.2, HEAD + 0.05, CX + 1.2, HEAD + 0.25), E)  # canopy
    for k in range(3):
        v.rect((CX - 1.4, NGL + 0.15 * k, CX + 1.4, NGL + 0.15 * (k + 1)), E)
    win(CX - 0.6, CX + 0.6, ROOF_FIN + 1.2, ROOF_FIN + 2.2)       # stair room vent window
    for f in FFL:
        v.level(W + 1.2, f, f"FFL {'±0.00' if f == 0 else f'+{f:.2f}'}")
    v.level(W + 1.2, ROOF_FIN, f"ROOF +{ROOF_FIN:.2f}")
    v.level(W + 1.2, PARAPET, f"PARAPET +{PARAPET:.2f}")
    v.level(W + 1.2, SR_TOP, f"STAIR ROOM +{SR_TOP:.2f}")
    v.level(W + 1.2, NGL, f"NGL {NGL:.2f}")
    v.hdim(0, W, NGL, NGL - 1.2)
    v.vdim(NGL, SR_TOP, 0, -1.2)
    v.text("SOUTH ELEVATION (MAIN ENTRANCE)", W / 2, -2.6, 0.55)
    v.text(f"Scale 1:100   |   windows sill +{SILL:.2f} / head +{HEAD:.2f} above each FFL", W / 2, -3.4, 0.25)

# ---------------- layout ----------------
GAP = 32.0
for i in range(4):
    draw_plan(V(i * GAP, 0), i)
draw_roof_plan(V(4 * GAP, 0))
draw_section(V(0, -30))
draw_elevation(V(42, -30))

# title block
tb = V(72, -30)
tb.rect((0, -4, 22, 18), "A-ANNO-TTLB")
lines = [
    ("4-STOREY RESIDENTIAL BUILDING", 0.6), ("8 apartments (2 mirrored units per floor) + common stair", 0.3), ("", 0.2),
    ("VERTICAL DESIGN (SBC 201 / SBC 304)", 0.36),
    (f"Floor-to-floor height            {FTF:.2f} m", 0.26),
    (f"Clear ceiling height              {CLEAR:.2f} m  (min. habitable 2.40)", 0.26),
    (f"Structural slab (ribbed/hordi)  {SLAB:.2f} m  (l/18.5 for 5.3 m span)", 0.26),
    (f"Floor finish (screed + tiles)    {FIN:.2f} m", 0.26),
    (f"Roof build-up                         0.20 m  + parapet 1.20 m", 0.26),
    (f"Ground FFL ±0.00 = NGL +0.45 (3 steps x 0.15)", 0.26), ("", 0.2),
    ("STAIR (SBC 201 means of egress)", 0.36),
    (f"20 risers x {FTF/RISER_N:.3f} m (max 0.18), tread {TREAD:.2f} m (min 0.28)", 0.26),
    (f"Flight width {FLIGHT_W:.2f} m, landing {LAND:.2f} m, handrail 0.90", 0.26),
    ("Stair enclosure & unit walls 0.25 m, 1 h fire rated", 0.26), ("", 0.2),
    ("WALLS", 0.36), ("Exterior 0.30 insulated block (SBC 602)", 0.26), ("Partitions 0.15", 0.26), ("", 0.2),
    ("Units: metres  |  Verify with structural & energy calcs", 0.24),
]
y = 17.0
for t, h in lines:
    tb.text(t, 0.6, y, h, "A-ANNO-TTLB", align=TextEntityAlignment.BOTTOM_LEFT)
    y -= h * 2.1 + 0.15

doc.saveas(str(OUT / "Building_4_Floors.dxf"))
print("stair foot y", ST_Y0, "riser", FTF / RISER_N, "clear", CLEAR)
