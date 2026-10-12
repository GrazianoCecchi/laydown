"""
Imposition engine — renders a sheet plan into an output PDF.

Uses PyMuPDF show_pdf_page for vector-quality placement (zero rasterization).
Layout decisions live in :mod:`laydown.engine.geometry`; this module only turns
the plan it produces into pages.
"""

import os
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import fitz

from laydown.engine.data_model import Project
from laydown.engine.geometry import (
    Sheet,
    bleed_caps,
    booklet_page_order,  # re-exported: callers import it from here
    clamped_clip,
    margins_pt,
    place_page,
    sheet_plan,
    sheet_size_pt,
    source_boxes,
)
from laydown.engine.marks import draw_marks
from laydown.engine.preprocessors import apply_preprocessors, normalise_rotation
from laydown.engine.utils import parse_page_range

__all__ = ["impose", "impose_to_temp", "booklet_page_order", "ImposeResult", "PlacedPage"]


@dataclass(frozen=True)
class PlacedPage:
    """Where one source page actually ended up on a sheet."""
    trim: fitz.Rect      # the cut line on the sheet — not the cell, which may be larger
    page_number: int     # 1-based, in the document as the preprocessors left it


@dataclass(frozen=True)
class ImposeResult:
    """
    What an imposition run produced.

    ``placed`` is the geometry the run actually used, handed back so callers that
    need to annotate the output (the preview overlay) can describe *this* run rather
    than recompute an opinion about it and quietly disagree.
    """
    sheets: int
    placed: List[List[PlacedPage]] = field(default_factory=list)


# ──────────────────────────────────────────────────────
#  Imposition entry point
# ──────────────────────────────────────────────────────


def impose(
    project: Project,
    output_path: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> int:
    """
    Run the full pipeline: preprocess → plan → place → marks → save.

    Returns the number of output pages (sides) written.
    """
    return impose_detailed(project, output_path, progress_callback).sheets


def impose_detailed(
    project: Project,
    output_path: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> ImposeResult:
    """As :func:`impose`, but also reports the placement geometry it used."""
    if not project.source_pdf_path:
        raise ValueError("No source PDF set")

    src_doc = fitz.open(project.source_pdf_path)
    try:
        if len(src_doc) == 0:
            raise ValueError("Source PDF has no pages")

        # May hand back a different document (see apply_preprocessors' contract).
        work_doc = apply_preprocessors(src_doc, project.preprocessors)
        # Bake any /Rotate into the content stream before a box is read or a page
        # placed — after the preprocessors, so the Rotate preprocessor is covered
        # too. In-memory only; the source file on disk is never touched.
        normalise_rotation(work_doc)
        try:
            expr = project.layout.page_range.strip()
            page_indices = (
                parse_page_range(expr, len(work_doc)) if expr else list(range(len(work_doc)))
            )
            plan = sheet_plan(project, page_indices)
            return _render(project, work_doc, plan, output_path, progress_callback)
        finally:
            if work_doc is not src_doc:
                work_doc.close()
    finally:
        src_doc.close()


def impose_to_temp(
    project: Project,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Tuple[str, ImposeResult]:
    """
    Impose to a temporary file and return (tmp_path, result).

    Caller is responsible for deleting the temp file.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        return tmp_path, impose_detailed(project, tmp_path, progress_callback)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ──────────────────────────────────────────────────────
#  Rendering the plan
# ──────────────────────────────────────────────────────


def _layout_info(project: Project, sheet: Sheet) -> str:
    preset = project.sheet.preset
    if sheet.side:
        return f"Booklet on {preset} — {sheet.side}"
    from laydown.engine.geometry import grid_for
    cols, rows = grid_for(project.layout)
    return f"{cols * rows}-Up on {preset}"


def _render(
    project: Project,
    src_doc: fitz.Document,
    plan: List[Sheet],
    output_path: str,
    progress_callback: Optional[Callable],
) -> ImposeResult:
    sheet_w, sheet_h = sheet_size_pt(project)
    ml, mr, mt, mb = margins_pt(project)
    filename = os.path.basename(project.source_pdf_path)
    out_doc = fitz.open()
    placed: List[List[PlacedPage]] = []

    try:
        for si, sheet in enumerate(plan):
            if progress_callback:
                progress_callback(si, len(plan))

            page = out_doc.new_page(width=sheet_w, height=sheet_h)
            trim_rects: List[fitz.Rect] = []
            on_this_sheet: List[PlacedPage] = []

            for placement in sheet.placements:
                src_page = src_doc[placement.page_index]
                place, clip = source_boxes(src_page, project.source)
                target, trim, rotate = place_page(
                    placement.cell, place, clip, allow_rotate=project.layout.auto_rotate)
                # Bleed is welcome on bare paper, but never over a
                # neighbour's live area or across a booklet fold. Each
                # side is capped in sheet space and the clip shrinks to
                # match; the place box stays put, so the trim (and the
                # marks that follow it) do not move. The caps apply with
                # no bleed too: there the clip starts equal to the place
                # box, and a negative fold cap — creep pushed the trim
                # past the fold — is the only thing that can cut in.
                caps = bleed_caps(project, sheet, placement.cell, trim)
                clamped = clamped_clip(place, clip, trim, rotate, caps)
                if clamped != clip:
                    clip = clamped
                    target, trim, rotate = place_page(
                        placement.cell, place, clip,
                        allow_rotate=project.layout.auto_rotate)

                if clip.width > 0 and clip.height > 0 and target.width > 0 and target.height > 0:
                    page.show_pdf_page(
                        target, src_doc, placement.page_index,
                        clip=clip, rotate=rotate, keep_proportion=True, overlay=True,
                    )
                # Marks follow the trim rect, not the cell: a page whose proportions
                # differ from the cell is letterboxed inside it, and crop marks drawn
                # on the cell would sit off the page edge.
                trim_rects.append(trim)
                on_this_sheet.append(PlacedPage(trim=trim, page_number=placement.page_index + 1))

            draw_marks(
                page, project.marks, trim_rects,
                sheet_w, sheet_h, ml, mr, mt, mb,
                sheet_num=sheet.number, total_sheets=sheet.total,
                filename=filename,
                layout_info=_layout_info(project, sheet),
                fold_x=sheet.fold_x,
            )
            placed.append(on_this_sheet)

        if progress_callback:
            progress_callback(len(plan), len(plan))

        out_doc.save(output_path, garbage=4, deflate=True)
    finally:
        out_doc.close()
    return ImposeResult(sheets=len(plan), placed=placed)
