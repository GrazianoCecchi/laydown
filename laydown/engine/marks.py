"""
Marks engine — draw print marks on imposed sheets.

Supports: crop marks, trim lines, registration marks, folding marks, text labels.
All drawing uses PDF points; callers pass the output page and layout geometry.

Press convention (owner decision 2026-10-12): crop, gap-crop, registration,
folding, perforation and collating marks print in the **Registration** colour —
a ``/Separation /All`` colour space that inks every plate, so the marks come
out on each separation the press pulls, not just the black one. PyMuPDF's
drawing helpers have no way to express a Separation colour space, so these
marks are emitted as raw content-stream operators (``/CSReg CS 1 SCN`` …)
appended as one extra stream after everything else on the page — the geometry
is exactly where it has always been. The colour bar is DeviceCMYK process
solids (``k``), the trim line and the text label are DeviceGray / K-only CMYK.
No mark is drawn in DeviceRGB any more.
"""

import os
from typing import List, Optional, Tuple

import fitz

from laydown.engine.utils import is_network_path, mm_to_pt
from laydown.engine.data_model import MarkItem, MarkType

# Defaults
_CROP_LEN_MM = 5.0
_CROP_OFF_MM = 3.0
_CROP_W = 0.5
_REG_SIZE_MM = 5.0
_REG_W = 0.3
_LABEL_FONT = 8
_FOLD_LEN_MM = 10.0


# ── the Registration separation ──────────────────────

# "/All": tint 1 maps to 100% of every ink (C1 = 1 1 1 1), i.e. the mark
# prints on all four plates — what "registration black" means on a press.
_REG_CS = "CSReg"
_REG_CS_DEF = (
    "[/Separation /All /DeviceCMYK "
    "<</FunctionType 2 /Domain [0 1] /C0 [0 0 0 0] /C1 [1 1 1 1] /N 1>>]"
)
_KAPPA = 0.5522847498307936  # circle as 4 cubic Béziers


def _n(v: float) -> str:
    return f"{v:.4f}"


def _ensure_registration_cs(page: fitz.Page) -> None:
    """Add the Registration colour space to the page resources (merged, never
    clobbering an existing ColorSpace dict)."""
    doc = page.parent
    holder = page.xref
    for key in ("Resources", "ColorSpace"):
        kind, value = doc.xref_get_key(holder, key)
        if kind == "xref":  # an indirect dict — keep walking into it
            holder = int(value.split()[0])
    doc.xref_set_key(holder, f"ColorSpace/{_REG_CS}", _REG_CS_DEF)


def _flush_registration(page: fitz.Page, ops: List[str]) -> None:
    """Append the queued Registration-colour operators as a new content stream."""
    if not ops:
        return
    _ensure_registration_cs(page)
    # The mark geometry lives in MuPDF's top-left page space; raw content
    # streams are PDF y-up, so the whole stream is flipped once, here, and
    # every operator inside keeps top-left coordinates.
    stream = f"q 1 0 0 -1 0 {_n(page.rect.height)} cm " + " ".join(ops) + " Q"
    doc = page.parent
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, stream.encode())
    doc.xref_set_key(
        page.xref, "Contents",
        "[" + " ".join(f"{x} 0 R" for x in page.get_contents() + [xref]) + "]",
    )


def _reg_line(
    ops: List[str], a: Tuple[float, float], b: Tuple[float, float],
    width: float, dashes: Optional[str] = None,
) -> None:
    d = f"[{dashes}] 0 d " if dashes else ""
    ops.append(
        f"q {_n(width)} w {d}/{_REG_CS} CS 1 SCN "
        f"{_n(a[0])} {_n(a[1])} m {_n(b[0])} {_n(b[1])} l S Q"
    )


def _reg_circle(ops: List[str], cx: float, cy: float, r: float, width: float) -> None:
    k = _KAPPA * r
    path = (
        f"{_n(cx + r)} {_n(cy)} m "
        f"{_n(cx + r)} {_n(cy + k)} {_n(cx + k)} {_n(cy + r)} {_n(cx)} {_n(cy + r)} c "
        f"{_n(cx - k)} {_n(cy + r)} {_n(cx - r)} {_n(cy + k)} {_n(cx - r)} {_n(cy)} c "
        f"{_n(cx - r)} {_n(cy - k)} {_n(cx - k)} {_n(cy - r)} {_n(cx)} {_n(cy - r)} c "
        f"{_n(cx + k)} {_n(cy - r)} {_n(cx + r)} {_n(cy - k)} {_n(cx + r)} {_n(cy)} c"
    )
    ops.append(f"q {_n(width)} w /{_REG_CS} CS 1 SCN {path} S Q")


def _reg_rect_fill(ops: List[str], x0: float, y0: float, x1: float, y1: float) -> None:
    ops.append(
        f"q /{_REG_CS} cs 1 scn {_n(x0)} {_n(y0)} {_n(x1 - x0)} {_n(y1 - y0)} re f Q"
    )


# ── public API ──────────────────────────────────────


def draw_marks(
    page: fitz.Page,
    marks: List[MarkItem],
    cell_rects: List[fitz.Rect],
    sheet_w: float,
    sheet_h: float,
    margin_l: float,
    margin_r: float,
    margin_t: float,
    margin_b: float,
    sheet_num: int = 1,
    total_sheets: int = 1,
    filename: str = "",
    layout_info: str = "",
    fold_x: Optional[float] = None,
) -> None:
    """
    Draw all enabled marks onto *page*.

    Args:
        page:        The output fitz.Page to draw on.
        marks:       List of MarkItem from the project.
        cell_rects:  List of fitz.Rect for each placed page cell.
        sheet_w/h:   Sheet size in points.
        margin_*:    Margins in points.
        sheet_num:   1-based sheet number.
        total_sheets: Total sheets in output.
        filename:    Source PDF filename.
        layout_info: e.g. "2-Up on A3".
        fold_x:      X-coordinate of the fold line (for booklet), or None.
    """
    reg: List[str] = []
    for m in marks:
        if not m.enabled:
            continue
        if m.mark_type == MarkType.CROP_MARKS:
            for r in cell_rects:
                _draw_crop_marks(reg, r, m.crop_length_mm, m.crop_offset_mm, m.crop_width_pt)
        elif m.mark_type == MarkType.GAP_CROP_MARKS:
            _draw_gap_crop_marks(reg, cell_rects, sheet_w, sheet_h,
                                 margin_l, margin_r, margin_t, margin_b,
                                 m.crop_offset_mm, m.crop_width_pt)
        elif m.mark_type == MarkType.TRIM_LINE:
            for r in cell_rects:
                _draw_trim_line(page, r)
        elif m.mark_type == MarkType.REGISTRATION:
            _draw_registration_marks(reg, sheet_w, sheet_h, margin_l, margin_t)
        elif m.mark_type == MarkType.FOLDING_MARKS:
            if fold_x is not None:
                _draw_folding_marks(reg, fold_x, sheet_h, margin_t, margin_b, m.fold_line_length_mm)
        elif m.mark_type == MarkType.PERFORATION_MARKS:
            if fold_x is not None:
                _draw_perforation_marks(reg, fold_x, sheet_h, margin_t, margin_b)
        elif m.mark_type == MarkType.COLOR_BAR:
            _draw_color_bar(page, sheet_w, sheet_h, margin_t, m.patch_size_mm)
        elif m.mark_type == MarkType.TEXT_LABEL:
            _draw_text_label(page, sheet_w, sheet_h, margin_b,
                             sheet_num, total_sheets, filename, layout_info,
                             m.label_font_size or _LABEL_FONT, m.label_text)
        elif m.mark_type == MarkType.COLLATING_MARKS:
            _draw_collating_mark(reg, sheet_w, sheet_h, margin_r, sheet_num, total_sheets)
        elif m.mark_type == MarkType.CUSTOM_MARK:
            _draw_custom_mark(page, m)
    _flush_registration(page, reg)


# ── crop marks ──────────────────────────────────────


def _draw_crop_marks(
    ops: List[str],
    rect: fitz.Rect,
    length_mm: float = _CROP_LEN_MM,
    offset_mm: float = _CROP_OFF_MM,
    width: float = _CROP_W,
) -> None:
    ml = mm_to_pt(length_mm)
    mo = mm_to_pt(offset_mm)
    x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
    lines = [
        ((x0 - mo - ml, y0), (x0 - mo, y0)),
        ((x0, y0 - mo - ml), (x0, y0 - mo)),
        ((x1 + mo, y0), (x1 + mo + ml, y0)),
        ((x1, y0 - mo - ml), (x1, y0 - mo)),
        ((x0 - mo - ml, y1), (x0 - mo, y1)),
        ((x0, y1 + mo), (x0, y1 + mo + ml)),
        ((x1 + mo, y1), (x1 + mo + ml, y1)),
        ((x1, y1 + mo), (x1, y1 + mo + ml)),
    ]
    for a, b in lines:
        _reg_line(ops, a, b, width)


# ── gap crop marks ──────────────────────────────────


def _draw_gap_crop_marks(
    ops: List[str],
    cell_rects: List[fitz.Rect],
    sheet_w: float, sheet_h: float,
    margin_l: float, margin_r: float, margin_t: float, margin_b: float,
    offset_mm: float = _CROP_OFF_MM,
    width: float = _CROP_W,
) -> None:
    """
    Cut marks that run from the sheet edge to each page edge.

    Where ordinary crop marks sit at the page's corners, these span the whole margin
    and gutter, so a guillotine operator can line the blade up on a single cut that
    crosses the sheet — the usual choice for a ganged sheet of cards or labels.
    """
    if not cell_rects:
        return
    gap = mm_to_pt(offset_mm)
    xs = sorted({r.x0 for r in cell_rects} | {r.x1 for r in cell_rects})
    ys = sorted({r.y0 for r in cell_rects} | {r.y1 for r in cell_rects})

    for x in xs:
        _reg_line(ops, (x, 0), (x, max(0.0, margin_t - gap)), width)
        _reg_line(ops, (x, min(sheet_h, sheet_h - margin_b + gap)), (x, sheet_h), width)
    for y in ys:
        _reg_line(ops, (0, y), (max(0.0, margin_l - gap), y), width)
        _reg_line(ops, (min(sheet_w, sheet_w - margin_r + gap), y), (sheet_w, y), width)


# ── trim line ───────────────────────────────────────


def _draw_trim_line(page: fitz.Page, rect: fitz.Rect) -> None:
    # A light DeviceGray guide, not a press mark — deliberately not the
    # Registration separation, and not RGB either.
    page.draw_rect(rect, color=(0.6,), width=0.25)


# ── registration marks ──────────────────────────────


def _draw_reg_mark(ops: List[str], x: float, y: float, size_mm: float = _REG_SIZE_MM) -> None:
    s = mm_to_pt(size_mm)
    h = s / 2
    _reg_line(ops, (x - h, y), (x + h, y), _REG_W)
    _reg_line(ops, (x, y - h), (x, y + h), _REG_W)
    _reg_circle(ops, x, y, s / 4, _REG_W)


def _draw_registration_marks(
    ops: List[str],
    sw: float, sh: float,
    ml: float, mt: float,
) -> None:
    min_margin = min(ml, mt)
    if min_margin < mm_to_pt(8):
        return
    off = min_margin / 2
    cx, cy = sw / 2, sh / 2
    for x, y in [
        (off, off), (sw - off, off), (off, sh - off), (sw - off, sh - off),
        (cx, off), (cx, sh - off), (off, cy), (sw - off, cy),
    ]:
        _draw_reg_mark(ops, x, y)


# ── folding marks ───────────────────────────────────


def _draw_folding_marks(
    ops: List[str],
    fold_x: float,
    sheet_h: float,
    margin_t: float,
    margin_b: float,
    length_mm: float = _FOLD_LEN_MM,
) -> None:
    ml = mm_to_pt(length_mm)
    _reg_line(
        ops,
        (fold_x, margin_t / 2 - ml / 2), (fold_x, margin_t / 2 + ml / 2),
        width=0.4, dashes="2 2",
    )
    bottom_y = sheet_h - margin_b / 2
    _reg_line(
        ops,
        (fold_x, bottom_y - ml / 2), (fold_x, bottom_y + ml / 2),
        width=0.4, dashes="2 2",
    )


# ── perforation marks ───────────────────────────────


def _draw_perforation_marks(
    ops: List[str],
    x: float,
    sheet_h: float,
    margin_t: float,
    margin_b: float,
) -> None:
    """A dotted rule up the fold, marking where the sheet gets perforated."""
    _reg_line(ops, (x, max(0.0, margin_t / 2)), (x, sheet_h - margin_b / 2),
               width=0.4, dashes="1 3")


# ── colour bar ──────────────────────────────────────

# Process solids plus two- and three-ink overprints — what a press operator
# reads density and trapping off, as true DeviceCMYK (owner decision
# 2026-10-12; the set was an RGB ramp before). Same count and geometry as the
# old bar; a shop with a house bar can still place it with a Custom Mark.
_COLOR_BAR = [
    (0, 0, 0, 1),  # K
    (1, 0, 0, 0),  # C
    (0, 1, 0, 0),  # M
    (0, 0, 1, 0),  # Y
    (1, 1, 0, 0),  # C+M
    (0, 1, 1, 0),  # M+Y
    (1, 0, 1, 0),  # C+Y
    (1, 1, 1, 0),  # C+M+Y overprint
]


def _draw_color_bar(
    page: fitz.Page,
    sheet_w: float, sheet_h: float,
    margin_t: float,
    patch_mm: float = 5.0,
) -> None:
    size = mm_to_pt(patch_mm)
    if margin_t < size + mm_to_pt(2):
        return
    total = size * len(_COLOR_BAR)
    x = (sheet_w - total) / 2
    y = max(1.0, (margin_t - size) / 2)
    for c, m_, y_, k in _COLOR_BAR:
        page.draw_rect(fitz.Rect(x, y, x + size, y + size),
                       color=(0.5,), width=0.2, fill=(c, m_, y_, k))
        x += size


# ── custom mark ─────────────────────────────────────


def _draw_custom_mark(page: fitz.Page, mark) -> None:
    """
    Stamp a user-supplied PDF onto the sheet, vector intact.

    Studying Imposition Wizard's install folder gave this away: its Placeholders/
    directory holds `missing-bull-eye.pdf`, `missing-color-bar.pdf` and friends — the
    fallbacks shown when a mark's artwork is absent. So a "custom mark" is not drawing
    code at all, it is artwork placed by rule, and show_pdf_page already does exactly
    that. A shop can drop in its own bull's-eye, star target or house colour bar.
    """
    if not mark.mark_pdf_path or is_network_path(mark.mark_pdf_path):
        return  # never touch a network path: Windows would send credentials to it
    if not os.path.isfile(mark.mark_pdf_path):
        return
    try:
        with fitz.open(mark.mark_pdf_path) as art:
            if not len(art):
                return
            source = art[0].rect
            width = mm_to_pt(mark.mark_width_mm)
            height = width * (source.height / source.width) if source.width else width
            x, y = mm_to_pt(mark.mark_x_mm), mm_to_pt(mark.mark_y_mm)
            page.show_pdf_page(fitz.Rect(x, y, x + width, y + height), art, 0,
                               keep_proportion=True, overlay=True)
    except Exception:
        # A bad mark PDF must never take the whole job down; the sheet is still
        # correct without it, and preflight is where this gets reported.
        return


# ── text labels ─────────────────────────────────────


def _draw_text_label(
    page: fitz.Page,
    sw: float, sh: float,
    margin_b: float,
    sheet_num: int, total: int,
    filename: str, layout_info: str,
    font_size: int = _LABEL_FONT,
    label_text: str = "",
) -> None:
    if margin_b < mm_to_pt(6):
        return
    y = sh - margin_b * 0.4
    inset = sw * 0.05
    # A custom label replaces the auto text ("" = auto: filename | layout);
    # the sheet counter on the right always prints.
    if label_text:
        left = label_text
    else:
        left = filename
        if layout_info:
            left += f"  |  {layout_info}" if left else layout_info
    # K-only CMYK — dark on the black plate, invisible on the colour plates,
    # and never DeviceRGB.
    if left:
        page.insert_text((inset, y), left, fontsize=font_size, color=(0, 0, 0, 0.4))
    right = f"Sheet {sheet_num} of {total}"
    tw = fitz.get_text_length(right, fontsize=font_size)
    page.insert_text((sw - inset - tw, y), right, fontsize=font_size, color=(0, 0, 0, 0.4))


# ── collating marks ─────────────────────────────────


def _draw_collating_mark(
    ops: List[str],
    sw: float, sh: float,
    margin_r: float,
    sheet_num: int, total: int,
) -> None:
    """Small stepping rectangle on the spine edge for collation."""
    if total < 2:
        return
    mark_h = mm_to_pt(4)
    mark_w = mm_to_pt(3)
    step = (sh - mm_to_pt(40)) / max(total - 1, 1)
    y = mm_to_pt(20) + (sheet_num - 1) * step
    x = sw - margin_r + mm_to_pt(1)
    _reg_rect_fill(ops, x, y, x + mark_w, y + mark_h)
