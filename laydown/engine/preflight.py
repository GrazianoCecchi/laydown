"""
Preflight — say what's wrong before the press does.

0.2.0 discovered problems at export: "Margins/gutters too large for sheet size"
arrived as an exception after the operator hit Generate, and subtler issues (a source
with no trim box, bleed with nowhere to go, a page count that doesn't fold) arrived
at the guillotine. Imposition Wizard has had a preflight dashboard for years; this is
the same idea sized to Laydown.

Checks are pure functions of a Project plus the source document, so they run on every
settings change without touching the UI.

The source is inspected exactly as the imposition will see it: preprocessors applied
(on a copy — the caller's document is never touched) and the page range selected
before any page is looked at. And preflight never raises for a document it cannot
read — an unreadable document is an ERROR finding, not a stack trace, because the
operator cannot act on what the strip never shows.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import fitz

from laydown.engine.data_model import LayoutType, MarkType, Project, SourceBox
from laydown.engine.geometry import cell_grid, grid_for, source_boxes
from laydown.engine.preprocessors import apply_preprocessors
from laydown.engine.utils import mm_to_pt, parse_page_range, pt_to_mm


class Severity(Enum):
    ERROR = "Error"      # imposition will fail or the output is unusable
    WARNING = "Warning"  # it will produce output, but probably not what was wanted
    NOTE = "Note"        # worth knowing


@dataclass(frozen=True)
class Finding:
    severity: Severity
    message: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.severity.value}: {self.message}"


def preflight(project: Project, source: Optional[fitz.Document] = None) -> List[Finding]:
    """
    Everything worth telling the operator about this job, worst first.

    *source* is optional so the sheet-geometry checks work with no PDF loaded.
    """
    findings: List[Finding] = []
    findings += _check_sheet(project)
    findings += _check_marks(project)
    findings += _check_bleed_overlap(project)
    if source is not None:
        findings += _check_document(project, source)
    findings.sort(key=lambda f: list(Severity).index(f.severity))
    return findings


def _check_sheet(project: Project) -> List[Finding]:
    try:
        cols, rows = grid_for(project.layout)
    except ValueError as exc:
        return [Finding(Severity.ERROR, str(exc))]

    if project.layout.layout_type == LayoutType.BOOKLET:
        cols, rows = 2, 1
    try:
        cell_grid(project, cols, rows)
    except ValueError as exc:
        return [Finding(
            Severity.ERROR, str(exc),
            "Reduce the margins or gutters, or use a larger sheet.",
        )]
    return []


# ── marks ───────────────────────────────────────────


def _check_marks(project: Project) -> List[Finding]:
    """
    A custom mark whose artwork is missing is skipped silently at export time —
    marks.py must never take a job down over a mark — so this is where it is said.
    """
    findings: List[Finding] = []
    for mark in project.marks:
        if mark.mark_type != MarkType.CUSTOM_MARK or not mark.enabled:
            continue
        path = mark.mark_pdf_path.strip()
        if not path:
            continue  # nothing configured; export skips it quietly too
        name = os.path.basename(path)
        if not os.path.isfile(path):
            findings.append(Finding(
                Severity.WARNING,
                f"Custom mark PDF '{name}' does not exist, so the mark will not be drawn",
                f"The mark is configured to use {path}, but there is no file there, "
                "so it is silently skipped at export time.",
            ))
            continue
        try:
            with fitz.open(path) as art:
                if not len(art):
                    raise ValueError("no pages")
        except Exception:
            findings.append(Finding(
                Severity.WARNING,
                f"Custom mark PDF '{name}' cannot be opened, so the mark will not be drawn",
                "A mark PDF that is not readable as a PDF with pages is silently "
                "skipped at export time.",
            ))
    return findings


# ── bleed and its neighbours ───────────────────────


def _check_bleed_overlap(project: Project) -> List[Finding]:
    bleed = project.source.bleed_mm
    if bleed <= 0:
        return []

    if project.layout.layout_type == LayoutType.BOOKLET:
        # The fold is a cut line with the facing page immediately beyond it, so
        # bleed at the fold side prints on that page's live area. The engine will
        # clamp bleed at a fold; until then, any bleed on a booklet gets warned.
        return [Finding(
            Severity.WARNING,
            f"{bleed:g} mm of bleed prints across the booklet fold onto the facing page",
            "The two pages sharing a fold are guillotined apart along it, so bleed at "
            "the fold side lands on the facing page's live area. Keep bleed at 0 for a "
            "booklet, or widen the gutter to at least twice the bleed so the bleed "
            "stops short of the fold.",
        )]

    try:
        cols, rows = grid_for(project.layout)
    except ValueError:
        return []  # the sheet check has already reported the broken layout

    tight = []
    if cols > 1 and 2 * bleed > project.layout.gutter_h_mm + 0.01:
        tight.append(f"the horizontal gutter is {project.layout.gutter_h_mm:g} mm")
    if rows > 1 and 2 * bleed > project.layout.gutter_v_mm + 0.01:
        tight.append(f"the vertical gutter is {project.layout.gutter_v_mm:g} mm")
    if not tight:
        return []
    return [Finding(
        Severity.WARNING,
        f"{bleed:g} mm of bleed is wider than half the gutter, so it reaches the "
        f"neighbouring page ({'; '.join(tight)})",
        "Neighbouring pages' bleeds meet in the gutter as soon as the gutter is "
        "narrower than twice the bleed, and a gutter narrower than the bleed itself "
        "puts one page's ink on its neighbour's live area. Widen the gutters to at "
        "least twice the bleed, or reduce the bleed.",
    )]


# ── the source document ────────────────────────────


def _check_document(project: Project, source: fitz.Document) -> List[Finding]:
    """
    Inspect the source as the imposition will see it — and never raise.

    Anything unexpected while reading the document becomes an ERROR finding rather
    than an exception, so a broken document is reported instead of blanking the
    preflight strip (the UI used to swallow the exception and show nothing at all).
    """
    try:
        return _inspect_document(project, source)
    except Exception as exc:
        return [Finding(
            Severity.ERROR,
            f"Preflight could not inspect this PDF: {str(exc).rstrip('.')}",
            "The document could not be read the way the imposition will read it, so "
            "nothing else about it could be checked.",
        )]


def _inspect_document(project: Project, source: fitz.Document) -> List[Finding]:
    if source.is_encrypted:
        return [Finding(
            Severity.ERROR,
            "The source PDF is password-protected and cannot be imposed",
            "Imposition reads the source without a password, so an encrypted PDF "
            "fails at export. Save a decrypted copy (print-to-PDF or "
            "qpdf --decrypt) and load that.",
        )]
    if len(source) == 0:
        return [Finding(
            Severity.ERROR,
            "The source PDF has no pages",
            "Every layout needs at least one page.",
        )]

    try:
        work, to_close = _prepared_doc(project, source)
    except ValueError as exc:
        return [Finding(
            Severity.ERROR,
            f"A preprocessor cannot be applied: {str(exc).rstrip('.')}",
            "Imposition runs the same steps on the same pages, so the export will "
            "fail the same way.",
        )]

    try:
        expr = project.layout.page_range.strip()
        try:
            indices = (
                parse_page_range(expr, len(work)) if expr else list(range(len(work)))
            )
        except ValueError as exc:
            return [Finding(
                Severity.ERROR,
                f"The page range cannot be used: {str(exc).rstrip('.')}",
                "Imposition reads the same page range, so the export will fail the "
                "same way.",
            )]
        pages = [work[i] for i in indices]
        source_pages = [source[i] for i in indices] if len(source) >= len(work) else pages
        findings = _check_pages(project, pages, source_pages=source_pages)
        findings += _check_fit(project, pages)
        return findings
    finally:
        for doc in to_close:
            doc.close()


def _prepared_doc(project: Project, source: fitz.Document):
    """
    (document, to_close) — the source as the preprocessors leave it.

    Rotate and Reorder rewrite pages in place, and apply_preprocessors flattens
    annotations in place. When any preprocessor step is enabled or the source carries
    annotations/widgets, the chain runs on a copy rebuilt from the source's bytes
    and the caller's document is never touched. With no steps and no annotations, the
    source is only read, so it is used directly and there is nothing to close.
    """
    has_steps = any(step.enabled for step in project.preprocessors)
    has_annots = any(p.first_annot or p.first_widget for p in source)
    if not has_steps and not has_annots:
        return source, []
    copy = fitz.open("pdf", source.tobytes())
    try:
        work = apply_preprocessors(copy, project.preprocessors)
    except Exception:
        copy.close()
        raise
    return work, [copy] if work is copy else [copy, work]


def _raw_box(page: fitz.Page, which: SourceBox) -> fitz.Rect:
    if which == SourceBox.MEDIA:
        return page.mediabox
    if which == SourceBox.CROP:
        return page.cropbox
    if which == SourceBox.BLEED:
        return page.bleedbox
    return page.trimbox


def _box_as_rendered(page: fitz.Page, box: fitz.Rect) -> fitz.Rect:
    """
    A raw PDF box moved into the space the page renders in — page.rect's.

    The box getters report /Rotate-unrotated coordinates while page.rect is the
    turned page, so comparing them directly answers the wrong question on a
    rotated page. page.rect is exactly the cropbox pushed through
    page.rotation_matrix and shifted to the origin, so the same trip puts a raw
    box alongside it.
    """
    m = page.rotation_matrix
    crop = page.cropbox * m
    dx = page.rect.x0 - crop.x0
    dy = page.rect.y0 - crop.y0
    r = box * m
    return fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)


def _available_bleed_mm(page: fitz.Page, which: SourceBox) -> float:
    """How much artwork exists beyond the imposed box, on its tightest side."""
    r = page.rect
    p = _box_as_rendered(page, _raw_box(page, which))
    return pt_to_mm(min(p.x0 - r.x0, r.x1 - p.x1, p.y0 - r.y0, r.y1 - p.y1))


def _is_printable_annot(annot: fitz.Annot) -> bool:
    flags = annot.flags
    is_print = bool(flags & fitz.PDF_ANNOT_IS_PRINT)
    is_hidden = bool(flags & fitz.PDF_ANNOT_IS_HIDDEN)
    # Sticky notes ('Text' annot) do not render text into page content
    return is_print and not is_hidden and annot.type[1] != "Text"


def _is_filled_widget(widget: fitz.Widget) -> bool:
    val = widget.field_value
    return bool(val is not None and str(val).strip())


def _check_pages(
    project: Project,
    pages: List[fitz.Page],
    source_pages: Optional[List[fitz.Page]] = None,
) -> List[Finding]:
    findings: List[Finding] = []
    if source_pages is None:
        source_pages = pages

    if project.source.box == SourceBox.TRIM:
        # TrimBox defaults to the CropBox, so an equal pair means "no trim box" —
        # a comparison inside one unrotated space, so page rotation cannot flip
        # the answer either way.
        lacking = sum(1 for p in pages if p.trimbox == p.cropbox)
        if lacking == len(pages):
            findings.append(Finding(
                Severity.NOTE,
                "This PDF has no trim box, so the whole page is being imposed",
                "That is the right behaviour for an ordinary PDF. If it was exported "
                "for print and should carry a trim box, check the export settings.",
            ))
        elif lacking:
            findings.append(Finding(
                Severity.NOTE,
                f"{lacking} of {len(pages)} pages have no trim box, "
                "so those pages are imposed whole",
                "A page without a trim box falls back to its crop box, which is the "
                "right behaviour for an ordinary PDF.",
            ))

    if project.source.bleed_mm > 0:
        available = min(_available_bleed_mm(p, project.source.box) for p in pages)
        if project.source.bleed_mm > available + 0.01:
            findings.append(Finding(
                Severity.WARNING,
                f"Asked for {project.source.bleed_mm:g} mm of bleed but the tightest "
                f"page only carries {max(available, 0):.1f} mm",
                "The bleed is clamped to the artwork that exists, so the cut edge may "
                "run out of ink.",
            ))

    sizes = {(round(p.rect.width, 1), round(p.rect.height, 1)) for p in pages}
    if len(sizes) > 1:
        findings.append(Finding(
            Severity.WARNING,
            f"The source mixes {len(sizes)} different page sizes",
            "Each page is fitted to its cell individually, so they will not line up.",
        ))

    n_annots = sum(1 for p in source_pages for _ in p.annots())
    n_fields = sum(1 for p in source_pages for _ in p.widgets())
    if n_annots or n_fields:
        carried = " and ".join(
            f"{n} {name}{'s' if n != 1 else ''}"
            for n, name in ((n_annots, "annotation"), (n_fields, "form field"))
            if n
        )
        has_printable = any(
            _is_printable_annot(a) for p in source_pages for a in p.annots()
        ) or any(
            _is_filled_widget(w) for p in source_pages for w in p.widgets()
        )
        if has_printable:
            findings.append(Finding(
                Severity.NOTE,
                f"The source carries {carried} that will be flattened into the output (screen-only ones are left out)",
                "Printable annotations and filled form fields are flattened into the page "
                "content; screen-only ones are left out.",
            ))
        else:
            findings.append(Finding(
                Severity.WARNING,
                f"The source carries {carried} that will not appear in the output",
                "Pages are placed with their printed contents only; annotations and form "
                "field values are dropped. Flatten them into the page contents first "
                "(e.g. print-to-PDF) if they must print.",
            ))

    return findings


def _check_fit(project: Project, pages: List[fitz.Page]) -> List[Finding]:
    findings: List[Finding] = []
    layout = project.layout

    if layout.layout_type == LayoutType.BOOKLET:
        count = len(pages)
        if count % 4:
            findings.append(Finding(
                Severity.NOTE,
                f"{count} pages will be padded to {((count + 3) // 4) * 4} with blanks",
                "A folded sheet always carries four pages. Use 'Blanks in the middle' "
                "to keep a printed back cover.",
            ))
        cols, rows = 2, 1
    else:
        cols, rows = grid_for(layout)

    try:
        cells = cell_grid(project, cols, rows)
    except ValueError:
        return findings  # already reported by _check_sheet

    cell = cells[0]
    scales = []
    wastes = []
    for page in pages:
        place, _ = source_boxes(page, project.source)
        scale = min(cell.width / place.width, cell.height / place.height)
        if layout.auto_rotate:
            scale = max(scale, min(cell.width / place.height, cell.height / place.width))
        scales.append(scale)
        wastes.append(
            1.0 - (place.width * scale * place.height * scale) / (cell.width * cell.height)
        )

    biggest, smallest = max(scales), min(scales)

    if biggest > 1.001:
        findings.append(Finding(
            Severity.WARNING,
            f"Pages are being enlarged to {biggest * 100:.0f}% to fill their cell",
            "Imposition scales pages to fill their cell — up as well as down. Smaller "
            "cells (a smaller sheet, tighter margins, or more pages per sheet) would "
            "keep them at or below 100%, which matters if the job has to hold its "
            "dimensions.",
        ))

    if smallest < 0.999:
        # Graded rather than shouted. Fitting A4 two-up onto A3 with any margin at all
        # lands near 97%, and that is the job this tool is *for* — warning about it
        # every time would just teach the operator to ignore the strip. A serious
        # reduction still gets a warning, and dimensional work still gets told.
        severity = Severity.WARNING if smallest < 0.9 else Severity.NOTE
        findings.append(Finding(
            severity,
            f"Pages are being scaled to {smallest * 100:.0f}% to fit their cell",
            "Imposition scales pages to fill their cell — down here, and up when the "
            "artwork is smaller than the cell. A larger sheet, smaller margins, or "
            "fewer pages per sheet would keep them at 100% — which matters if the job "
            "has to hold its dimensions."
            + ("" if layout.auto_rotate else " Turning pages to fit may also help."),
        ))

    worst_waste = max(wastes)
    if 0.35 < worst_waste < 1.0 and smallest >= 0.999:
        findings.append(Finding(
            Severity.NOTE,
            f"About {worst_waste * 100:.0f}% of each cell is empty",
            "The page proportions differ from the cell, so pages sit letterboxed.",
        ))
    return findings
