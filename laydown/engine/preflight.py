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

from laydown.engine.data_model import LayoutType, MarkType, PageScaling, Project, SourceBox
from laydown.engine.geometry import cell_grid, grid_for, margins_pt, sheet_size_pt, source_boxes
from laydown.engine.preprocessors import apply_preprocessors
from laydown.engine.utils import is_network_path, mm_to_pt, parse_page_range, pt_to_mm


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
            "Riduci i margini o le spaziature (gutter), oppure utilizza un foglio più grande.",
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
        if is_network_path(path):
            # Checked before any filesystem call: even looking at a network path
            # makes Windows sign in to that server with the user's credentials.
            findings.append(Finding(
                Severity.WARNING,
                "Il PDF del marcatore personalizzato si trova su un percorso di rete e non verrà utilizzato — copialo su questo computer",
                f"Il marcatore punta a {path}. Laydown non apre file di rete per i marcatori, pertanto verrà ignorato durante l'esportazione.",
            ))
            continue
        name = os.path.basename(path)
        if not os.path.isfile(path):
            findings.append(Finding(
                Severity.WARNING,
                f"Il PDF del marcatore personalizzato '{name}' non esiste, pertanto il marcatore non verrà disegnato",
                f"Il marcatore è configurato per usare {path}, ma il file non è stato trovato; verrà ignorato all'esportazione.",
            ))
            continue
        try:
            with fitz.open(path) as art:
                if not len(art):
                    raise ValueError("nessuna pagina")
        except Exception:
            findings.append(Finding(
                Severity.WARNING,
                f"Impossibile aprire il PDF del marcatore personalizzato '{name}', pertanto il marcatore non verrà disegnato",
                "Un file di marcatore non leggibile come PDF valido viene ignorato durante l'esportazione.",
            ))
    return findings


# ── bleed and its neighbours ───────────────────────


def _check_bleed_overlap(project: Project) -> List[Finding]:
    bleed = project.source.bleed_mm
    if bleed <= 0:
        return []

    if project.layout.layout_type == LayoutType.BOOKLET:
        return [Finding(
            Severity.WARNING,
            f"{bleed:g} mm di abbondanza oltrepassano la piega dell'opuscolo sulla pagina adiacente",
            "Le due pagine che condividono la piega vengono rifilate lungo la piega stessa. "
            "Imposta l'abbondanza a 0 per gli opuscoli, oppure allarga il gutter ad almeno il doppio dell'abbondanza.",
        )]

    try:
        cols, rows = grid_for(project.layout)
    except ValueError:
        return []

    tight = []
    if cols > 1 and 2 * bleed > project.layout.gutter_h_mm + 0.01:
        tight.append(f"la spaziatura orizzontale è di {project.layout.gutter_h_mm:g} mm")
    if rows > 1 and 2 * bleed > project.layout.gutter_v_mm + 0.01:
        tight.append(f"la spaziatura verticale è di {project.layout.gutter_v_mm:g} mm")
    if not tight:
        return []
    return [Finding(
        Severity.WARNING,
        f"{bleed:g} mm di abbondanza superano la metà della spaziatura, raggiungendo la pagina adiacente ({'; '.join(tight)})",
        "Le abbondanze delle pagine vicine si sovrappongono non appena la spaziatura è inferiore al doppio dell'abbondanza. "
        "Allarga la spaziatura ad almeno il doppio dell'abbondanza oppure riduci l'abbondanza.",
    )]


# ── the source document ────────────────────────────


def _check_document(project: Project, source: fitz.Document) -> List[Finding]:
    try:
        return _inspect_document(project, source)
    except Exception as exc:
        return [Finding(
            Severity.ERROR,
            f"Il controllo preliminare non è riuscito a ispezionare il PDF: {str(exc).rstrip('.')}",
            "Impossibile leggere il documento nella modalità richiesta per l'imposizione.",
        )]


def _inspect_document(project: Project, source: fitz.Document) -> List[Finding]:
    if source.is_encrypted:
        return [Finding(
            Severity.ERROR,
            "Il PDF di origine è protetto da password e non può essere impaginato",
            "L'imposizione richiede un PDF non protetto. Rimuovi la password e ricarica il file.",
        )]
    if len(source) == 0:
        return [Finding(
            Severity.ERROR,
            "Il PDF di origine non contiene pagine",
            "Il documento deve contenere almeno una pagina.",
        )]

    try:
        work, to_close = _prepared_doc(project, source)
    except ValueError as exc:
        return [Finding(
            Severity.ERROR,
            f"Impossibile applicare un preprocessore: {str(exc).rstrip('.')}",
            "L'imposizione applica gli stessi passaggi; l'esportazione fallirebbe allo stesso modo.",
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
                f"Intervallo di pagine non valido: {str(exc).rstrip('.')}",
                "L'intervallo specificato non può essere interpretato.",
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
                "Questo PDF non definisce una TrimBox (rifilo): verrà impaginata l'intera pagina (CropBox/MediaBox)",
                "Questo è il comportamento standard per un PDF generico. Se il file è stato preparato "
                "per la stampa tipografica e dovrebbe contenere una TrimBox, verifica le impostazioni di esportazione.",
            ))
        elif lacking:
            findings.append(Finding(
                Severity.NOTE,
                f"{lacking} su {len(pages)} pagine non hanno una TrimBox: verranno impaginate per intero",
                "Le pagine prive di rifilo TrimBox utilizzano automaticamente la CropBox.",
            ))

    if project.source.bleed_mm > 0:
        available = min(_available_bleed_mm(p, project.source.box) for p in pages)
        if project.source.bleed_mm > available + 0.01:
            findings.append(Finding(
                Severity.WARNING,
                f"Richiesti {project.source.bleed_mm:g} mm di abbondanza, ma la pagina con minor margine dispone solo di {max(available, 0):.1f} mm",
                "L'abbondanza viene limitata alla grafica effettivamente presente nel PDF; il bordo tagliato potrebbe non avere inchiostro a sufficienza.",
            ))

    sizes = {(round(p.rect.width, 1), round(p.rect.height, 1)) for p in pages}
    if len(sizes) > 1:
        findings.append(Finding(
            Severity.WARNING,
            f"Il documento di origine contiene {len(sizes)} formati di pagina differenti",
            "Ciascuna pagina viene posizionata singolarmente e potrebbe non allinearsi uniformemente alle altre.",
        ))

    n_annots = sum(1 for p in source_pages for _ in p.annots())
    n_fields = sum(1 for p in source_pages for _ in p.widgets())
    if n_annots or n_fields:
        parts = []
        if n_annots:
            parts.append(f"{n_annots} annotazione" if n_annots == 1 else f"{n_annots} annotazioni")
        if n_fields:
            parts.append(f"{n_fields} campo modulo" if n_fields == 1 else f"{n_fields} campi modulo")
        carried = " e ".join(parts)
        has_printable = any(
            _is_printable_annot(a) for p in source_pages for a in p.annots()
        ) or any(
            _is_filled_widget(w) for p in source_pages for w in p.widgets()
        )
        if has_printable:
            findings.append(Finding(
                Severity.NOTE,
                f"Il documento contiene {carried} che verranno incorporate nell'output di stampa (gli elementi solo a schermo saranno ignorati)",
                "Le annotazioni stampabili e i campi compilati vengono uniti nei contenuti della pagina.",
            ))
        else:
            findings.append(Finding(
                Severity.WARNING,
                f"Il documento contiene {carried} che non appariranno nella stampa finale",
                "Le pagine vengono posizionate solo con i loro contenuti grafici; le annotazioni a schermo e i moduli non stampabili sono esclusi.",
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
                f"{count} pagine verranno completate a {((count + 3) // 4) * 4} con pagine bianche",
                "Un foglio piegato contiene sempre quattro facciate. Usa 'Pagine bianche al centro' "
                "per preservare l'ultima facciata sul retro della copertina.",
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
        fit = min(cell.width / place.width, cell.height / place.height)
        if layout.auto_rotate:
            fit = max(fit, min(cell.width / place.height, cell.height / place.width))
        scale = 1.0 if layout.scaling == PageScaling.ACTUAL_SIZE else fit
        scales.append(scale)
        wastes.append(
            1.0 - (place.width * scale * place.height * scale) / (cell.width * cell.height)
        )

    biggest, smallest = max(scales), min(scales)

    if layout.scaling == PageScaling.ACTUAL_SIZE:
        sheet_w, sheet_h = sheet_size_pt(project)
        ml, mr, mt, mb = margins_pt(project)
        avail_w = sheet_w - ml - mr
        avail_h = sheet_h - mt - mb
        gh = mm_to_pt(layout.gutter_h_mm)
        gv = mm_to_pt(layout.gutter_v_mm)

        if pages:
            sample_place, _ = source_boxes(pages[0], project.source)
            pw, ph = sample_place.width, sample_place.height
            if layout.auto_rotate and ((cols * ph + (cols - 1) * gh <= avail_w + 0.1) and (rows * pw + (rows - 1) * gv <= avail_h + 0.1)):
                pw, ph = ph, pw
            w_tot = cols * pw + (cols - 1) * gh
            h_tot = rows * ph + (rows - 1) * gv

            if w_tot > sheet_w + 1.0 or h_tot > sheet_h + 1.0:
                findings.append(Finding(
                    Severity.WARNING,
                    "Pagine a dimensione reale (100%): i contenuti eccedono i limiti fisici del foglio",
                    "A grandezza naturale 1:1 le pagine superano il formato carta impostato e risulteranno rifilate o troncate. "
                    "Riduci il numero di pagine per foglio, usa un formato carta maggiore oppure seleziona 'Adatta al foglio'.",
                ))
            elif w_tot > avail_w + 1.0 or h_tot > avail_h + 1.0:
                findings.append(Finding(
                    Severity.WARNING,
                    "Pagine a dimensione reale (100%): i contenuti superano i margini del foglio",
                    "A grandezza naturale 1:1 le pagine invadono i margini impostati del foglio. "
                    "Riduci i margini, aumenta il foglio o seleziona 'Adatta al foglio' per ridurle proporzionalmente.",
                ))
    else:  # FIT_TO_CELL
        if biggest > 1.001:
            findings.append(Finding(
                Severity.WARNING,
                f"Pagine ingrandite al {biggest * 100:.0f}% per riempire la cella",
                "L'imposizione adatta le pagine ingrandendole oltre il 100% per saturare la griglia. "
                "Per preservare le misure originali esatte, seleziona 'Dim. reale (100%)'.",
            ))

        if smallest < 0.999:
            severity = Severity.WARNING if smallest < 0.9 else Severity.NOTE
            findings.append(Finding(
                severity,
                f"Pagine ridimensionate al {smallest * 100:.0f}% per adattarsi al foglio",
                "L'imposizione riduce le pagine per farle rientrare nella griglia del foglio. "
                "Per stamparle a grandezza naturale 1:1, seleziona 'Dim. reale (100%)' o scegli un foglio più grande."
                + ("" if layout.auto_rotate else " L'opzione 'Ruota per adattare' può aiutare a recuperare spazio."),
            ))

    worst_waste = max(wastes)
    if 0.35 < worst_waste < 1.0 and smallest >= 0.999:
        findings.append(Finding(
            Severity.NOTE,
            f"Circa il {worst_waste * 100:.0f}% di ciascuna cella risulta vuoto",
            "Le proporzioni delle pagine differiscono dalla griglia del foglio, creando spaziature laterali.",
        ))
    return findings
