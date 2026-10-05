"""Twin-apartment typical floor plan -> layered AutoCAD DXF (v2, dimensioned grid).
Units: metres. Exterior walls 0.30, unit/stair walls 0.25, partitions 0.15. Symmetric about x = 9.575.
Room sizes are CLEAR internal dimensions (wall face to wall face).
"""
import math
from pathlib import Path
import ezdxf
OUT = Path(__file__).resolve().parent   # DXF files are written next to this script
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import box, MultiPolygon
from shapely.ops import unary_union

# Wall types (SBC-oriented):
#   EXT 0.30  exterior: 200 mm insulated block + insulation/finishes (SBC 602 thermal envelope)
#   FIRE 0.25 dwelling-unit separation & stair/shaft enclosure: 200 mm block + plaster (>= 1 h, SBC 201)
#   PRT 0.15  internal partitions: 100 mm block + 2 x 25 mm plaster
EXT, FIRE, PRT = 0.30, 0.25, 0.15
W, H = 19.15, 28.00
CX = W / 2

def mirror_rect(r):
    x1, y1, x2, y2 = r
    return (W - x2, y1, W - x1, y2)

def mx(x):
    return W - x

rooms = {  # key: (rect, label, show_dims)   -- clear internal sizes
    "master":  ((0.30, 22.70, 5.30, 27.70), "MASTER BED R.", True),
    "ensuite": ((0.30, 21.05, 2.80, 22.55), "MASTER BATH", True),
    "lobby":   ((2.95, 21.05, 5.30, 22.55), "LOBBY", True),
    "bed_l":   ((0.30, 16.90, 4.30, 20.90), "BED R.", True),
    "corr_w":  ((4.45, 16.90, 5.45, 20.90), "", False),   # corridor widens beside 4 m bedroom
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
core = (7.45, 0.30, W - 7.45, 9.35)   # shared stair + lobby (fire-rated enclosure)

doors = [
    (3.80, 22.625, 0.90, "h", +1),  # master (from lobby)
    (2.875, 21.30, 0.70, "v", -1),  # master bath
    (5.375, 21.30, 0.90, "v", -1),  # lobby <- corridor
    (5.65, 23.625, 0.90, "h", +1),  # top bedroom
    (7.325, 22.15, 0.80, "v", +1),  # central bath
    (7.325, 19.90, 0.80, "v", +1),  # maid room
    (4.375, 19.90, 0.90, "v", -1),  # side bedroom, opposite maid door
    (4.375, 8.30, 0.90, "v", -1),   # kitchen
    (7.325, 10.20, 0.80, "v", +1),  # bath beside kitchen
    (2.375, 6.05, 0.70, "v", -1),   # entrance bath
    (3.70, 5.375, 0.90, "h", -1),   # guest room
    (7.325, 5.95, 1.00, "v", -1),   # apartment entrance (fire-rated door)
]
openings = [
    box(1.75, 12.75, 2.15, 15.75),   # balcony sliding door 3.0
    box(4.45, 16.70, 9.45, 16.95),   # corridor -> living/dining
    box(7.15, 16.90, 7.50, 19.25),   # corridor widens beside maid room
    box(7.05, 11.75, 7.30, 16.80),   # living / dining fully open
    box(4.45, 11.55, 7.20, 11.80),   # hall -> living
    box(4.45, 7.40, 7.20, 7.65),     # entrance -> hall
]
windows = [
    (0.0, 21.35, EXT, 22.25), (0.0, 18.00, EXT, 19.80), (0.0, 12.25, EXT, 16.25),
    (0.0, 8.70, EXT, 10.50), (0.0, 6.05, EXT, 6.85), (0.0, 1.80, EXT, 3.80),
    (1.80, H - EXT, 3.80, H), (6.45, H - EXT, 8.45, H),
    (1.80, 0.0, 3.80, EXT),
]
core_window = (CX - 1.0, 0.0, CX + 1.0, EXT)

# ---------------- geometry ----------------
spaces = [box(*r[0]) for r in rooms.values()] + [box(*mirror_rect(r[0])) for r in rooms.values()]
spaces.append(box(*core))

def door_gap(x, y, w, d, s):
    return box(x - 0.2, y, x + 0.2, y + w) if d == "v" else box(x, y - 0.2, x + w, y + 0.2)

gaps = []
for dd in doors:
    g = door_gap(*dd)
    gaps += [g, box(*mirror_rect(g.bounds))]
for o in openings:
    gaps += [o, box(*mirror_rect(o.bounds))]
win_rects = windows + [mirror_rect(w) for w in windows] + [core_window]
gaps += [box(*w) for w in win_rects]

walls = box(0, 0, W, H).difference(unary_union(spaces)).difference(unary_union(gaps))

# ---------------- DXF setup ----------------
doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.M
doc.header["$INSUNITS"] = 6
doc.header["$MEASUREMENT"] = 1
for n, (c, lw) in {
    "A-WALL": (7, 50), "A-WALL-PATT": (8, 0), "A-DOOR": (3, 18), "A-GLAZ": (4, 18),
    "A-FLOR-STRS": (6, 18), "A-ANNO-TEXT": (7, 25),
    "A-ANNO-DIMS": (1, 18), "A-AREA": (7, 9), "A-ANNO-TTLB": (7, 35),
}.items():
    doc.layers.add(n, color=c, lineweight=lw)
for n in ("A-ANNO-TEXT", "A-AREA", "A-ANNO-TTLB"):
    doc.layers.get(n).rgb = (0, 0, 0)
doc.layers.remove("A-FURN") if "A-FURN" in doc.layers else None
msp = doc.modelspace()

def add_walls(poly):
    for g in (poly.geoms if isinstance(poly, MultiPolygon) else [poly]):
        if g.area < 1e-4:
            continue
        for ring in [g.exterior, *g.interiors]:
            msp.add_lwpolyline(list(ring.coords), close=True, dxfattribs={"layer": "A-WALL"})
        h = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-PATT"})
        h.paths.add_polyline_path(list(g.exterior.coords), is_closed=True, flags=1)
        for i in g.interiors:
            h.paths.add_polyline_path(list(i.coords), is_closed=True, flags=16)

add_walls(walls)

# ---------------- doors ----------------
def arc_between(c, p1, p2, layer):
    a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0])) % 360
    a2 = math.degrees(math.atan2(p2[1] - c[1], p2[0] - c[0])) % 360
    r = math.dist(c, p1)
    if round((a2 - a1) % 360) == 90:
        msp.add_arc(c, r, a1, a2, dxfattribs={"layer": layer})
    else:
        msp.add_arc(c, r, a2, a1, dxfattribs={"layer": layer})

for (x, y, w, d, s) in doors:
    if d == "v":
        hinge, leaf, closed = (x, y), (x + s * w, y), (x, y + w)
    else:
        hinge, leaf, closed = (x, y), (x, y + s * w), (x + w, y)
    for m in (False, True):
        f = (lambda p: (mx(p[0]), p[1])) if m else (lambda p: p)
        msp.add_line(f(hinge), f(leaf), dxfattribs={"layer": "A-DOOR"})
        arc_between(f(hinge), f(leaf), f(closed), "A-DOOR")

# sliding balcony doors
for r in (openings[0].bounds, mirror_rect(openings[0].bounds)):
    x1, y1, x2, y2 = r
    xm, ym = (x1 + x2) / 2, (y1 + y2) / 2
    msp.add_line((xm - 0.04, y1), (xm - 0.04, ym + 0.1), dxfattribs={"layer": "A-DOOR"})
    msp.add_line((xm + 0.04, ym - 0.1), (xm + 0.04, y2), dxfattribs={"layer": "A-DOOR"})

# windows
for (x1, y1, x2, y2) in win_rects:
    msp.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True, dxfattribs={"layer": "A-GLAZ"})
    for f in (0.4, 0.6):
        if (x2 - x1) < (y2 - y1):
            xx = x1 + f * (x2 - x1); msp.add_line((xx, y1), (xx, y2), dxfattribs={"layer": "A-GLAZ"})
        else:
            yy = y1 + f * (y2 - y1); msp.add_line((x1, yy), (x2, yy), dxfattribs={"layer": "A-GLAZ"})

# ---------------- stairs ----------------
def flight(x1, y1, x2, y2, tread=0.28):
    L = {"layer": "A-FLOR-STRS"}
    msp.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True, dxfattribs=L)
    y = y1 + tread
    while y < y2 - 1e-6:
        msp.add_line((x1, y), (x2, y), dxfattribs=L); y += tread
    xm = (x1 + x2) / 2
    msp.add_line((xm, y1 + 0.3), (xm, y2 - 0.3), dxfattribs=L)
    msp.add_lwpolyline([(xm - 0.12, y2 - 0.55), (xm, y2 - 0.3), (xm + 0.12, y2 - 0.55)], dxfattribs=L)

flight(7.85, 0.80, CX - 0.12, 5.00)
flight(CX + 0.12, 0.80, W - 7.85, 5.00)
msp.add_line((CX, 0.80), (CX, 5.00), dxfattribs={"layer": "A-WALL"})

# ---------------- labels ----------------
def text(s, x, y, h, layer="A-ANNO-TEXT"):
    msp.add_text(s, height=h, dxfattribs={"layer": layer, "style": "OpenSans"}).set_placement(
        (x, y), align=TextEntityAlignment.MIDDLE_CENTER)

label_shift = {"master": (0, -1.2), "bed_top": (0, -0.9), "living": (0.6, -0.1), "guest": (0, -0.4),
               "kitchen": (0.3, 0.2), "bed_l": (0.9, 0)}
for k, (r, name, show) in rooms.items():
    if not name:
        continue
    x1, y1, x2, y2 = r
    w, h = x2 - x1, y2 - y1
    small = min(w, h) < 2.6
    th = 0.17 if small else 0.28
    dx, dy = label_shift.get(k, (0, 0))
    for cx in ((x1 + x2) / 2 + dx, mx((x1 + x2) / 2 + dx)):
        cy = (y1 + y2) / 2 + dy
        if k in ("ward",):
            text(name, cx, cy, 0.15); continue
        text(name, cx, cy + th * 0.9, th)
        if show:
            text(f"{w:.2f} x {h:.2f}", cx, cy - th * 0.4, th * 0.8)
            text(f"{w * h:.2f} m²", cx, cy - th * 1.5, th * 0.6, "A-AREA")
text("COMMON STAIR / LOBBY", CX, 7.4, 0.25)
text(f"{core[2]-core[0]:.2f} x {core[3]-core[1]:.2f}", CX, 7.0, 0.2)

# ---------------- dimensions ----------------
OVR = {"dimtxt": 0.18, "dimasz": 0.12, "dimexo": 0.08, "dimdec": 2, "dimzin": 0, "dimlfac": 1.0, "dimtad": 1, "dimtih": 0, "dimtoh": 0}

def chain_h(xs, y_ref, base):
    for a, b in zip(xs, xs[1:]):
        msp.add_linear_dim(base=(a, base), p1=(a, y_ref), p2=(b, y_ref), dimstyle="EZDXF",
                           override=OVR, dxfattribs={"layer": "A-ANNO-DIMS"}).render()

def chain_v(ys, x_ref, base):
    for a, b in zip(ys, ys[1:]):
        msp.add_linear_dim(base=(base, a), p1=(x_ref, a), p2=(x_ref, b), angle=90, dimstyle="EZDXF",
                           override=OVR, dxfattribs={"layer": "A-ANNO-DIMS"}).render()

xs_bot = [0, 0.3, 7.2, 7.45, W-7.45, W-7.2, W-0.3, W]
xs_top = [0, 0.3, 5.3, 5.45, 9.45, W-9.45, W-5.45, W-5.3, W-0.3, W]
ys_left = [0, 0.3, 5.3, 5.45, 7.45, 7.6, 11.6, 11.75, 16.75, 16.9, 20.9, 21.05, 22.55, 22.7, 27.7, 28.0]
chain_h(xs_bot, 0, -1.0); chain_h([0, W], 0, -2.0)
chain_h(xs_top, H, H + 1.0); chain_h([0, W], H, H + 2.0)
chain_v(ys_left, 0, -1.0); chain_v([0, H], 0, -2.0)
chain_v(ys_left, W, W + 1.0)

doc.saveas(str(OUT / "Floor_Layout.dxf"))

# self-check of requested sizes
req = {"master": (5, 5), "guest": (6.9, 5), "living": (5, 5), "bed_top": (4, 4), "bed_l": (4, 4),
       "kitchen": (4, 4), "ensuite": (2.5, 1.5), "bath_c": (2, 2), "bath_s": (2, 2), "bath_e": (2, 2), "maid": (2, 2)}
for k, (rw, rh) in req.items():
    x1, y1, x2, y2 = rooms[k][0]
    ok = abs((x2 - x1) - rw) < 1e-9 and abs((y2 - y1) - rh) < 1e-9
    print(f"{k:8s} {x2-x1:.2f} x {y2-y1:.2f}  {'OK' if ok else 'MISMATCH'}")
