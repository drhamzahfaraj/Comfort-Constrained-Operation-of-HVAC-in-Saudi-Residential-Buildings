"""Vector PDF panels of the drawing set for Fig. 3 of the paper (no sheet frame or title block).
Same monochrome plot style as export_pdf.py; scale 1:100, page cropped to the drawing region.
Run after make_dxf.py, make_dxf_single.py and make_building.py:  python src/drawings/make_figure_panels.py
"""
from pathlib import Path
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf, config
from ezdxf import bbox
from ezdxf.math import BoundingBox2d
from export_pdf import plot_style

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "figures"; OUT.mkdir(parents=True, exist_ok=True)

def panel(dxf, region, out, scale_den=100):
    doc = plot_style(ezdxf.readfile(HERE / dxf))
    x1, y1, x2, y2 = region
    def inside(e):
        try:
            b = bbox.extents([e], fast=True)
        except Exception:
            return True
        return b.has_data and x1 <= b.center.x <= x2 and y1 <= b.center.y <= y2
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE, color_policy=config.ColorPolicy.COLOR,
                               lineweight_scaling=1.0, min_lineweight=0.13)
    be = pymupdf.PyMuPdfBackend()
    Frontend(RenderContext(doc), be, config=cfg).draw_layout(doc.modelspace(), filter_func=inside)
    w, h = (x2 - x1) * 1000 / scale_den, (y2 - y1) * 1000 / scale_den          # mm at 1:100
    page = layout.Page(w, h, layout.Units.mm, margins=layout.Margins.all(0))
    data = be.get_pdf_bytes(page, settings=layout.Settings(fit_page=False, scale=1000 / scale_den, max_stroke_width=0.01),
                            render_box=BoundingBox2d([(x1, y1), (x2, y2)]))
    (OUT / out).write_bytes(data); print(out, f"{w:.0f} x {h:.0f} mm")

def ext(dxf, pad=0.3):
    b = bbox.extents(ezdxf.readfile(HERE / dxf).modelspace())
    return (b.extmin.x - pad, b.extmin.y - pad, b.extmax.x + pad, b.extmax.y + pad)

panel("Apartment_Single.dxf", ext("Apartment_Single.dxf"), "fig_cad_apartment.pdf")
panel("Floor_Layout.dxf", ext("Floor_Layout.dxf"), "fig_cad_floor.pdf")
panel("Building_4_Floors.dxf", (40.3, -32.0, 66.4, -12.6), "_elev.pdf")       # south elevation (levels, dims), title cropped
panel("Building_4_Floors.dxf", (-1.0, -31.0, 28.9, -12.6), "_sect.pdf")       # section A-A (cut through the stair), title cropped

def stack(top, bottom, out, gap_mm=8):
    """Stack two 1:100 panels vertically on one page at the same scale, centred (Fig. 3c)."""
    import pymupdf as fitz
    a, b = fitz.open(OUT / top), fitz.open(OUT / bottom)
    ra, rb = a[0].rect, b[0].rect
    W = max(ra.width, rb.width); H = ra.height + rb.height + gap_mm * 72 / 25.4
    doc = fitz.open(); pg = doc.new_page(width=W, height=H)
    pg.show_pdf_page(fitz.Rect((W - ra.width) / 2, 0, (W + ra.width) / 2, ra.height), a, 0)
    pg.show_pdf_page(fitz.Rect((W - rb.width) / 2, H - rb.height, (W + rb.width) / 2, H), b, 0)
    doc.save(OUT / out, garbage=3, deflate=True)
    for f in (top, bottom): (OUT / f).unlink()
    print(out, f"{W * 25.4 / 72:.0f} x {H * 25.4 / 72:.0f} mm")

stack("_elev.pdf", "_sect.pdf", "fig_cad_elevation.pdf")

# PNG previews (for the README and quick viewing; the paper uses the vector PDFs)
import pymupdf as fitz
for name in ("fig_cad_apartment", "fig_cad_floor", "fig_cad_elevation"):
    d = fitz.open(OUT / f"{name}.pdf"); d[0].get_pixmap(dpi=150).save(OUT / f"{name}.png"); print(f"{name}.png")
