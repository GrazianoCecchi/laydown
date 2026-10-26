"""
PDF Inspection Engine — extract document metadata and page box geometry.

No Qt imports: this module is pure PyMuPDF and data structures, keeping it
testable headless.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import fitz

from laydown.engine.utils import pt_to_mm


def format_file_size(size_bytes: int) -> str:
    """Format a byte count into a human-readable string (B, KB, MB, GB)."""
    if size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def clean_pdf_date(raw_date: str) -> str:
    """
    Format a raw PDF date string like "D:20261012143000+02'00'" into a readable date.
    Returns the original string if not conforming to PDF date format.
    """
    if not raw_date or not isinstance(raw_date, str):
        return ""
    clean = raw_date.strip()
    if clean.startswith("D:"):
        clean = clean[2:]
    if len(clean) >= 8 and clean[:8].isdigit():
        year = clean[0:4]
        month = clean[4:6]
        day = clean[6:8]
        if len(clean) >= 14 and clean[8:14].isdigit():
            hour = clean[8:10]
            minute = clean[10:12]
            sec = clean[12:14]
            return f"{year}-{month}-{day} {hour}:{minute}:{sec}"
        return f"{year}-{month}-{day}"
    return raw_date


@dataclass(frozen=True)
class BoxInfo:
    """
    Geometry and status of a single PDF page boundary box.

    rect: the raw, unrotated PDF coordinate space rectangle.
    visual_rect: the rectangle mapped to visual space (accounting for page.rotation).
    """
    name: str
    rect: Tuple[float, float, float, float]
    visual_rect: Tuple[float, float, float, float]
    width_pt: float
    height_pt: float
    width_mm: float
    height_mm: float
    is_explicit: bool

    @property
    def dimensions_mm_str(self) -> str:
        return f"{self.width_mm:.2f} × {self.height_mm:.2f} mm"

    @property
    def dimensions_pt_str(self) -> str:
        return f"{self.width_pt:.1f} × {self.height_pt:.1f} pt"


@dataclass
class PageInspection:
    """Inspection details for a single PDF page."""
    page_index: int
    page_number: int
    rotation: int
    width_pt: float
    height_pt: float
    width_mm: float
    height_mm: float
    mediabox: BoxInfo
    cropbox: BoxInfo
    bleedbox: BoxInfo
    trimbox: BoxInfo
    artbox: BoxInfo
    boxes: Dict[str, BoxInfo] = field(default_factory=dict)


@dataclass
class DocumentInspection:
    """Comprehensive inspection information for an entire PDF document."""
    file_path: str
    file_name: str
    file_size_bytes: int
    file_size_formatted: str
    pdf_version: str
    page_count: int
    title: str
    author: str
    subject: str
    keywords: str
    creator: str
    producer: str
    creation_date: str
    mod_date: str
    is_encrypted: bool
    trapped: str
    pages: List[PageInspection] = field(default_factory=list)


def _is_box_explicit(doc: fitz.Document, page: fitz.Page, box_name: str) -> bool:
    """Check whether *box_name* is explicitly defined in the page's PDF dictionary."""
    try:
        key_type, val = doc.xref_get_key(page.xref, box_name)
        return key_type != "null" and bool(val)
    except Exception:
        return False


def _aligned_box(page: fitz.Page, name: str) -> fitz.Rect:
    if name == "MediaBox":
        mb = page.mediabox
        mb_raw = page._other_box("MediaBox") if hasattr(page, "_other_box") else None
        if mb_raw is not None:
            return fitz.Rect(mb_raw[0], mb.y1 - mb_raw[3], mb_raw[2], mb.y1 - mb_raw[1])
        return fitz.Rect(mb)
    return fitz.Rect(getattr(page, name.lower()))


def _box_as_rendered(page: fitz.Page, box: fitz.Rect) -> fitz.Rect:
    """
    Convert a raw unrotated box into page.rect coordinate space (the rendered space).
    """
    m = page.rotation_matrix
    crop = page.cropbox * m
    dx = page.rect.x0 - crop.x0
    dy = page.rect.y0 - crop.y0
    r = box * m
    return fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)


def _make_box_info(doc: fitz.Document, page: fitz.Page, name: str) -> BoxInfo:
    is_explicit = _is_box_explicit(doc, page, name)
    raw_rect = _aligned_box(page, name)
    v_rect = _box_as_rendered(page, raw_rect)

    w_pt = abs(v_rect.width)
    h_pt = abs(v_rect.height)
    w_mm = pt_to_mm(w_pt)
    h_mm = pt_to_mm(h_pt)

    return BoxInfo(
        name=name,
        rect=(round(raw_rect.x0, 2), round(raw_rect.y0, 2), round(raw_rect.x1, 2), round(raw_rect.y1, 2)),
        visual_rect=(round(v_rect.x0, 2), round(v_rect.y0, 2), round(v_rect.x1, 2), round(v_rect.y1, 2)),
        width_pt=round(w_pt, 2),
        height_pt=round(h_pt, 2),
        width_mm=round(w_mm, 2),
        height_mm=round(h_mm, 2),
        is_explicit=is_explicit,
    )


def inspect_page(doc: fitz.Document, page_index: int) -> PageInspection:
    """Extract page dimensions and all 5 boundary boxes for a given page."""
    if page_index < 0 or page_index >= len(doc):
        raise IndexError(f"Page index {page_index} out of range (0..{len(doc) - 1})")

    page = doc[page_index]
    rot = page.rotation

    mediabox = _make_box_info(doc, page, "MediaBox")
    cropbox = _make_box_info(doc, page, "CropBox")
    bleedbox = _make_box_info(doc, page, "BleedBox")
    trimbox = _make_box_info(doc, page, "TrimBox")
    artbox = _make_box_info(doc, page, "ArtBox")

    boxes = {
        "MediaBox": mediabox,
        "CropBox": cropbox,
        "BleedBox": bleedbox,
        "TrimBox": trimbox,
        "ArtBox": artbox,
    }

    w_pt = page.rect.width
    h_pt = page.rect.height

    return PageInspection(
        page_index=page_index,
        page_number=page_index + 1,
        rotation=rot,
        width_pt=round(w_pt, 2),
        height_pt=round(h_pt, 2),
        width_mm=round(pt_to_mm(w_pt), 2),
        height_mm=round(pt_to_mm(h_pt), 2),
        mediabox=mediabox,
        cropbox=cropbox,
        bleedbox=bleedbox,
        trimbox=trimbox,
        artbox=artbox,
        boxes=boxes,
    )


def inspect_document(doc: fitz.Document, file_path: str = "") -> DocumentInspection:
    """Inspect full document metadata, general info, and page boxes."""
    path = file_path or doc.name or ""
    size_bytes = 0
    if path and os.path.isfile(path):
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = 0

    meta = doc.metadata or {}
    pdf_version = meta.get("format") or "PDF"

    pages = [inspect_page(doc, i) for i in range(len(doc))]

    return DocumentInspection(
        file_path=path,
        file_name=os.path.basename(path) if path else "Senza titolo",
        file_size_bytes=size_bytes,
        file_size_formatted=format_file_size(size_bytes),
        pdf_version=pdf_version,
        page_count=len(doc),
        title=meta.get("title", ""),
        author=meta.get("author", ""),
        subject=meta.get("subject", ""),
        keywords=meta.get("keywords", ""),
        creator=meta.get("creator", ""),
        producer=meta.get("producer", ""),
        creation_date=clean_pdf_date(meta.get("creationDate", "")),
        mod_date=clean_pdf_date(meta.get("modDate", "")),
        is_encrypted=doc.is_encrypted,
        trapped=meta.get("trapped", ""),
        pages=pages,
    )
