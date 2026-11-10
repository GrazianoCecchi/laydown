"""
PDF Composer and Merger engine — pure Python / PyMuPDF (fitz), zero Qt dependencies.

Enables assembling pages from multiple source PDFs into a single target PDF
using a concise, expressive sequence expression, such as:
    "A1-10, B2, A11-12, C1-4"
or:
    "A, B" (all pages of A followed by all pages of B)
    "A1-5, blank, B2-4, A6-" (with blank filler pages and open-ended ranges)
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Dict, List, Optional, Tuple, Union

import fitz

from laydown.engine.utils import pt_to_mm


@dataclass(frozen=True)
class SourceDocInfo:
    """Metadata describing a source PDF document loaded into the composer."""
    id: str  # 'A', 'B', 'C', etc.
    path: str
    page_count: int
    first_page_width_mm: float
    first_page_height_mm: float
    title: str = ""

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def dimensions_str(self) -> str:
        return f"{self.first_page_width_mm:.1f} × {self.first_page_height_mm:.1f} mm"


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


def inspect_source_pdf(path: str, doc_id: str = "A") -> SourceDocInfo:
    """
    Open and inspect a source PDF file, returning its metadata without keeping it locked.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File PDF non trovato: {path}")

    doc = fitz.open(path)
    try:
        count = len(doc)
        if count == 0:
            raise ValueError(f"Il file PDF '{os.path.basename(path)}' non contiene pagine.")

        page0 = doc[0]
        rect = page0.rect
        width_mm = pt_to_mm(rect.width)
        height_mm = pt_to_mm(rect.height)
        meta = doc.metadata or {}
        title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]

        return SourceDocInfo(
            id=doc_id.upper(),
            path=os.path.abspath(path),
            page_count=count,
            first_page_width_mm=width_mm,
            first_page_height_mm=height_mm,
            title=title,
        )
    finally:
        doc.close()


def parse_page_sequence(
    expression: str,
    doc_page_counts: Dict[str, int],
) -> ParseResult:
    """
    Parse a page sequence expression against a mapping of source IDs to page counts.

    Supported syntax examples:
      - "A1-10, B2, A11-12, C1-4"
      - "A, B" (all pages of A, then all pages of B)
      - "A5-" (from page 5 to end of document A)
      - "A-5" (from page 1 to 5 of document A)
      - "A10-1" (pages 10 down to 1 in reverse order)
      - "blank" or "_" (a blank page)
      - Separators: commas, semicolons, or whitespace
    """
    expr = expression.strip()
    if not expr:
        return ParseResult(
            is_valid=True,
            page_specs=[],
            error_message=None,
            summary_by_source={},
        )

    # Normalize doc_page_counts keys to uppercase
    counts = {k.upper(): v for k, v in doc_page_counts.items()}

    # Split tokens by comma or semicolon; whitespace inside a token can be trimmed,
    # but tokens themselves can also be space-separated if no commas are used.
    # We replace semicolons with commas, then split by commas or spaces.
    raw_tokens = re.split(r"[,;\s]+", expr)
    tokens = [t.strip() for t in raw_tokens if t.strip()]

    specs: List[PageSpec] = []
    summary: Dict[str, int] = {k: 0 for k in counts}
    summary["BLANK"] = 0

    for token in tokens:
        # Check for blank page token
        if token.lower() in ("blank", "_", "[blank]", "vuota", "bianca"):
            specs.append(PageSpec(source_id=None, source_page_index=-1, is_blank=True))
            summary["BLANK"] += 1
            continue

        # Pattern: [DocID][PageRange]
        # e.g., A, A1, A:1, A1-10, A10-1, A5-, A-5, A-
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

        # Case 1: Just the document ID (e.g. "A" or "A-") -> all pages from 1 to max_pages
        if not range_part or range_part == "-":
            for p in range(max_pages):
                specs.append(PageSpec(source_id=doc_id, source_page_index=p))
            summary[doc_id] += max_pages
            continue

        # Case 2: Open range from start (e.g. "-5")
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

        # Case 3: Open range to end (e.g. "5-")
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

        # Case 4: Closed range (e.g. "1-10" or "10-1")
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

        # Case 5: Single page (e.g. "2")
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

    # Clean up empty summary entries
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
) -> fitz.Document:
    """
    Assemble a target PDF from source documents according to page_specs.

    *sources* is a mapping of ID (e.g. 'A', 'B') to file path or opened fitz.Document.
    If file paths are passed, they are opened and safely closed upon completion.
    The caller is responsible for closing the returned fitz.Document.

    Preserves page geometry, TrimBox, BleedBox, CropBox, vector content and annotations.
    """
    if not page_specs:
        raise ValueError("Nessuna pagina specificata per la composizione del PDF.")

    opened_internally: List[fitz.Document] = []
    doc_map: Dict[str, fitz.Document] = {}

    try:
        # Resolve sources to fitz.Document instances
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

        # Track default page dimensions for blank pages
        fallback_width_pt = 595.28   # A4 default width
        fallback_height_pt = 841.89  # A4 default height

        # If we have at least one source document with pages, use its first page dimensions as fallback
        for src_doc in doc_map.values():
            if len(src_doc) > 0:
                fallback_width_pt = src_doc[0].rect.width
                fallback_height_pt = src_doc[0].rect.height
                break

        last_width = fallback_width_pt
        last_height = fallback_height_pt

        for spec in page_specs:
            if spec.is_blank:
                w = spec.custom_width_pt or last_width
                h = spec.custom_height_pt or last_height
                out_doc.new_page(width=w, height=h)
                last_width, last_height = w, h
            else:
                if not spec.source_id or spec.source_id not in doc_map:
                    raise KeyError(f"Documento sorgente '{spec.source_id}' non trovato.")
                src_doc = doc_map[spec.source_id]
                idx = spec.source_page_index
                if idx < 0 or idx >= len(src_doc):
                    raise IndexError(
                        f"Indice di pagina {idx} non valido per il documento '{spec.source_id}' "
                        f"(ha {len(src_doc)} pagine)."
                    )

                # insert_pdf correctly preserves orientation, content, fonts, and page boxes
                out_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)
                # Update last seen dimensions
                last_rect = src_doc[idx].rect
                last_width, last_height = last_rect.width, last_rect.height

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
