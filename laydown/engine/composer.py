"""
PDF Composer and Merger engine — pure Python / PyMuPDF (fitz), zero Qt dependencies.

Enables assembling pages from multiple source PDFs into a single target PDF
using a concise, expressive sequence expression, such as:
    "A1-10, B2, A11-12, C1-4"
or:
    "A, B" (all pages of A followed by all pages of B)
    "A1-5, blank, B2-4, A6-" (with blank filler pages and open-ended ranges)

Includes geometry normalization for:
  - Clean View mode (crops pages to finished TrimBox, hiding bleeds & trim marks)
  - Prepress Print mode (ensures consistent TrimBox & BleedBox with standard bleed margins)
  - Mixed format handling (preserve original sizes or scale to common target format)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
from typing import Dict, List, Optional, Tuple, Union

import fitz

from laydown.engine.data_model import SHEET_PRESETS_MM
from laydown.engine.utils import mm_to_pt, pt_to_mm


class ComposerIntent(str, Enum):
    """Output intent determining how page boxes and bleeds are treated."""
    PRESERVE = "preserve"  # Keep source pages exactly as they are
    VIEW = "view"          # Clean screen view: crop to finished TrimBox, hide bleed/marks
    PRINT = "print"        # Prepress print: ensure uniform TrimBox and standard BleedBox


@dataclass(frozen=True)
class CompositionGeometryConfig:
    """Configuration for page geometry processing during PDF composition."""
    intent: ComposerIntent = ComposerIntent.PRESERVE
    bleed_mm: float = 3.0  # Bleed margin in mm (used in PRINT mode)
    scale_to_common_format: bool = False
    common_format_name: Optional[str] = None  # Key in SHEET_PRESETS_MM, e.g. "A4", "A3"


@dataclass(frozen=True)
class SourceDocInfo:
    """Metadata describing a source PDF document loaded into the composer."""
    id: str  # 'A', 'B', 'C', etc.
    path: str
    page_count: int
    media_width_mm: float
    media_height_mm: float
    trim_width_mm: float
    trim_height_mm: float
    has_bleed: bool
    bleed_margin_mm: float
    format_name: str
    title: str = ""

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def dimensions_str(self) -> str:
        dim = f"{self.media_width_mm:.1f} × {self.media_height_mm:.1f} mm"
        if self.has_bleed and self.trim_width_mm > 0 and self.trim_height_mm > 0:
            trim_dim = f"{self.trim_width_mm:.1f} × {self.trim_height_mm:.1f} mm"
            return f"{self.format_name} ({trim_dim} netto, +{self.bleed_margin_mm:.1f}mm al vivo)"
        return f"{self.format_name} ({dim})"

    # Backward compatibility properties
    @property
    def first_page_width_mm(self) -> float:
        return self.media_width_mm

    @property
    def first_page_height_mm(self) -> float:
        return self.media_height_mm


@dataclass(frozen=True)
class GeometryDiagnosis:
    """Diagnosis of geometry discrepancies across loaded documents."""
    has_mixed_formats: bool
    has_mixed_bleed: bool
    distinct_formats: List[str]
    distinct_dimensions: List[str]
    warning_message: Optional[str] = None


@dataclass(frozen=True)
class PageSpec:
    """Specification of a single page in the target composite PDF."""
    source_id: Optional[str]  # e.g. 'A', 'B' or None for blank pages
    source_page_index: int     # 0-based index in the source PDF (-1 for blank)
    is_blank: bool = False
    custom_width_pt: Optional[float] = None
    custom_height_pt: Optional[float] = None

    @property
    def display_source_page(self) -> int:
        """1-based page number for display."""
        return self.source_page_index + 1 if not self.is_blank else 0


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing and validating a page sequence string."""
    is_valid: bool
    page_specs: List[PageSpec]
    error_message: Optional[str] = None
    summary_by_source: Dict[str, int] = None

    @property
    def total_pages(self) -> int:
        return len(self.page_specs)


def detect_format_name(width_mm: float, height_mm: float, tolerance_mm: float = 2.5) -> str:
    """Identify standard ISO or US paper format name from dimensions with tolerance."""
    w, h = min(width_mm, height_mm), max(width_mm, height_mm)
    for name, (pw, ph) in SHEET_PRESETS_MM.items():
        if abs(w - pw) <= tolerance_mm and abs(h - ph) <= tolerance_mm:
            return name
    return "Personalizzato"


def inspect_source_pdf(path: str, doc_id: str = "A") -> SourceDocInfo:
    """
    Open and inspect a source PDF file, detecting trim box, bleed and format name.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File PDF non trovato: {path}")

    doc = fitz.open(path)
    try:
        count = len(doc)
        if count == 0:
            raise ValueError(f"Il file PDF '{os.path.basename(path)}' non contiene pagine.")

        page0 = doc[0]
        media_rect = page0.rect
        m_w_mm = pt_to_mm(media_rect.width)
        m_h_mm = pt_to_mm(media_rect.height)

        # Inspect TrimBox
        trim_box = page0.trimbox
        has_trim = bool(trim_box and not trim_box.is_empty and trim_box != media_rect)

        if has_trim:
            t_w_mm = pt_to_mm(trim_box.width)
            t_h_mm = pt_to_mm(trim_box.height)
            margin_pt = max(0.0, (media_rect.width - trim_box.width) / 2.0)
            margin_mm = pt_to_mm(margin_pt)
            has_bleed = margin_mm >= 1.0
            # Identify format based on finished TrimBox
            fmt_name = detect_format_name(t_w_mm, t_h_mm)
        else:
            t_w_mm = m_w_mm
            t_h_mm = m_h_mm
            margin_mm = 0.0
            has_bleed = False
            # Check if MediaBox matches a common trim format plus standard 3-5mm bleed
            # (e.g. 216x303 mm = A4 + 3mm bleed)
            fmt_name = detect_format_name(m_w_mm, m_h_mm)
            if fmt_name == "Personalizzato":
                for cand_name in ("A5", "A4", "A3", "SRA3", "B4", "B3"):
                    pw, ph = SHEET_PRESETS_MM[cand_name]
                    w_cand, h_cand = min(m_w_mm, m_h_mm), max(m_w_mm, m_h_mm)
                    diff_w = (w_cand - pw) / 2.0
                    diff_h = (h_cand - ph) / 2.0
                    if 2.0 <= diff_w <= 7.0 and abs(diff_w - diff_h) <= 1.5:
                        fmt_name = cand_name
                        t_w_mm, t_h_mm = (pw, ph) if m_w_mm <= m_h_mm else (ph, pw)
                        has_bleed = True
                        margin_mm = diff_w
                        break

        meta = doc.metadata or {}
        title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]

        return SourceDocInfo(
            id=doc_id.upper(),
            path=os.path.abspath(path),
            page_count=count,
            media_width_mm=m_w_mm,
            media_height_mm=m_h_mm,
            trim_width_mm=t_w_mm,
            trim_height_mm=t_h_mm,
            has_bleed=has_bleed,
            bleed_margin_mm=margin_mm,
            format_name=fmt_name,
            title=title,
        )
    finally:
        doc.close()


def diagnose_sources_geometry(sources: List[SourceDocInfo]) -> GeometryDiagnosis:
    """Analyze loaded source documents to detect mixed formats or bleed inconsistencies."""
    if not sources:
        return GeometryDiagnosis(
            has_mixed_formats=False,
            has_mixed_bleed=False,
            distinct_formats=[],
            distinct_dimensions=[],
            warning_message=None,
        )

    formats = list(dict.fromkeys(s.format_name for s in sources))
    dims = list(dict.fromkeys(f"{s.media_width_mm:.0f}×{s.media_height_mm:.0f} mm" for s in sources))
    bleeds = [s.has_bleed for s in sources]

    has_mixed_formats = len(formats) > 1 or len(dims) > 1
    has_mixed_bleed = any(bleeds) and not all(bleeds)

    warnings = []
    if has_mixed_formats:
        fmt_str = ", ".join(formats)
        warnings.append(f"Rilevati formati eterogenei tra i file ({fmt_str}).")

    if has_mixed_bleed:
        warnings.append("Alcuni file contengono abbondanza per il taglio (al vivo), altri hanno formato netto.")

    msg = " ".join(warnings) if warnings else None

    return GeometryDiagnosis(
        has_mixed_formats=has_mixed_formats,
        has_mixed_bleed=has_mixed_bleed,
        distinct_formats=formats,
        distinct_dimensions=dims,
        warning_message=msg,
    )


def parse_page_sequence(
    expression: str,
    doc_page_counts: Dict[str, int],
) -> ParseResult:
    """
    Parse a page sequence expression against a mapping of source IDs to page counts.
    """
    expr = expression.strip()
    if not expr:
        return ParseResult(
            is_valid=True,
            page_specs=[],
            error_message=None,
            summary_by_source={},
        )

    counts = {k.upper(): v for k, v in doc_page_counts.items()}
    raw_tokens = re.split(r"[,;\s]+", expr)
    tokens = [t.strip() for t in raw_tokens if t.strip()]

    specs: List[PageSpec] = []
    summary: Dict[str, int] = {k: 0 for k in counts}
    summary["BLANK"] = 0

    for token in tokens:
        if token.lower() in ("blank", "_", "[blank]", "vuota", "bianca"):
            specs.append(PageSpec(source_id=None, source_page_index=-1, is_blank=True))
            summary["BLANK"] += 1
            continue

        m = re.match(r"^([a-zA-Z]+)[:\s]*(.*)$", token)
        if not m:
            return ParseResult(
                is_valid=False,
                page_specs=[],
                error_message=f"Elemento non riconosciuto: '{token}'",
            )

        doc_id = m.group(1).upper()
        range_part = m.group(2).strip()

        if doc_id not in counts:
            avail = ", ".join(sorted(counts.keys())) or "nessuno"
            return ParseResult(
                is_valid=False,
                page_specs=[],
                error_message=f"Documento '{doc_id}' non presente tra i file caricati (disponibili: {avail}).",
            )

        max_pages = counts[doc_id]

        if not range_part or range_part == "-":
            for p in range(max_pages):
                specs.append(PageSpec(source_id=doc_id, source_page_index=p))
            summary[doc_id] += max_pages
            continue

        if range_part.startswith("-"):
            sub = range_part[1:].strip()
            if not sub.isdigit():
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Intervallo non valido in '{token}': '{range_part}'",
                )
            end = int(sub)
            if end < 1:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il numero di pagina deve essere >= 1 in '{token}'.",
                )
            if end > max_pages:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il documento '{doc_id}' ha {max_pages} pagine (richiesta pagina {end}).",
                )
            for p in range(end):
                specs.append(PageSpec(source_id=doc_id, source_page_index=p))
            summary[doc_id] += end
            continue

        if range_part.endswith("-"):
            sub = range_part[:-1].strip()
            if not sub.isdigit():
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Intervallo non valido in '{token}': '{range_part}'",
                )
            start = int(sub)
            if start < 1:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il numero di pagina deve essere >= 1 in '{token}'.",
                )
            if start > max_pages:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il documento '{doc_id}' ha {max_pages} pagine (richiesta pagina {start}).",
                )
            for p in range(start - 1, max_pages):
                specs.append(PageSpec(source_id=doc_id, source_page_index=p))
            count_added = max_pages - (start - 1)
            summary[doc_id] += count_added
            continue

        if "-" in range_part:
            parts = range_part.split("-", 1)
            p1_str, p2_str = parts[0].strip(), parts[1].strip()
            if not (p1_str.isdigit() and p2_str.isdigit()):
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Intervallo non valido in '{token}': '{range_part}'",
                )
            start, end = int(p1_str), int(p2_str)
            if start < 1 or end < 1:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"I numeri di pagina devono essere >= 1 in '{token}'.",
                )
            if start > max_pages:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il documento '{doc_id}' ha {max_pages} pagine (richiesta pagina {start}).",
                )
            if end > max_pages:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il documento '{doc_id}' ha {max_pages} pagine (richiesta pagina {end}).",
                )

            step = 1 if end >= start else -1
            num_range = range(start, end + step, step)
            for page_num in num_range:
                specs.append(PageSpec(source_id=doc_id, source_page_index=page_num - 1))
            summary[doc_id] += len(num_range)
            continue

        if range_part.isdigit():
            page_num = int(range_part)
            if page_num < 1:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il numero di pagina deve essere >= 1 in '{token}'.",
                )
            if page_num > max_pages:
                return ParseResult(
                    is_valid=False,
                    page_specs=[],
                    error_message=f"Il documento '{doc_id}' ha {max_pages} pagine (richiesta pagina {page_num}).",
                )
            specs.append(PageSpec(source_id=doc_id, source_page_index=page_num - 1))
            summary[doc_id] += 1
            continue

        return ParseResult(
            is_valid=False,
            page_specs=[],
            error_message=f"Sintassi pagina non valida in '{token}': '{range_part}'",
        )

    active_summary = {k: v for k, v in summary.items() if v > 0}

    return ParseResult(
        is_valid=True,
        page_specs=specs,
        error_message=None,
        summary_by_source=active_summary,
    )


def compose_pdf(
    sources: Dict[str, Union[str, fitz.Document]],
    page_specs: List[PageSpec],
    output_path: Optional[str] = None,
    geometry_config: Optional[CompositionGeometryConfig] = None,
) -> fitz.Document:
    """
    Assemble a target PDF from source documents according to page_specs and geometry_config.

    When geometry_config.intent is:
      - PRESERVE: exact page duplication preserving all boxes and artwork.
      - VIEW: crops pages to finished TrimBox, hiding extra bleed margins or crop marks.
      - PRINT: ensures uniform TrimBox and standard BleedBox margins.

    If scale_to_common_format is enabled in geometry_config, pages are proportionally
    scaled and centered onto sheets of common_format_name (e.g. 'A4').
    """
    if not page_specs:
        raise ValueError("Nessuna pagina specificata per la composizione del PDF.")

    geo = geometry_config or CompositionGeometryConfig()
    opened_internally: List[fitz.Document] = []
    doc_map: Dict[str, fitz.Document] = {}

    try:
        for doc_id, src in sources.items():
            key = doc_id.upper()
            if isinstance(src, str):
                d = fitz.open(src)
                opened_internally.append(d)
                doc_map[key] = d
            elif isinstance(src, fitz.Document):
                doc_map[key] = src
            else:
                raise TypeError(f"Sorgente per '{doc_id}' non valida: {type(src)}")

        out_doc = fitz.open()

        fallback_width_pt = 595.28   # A4 default width
        fallback_height_pt = 841.89  # A4 default height

        for src_doc in doc_map.values():
            if len(src_doc) > 0:
                fallback_width_pt = src_doc[0].rect.width
                fallback_height_pt = src_doc[0].rect.height
                break

        last_width = fallback_width_pt
        last_height = fallback_height_pt

        # Resolve target common dimensions if scaling requested
        target_common_w_pt: Optional[float] = None
        target_common_h_pt: Optional[float] = None
        if geo.scale_to_common_format and geo.common_format_name:
            if geo.common_format_name in SHEET_PRESETS_MM:
                cw_mm, ch_mm = SHEET_PRESETS_MM[geo.common_format_name]
                target_common_w_pt = mm_to_pt(cw_mm)
                target_common_h_pt = mm_to_pt(ch_mm)

        for spec in page_specs:
            if spec.is_blank:
                w = spec.custom_width_pt or (target_common_w_pt or last_width)
                h = spec.custom_height_pt or (target_common_h_pt or last_height)
                out_doc.new_page(width=w, height=h)
                last_width, last_height = w, h
                continue

            if not spec.source_id or spec.source_id not in doc_map:
                raise KeyError(f"Documento sorgente '{spec.source_id}' non trovato.")

            src_doc = doc_map[spec.source_id]
            idx = spec.source_page_index
            if idx < 0 or idx >= len(src_doc):
                raise IndexError(
                    f"Indice di pagina {idx} non valido per il documento '{spec.source_id}' "
                    f"(ha {len(src_doc)} pagine)."
                )

            src_page = src_doc[idx]
            src_rect = src_page.rect

            # ── Case A: Scale to common target format ──
            if geo.scale_to_common_format and target_common_w_pt and target_common_h_pt:
                # Respect page orientation
                is_landscape = src_rect.width > src_rect.height
                sheet_w = max(target_common_w_pt, target_common_h_pt) if is_landscape else min(target_common_w_pt, target_common_h_pt)
                sheet_h = min(target_common_w_pt, target_common_h_pt) if is_landscape else max(target_common_w_pt, target_common_h_pt)

                # What source area to place? Use TrimBox in VIEW mode, rect otherwise
                clip_box = src_rect
                if geo.intent == ComposerIntent.VIEW and src_page.trimbox and not src_page.trimbox.is_empty:
                    clip_box = src_page.trimbox & src_rect

                scale = min(sheet_w / clip_box.width, sheet_h / clip_box.height)
                placed_w = clip_box.width * scale
                placed_h = clip_box.height * scale
                placed_x = (sheet_w - placed_w) / 2.0
                placed_y = (sheet_h - placed_h) / 2.0
                dest_rect = fitz.Rect(placed_x, placed_y, placed_x + placed_w, placed_y + placed_h)

                new_page = out_doc.new_page(width=sheet_w, height=sheet_h)
                new_page.show_pdf_page(dest_rect, src_doc, idx, clip=clip_box)
                new_page.set_cropbox(new_page.rect)
                new_page.set_trimbox(dest_rect if geo.intent == ComposerIntent.PRINT else new_page.rect)
                last_width, last_height = sheet_w, sheet_h
                continue

            # ── Case B: Clean VIEW mode (crop to TrimBox) ──
            if geo.intent == ComposerIntent.VIEW:
                trim = src_page.trimbox
                has_valid_trim = bool(
                    trim and not trim.is_empty
                    and (src_rect.width - trim.width > 2.0 or src_rect.height - trim.height > 2.0)
                )

                if has_valid_trim:
                    clean_trim = trim & src_rect
                    new_page = out_doc.new_page(width=clean_trim.width, height=clean_trim.height)
                    new_page.show_pdf_page(new_page.rect, src_doc, idx, clip=clean_trim)
                    new_page.set_cropbox(new_page.rect)
                    new_page.set_trimbox(new_page.rect)
                    last_width, last_height = clean_trim.width, clean_trim.height
                    continue
                else:
                    out_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)
                    last_width, last_height = src_rect.width, src_rect.height
                    continue

            # ── Case C: Prepress PRINT mode (ensure standard TrimBox & BleedBox) ──
            if geo.intent == ComposerIntent.PRINT:
                bleed_pt = mm_to_pt(geo.bleed_mm)
                trim = src_page.trimbox
                has_distinct_trim = bool(
                    trim and not trim.is_empty
                    and (src_rect.width - trim.width > 2.0 or src_rect.height - trim.height > 2.0)
                )

                if has_distinct_trim:
                    # Page already has an inner TrimBox and bleed margins
                    out_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)
                    dest_page = out_doc[-1]
                    # Ensure BleedBox is defined around TrimBox
                    tb = dest_page.trimbox
                    bb = fitz.Rect(tb.x0 - bleed_pt, tb.y0 - bleed_pt, tb.x1 + bleed_pt, tb.y1 + bleed_pt) & dest_page.rect
                    dest_page.set_bleedbox(bb)
                    last_width, last_height = dest_page.rect.width, dest_page.rect.height
                    continue
                else:
                    # Page was net-format without bleed margins: expand sheet and center TrimBox
                    new_w = src_rect.width + 2 * bleed_pt
                    new_h = src_rect.height + 2 * bleed_pt
                    new_page = out_doc.new_page(width=new_w, height=new_h)
                    trim_rect = fitz.Rect(bleed_pt, bleed_pt, bleed_pt + src_rect.width, bleed_pt + src_rect.height)
                    new_page.show_pdf_page(trim_rect, src_doc, idx)
                    new_page.set_trimbox(trim_rect)
                    new_page.set_bleedbox(new_page.rect)
                    last_width, last_height = new_w, new_h
                    continue

            # ── Case D: PRESERVE mode (default) ──
            out_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)
            last_width, last_height = src_rect.width, src_rect.height

        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            out_doc.save(output_path, deflate=True)

        return out_doc

    finally:
        for doc in opened_internally:
            try:
                doc.close()
            except Exception:
                pass
