"""Vector PDF export of the DXF drawings, AutoCAD-style monochrome plot at true scale.
Plot style: all linework/text black (like monochrome.ctb), wall poché screened grey,
layer lineweights honoured. Drawing unit = metre, paper unit = mm.
"""
from pathlib import Path
import ezdxf
from ezdxf import bbox, colors
from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf, config
from ezdxf.math import BoundingBox2d
import pymupdf as fitz

OUT = str(Path(__file__).resolve().parent) + "/"
SHEETS = {"A3P": (297, 420), "A3L": (420, 297), "A2L": (594, 420)}
GREY = (170, 170, 170)

def plot_style(doc):
    for lay in doc.layers:
        lay.rgb = GREY if lay.dxf.name == "A-WALL-PATT" else (0, 0, 0)
    for e in doc.modelspace():
        if e.dxftype() == "HATCH":
            e.rgb = GREY
            e.dxf.color = 256
        elif e.dxf.hasattr("true_color") or e.dxf.color not in (256, 0):
            e.dxf.color = 256
            if e.dxf.hasattr("true_color"):
                e.dxf.discard("true_color")
    # dimension blocks carry their own colours
    for blk in doc.blocks:
        if blk.name.startswith("*D"):
            for e in blk:
                e.dxf.color = 0          # BYBLOCK -> follows dimension (layer, black)
    return doc

def render_page(doc, region, sheet, scale_den, title):
    """region: (x1,y1,x2,y2) in model metres; returns single-page PDF bytes"""
    x1, y1, x2, y2 = region
    def inside(e):
        try:
            b = bbox.extents([e], fast=True)
        except Exception:
            return True
        if not b.has_data:
            return False
        c = b.center
        return x1 <= c.x <= x2 and y1 <= c.y <= y2
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                               color_policy=config.ColorPolicy.COLOR,
                               lineweight_scaling=1.0, min_lineweight=0.13)
    backend = pymupdf.PyMuPdfBackend()
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(doc.modelspace(), filter_func=inside)
    w, h = SHEETS[sheet]
    page = layout.Page(w, h, layout.Units.mm, margins=layout.Margins.all(10))
    settings = layout.Settings(fit_page=False, scale=1000 / scale_den, max_stroke_width=0.01)
    data = backend.get_pdf_bytes(page, settings=settings, render_box=BoundingBox2d([(x1, y1), (x2, y2)]))
    # sheet frame + caption + scale bar (vector)
    pdf = fitz.open("pdf", data)
    pg = pdf[0]
    mm = 72 / 25.4
    pg.draw_rect(fitz.Rect(10 * mm, 10 * mm, (w - 10) * mm, (h - 10) * mm), color=(0, 0, 0), width=0.7)
    pg.insert_text(((15) * mm, (h - 15) * mm), f"{title}   |   SCALE 1:{scale_den} @ {sheet[:2]}   |   UNITS: m",
                   fontsize=9, fontname="helv", color=(0, 0, 0))
    # scale bar 0-5 m
    bx, by, seg = (w - 15 - 5000 / scale_den) * mm, (h - 16) * mm, 1000 / scale_den * mm
    for i in range(5):
        pg.draw_rect(fitz.Rect(bx + i * seg, by - 1.2 * mm, bx + (i + 1) * seg, by), color=(0, 0, 0),
                     fill=(0, 0, 0) if i % 2 == 0 else None, width=0.4)
    for i in (0, 1, 5):
        pg.insert_text((bx + i * seg - 1.0, by + 3.2 * mm), f"{i}", fontsize=6, fontname="helv")
    pg.insert_text((bx + 5 * seg + 1.5 * mm, by), "m", fontsize=6, fontname="helv")
    return pdf

def export(dxf, pages, out):
    result = fitz.open()
    for region, sheet, title in pages:
        doc = plot_style(ezdxf.readfile(OUT + dxf))
        result.insert_pdf(render_page(doc, region, sheet, 100, title))
    result.set_metadata({"title": out.replace(".pdf", "").replace("_", " "), "creator": "DXF plot (AutoCAD-style monochrome)"})
    result.save(OUT + out, garbage=3, deflate=True)
    print(out, len(result), "pages")

def extents(dxf, pad=1.0):
    doc = ezdxf.readfile(OUT + dxf)
    b = bbox.extents(doc.modelspace())
    return (b.extmin.x - pad, b.extmin.y - pad, b.extmax.x + pad, b.extmax.y + pad)

if __name__ == "__main__":
    export("Floor_Layout.dxf", [(extents("Floor_Layout.dxf"), "A3P", "TYPICAL FLOOR LAYOUT - 2 APARTMENTS")],
           "Floor_Layout.pdf")
    export("Apartment_Single.dxf", [(extents("Apartment_Single.dxf"), "A3P", "SINGLE APARTMENT PLAN")],
           "Apartment_Single.pdf")
    names = ["GROUND FLOOR PLAN", "FIRST FLOOR PLAN", "SECOND FLOOR PLAN", "THIRD FLOOR PLAN", "ROOF PLAN"]
    pages = [((i * 32 - 4.5, -6.0, i * 32 + 25.0, 31.0), "A3P", n) for i, n in enumerate(names)]
    pages += [((-9.5, -34.5, 36.0, -11.5), "A2L", "SECTION A-A"),
              ((37.5, -34.5, 95.0, -11.5), "A2L", "SOUTH ELEVATION + DESIGN NOTES")]
    export("Building_4_Floors.dxf", pages, "Building_4_Floors.pdf")
