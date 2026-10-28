"""
PDF Inspection Engine — extract document metadata and page box geometry.

No Qt imports: this module is pure PyMuPDF and data structures, keeping it
testable headless.
"""

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

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


@dataclass(frozen=True)
class ICCProfileInfo:
    """Information extracted from an embedded or referenced ICC profile."""
    name: str
    color_space: str
    profile_class: str
    version: str
    rendering_intent: str
    size_bytes: int = 0
    is_embedded: bool = True

    @property
    def size_formatted(self) -> str:
        return format_file_size(self.size_bytes)


@dataclass(frozen=True)
class OutputIntentInfo:
    """OutputIntent dictionary from PDF catalog (e.g. PDF/X, PDF/A)."""
    subtype: str
    output_condition_identifier: str
    output_condition: str = ""
    registry_name: str = ""
    info: str = ""
    dest_profile_name: str = ""
    profile: Optional[ICCProfileInfo] = None


@dataclass(frozen=True)
class SpotColorInfo:
    """Spot color / separation found in the PDF (e.g. PANTONE, CutContour)."""
    name: str
    alternate_space: str = "DeviceCMYK"
    pages: Tuple[int, ...] = ()


@dataclass
class PageColorInspection:
    """Color characteristics of a single PDF page."""
    page_index: int
    page_number: int
    has_cmyk: bool = False
    has_rgb: bool = False
    has_gray: bool = False
    has_spot: bool = False
    has_transparency: bool = False
    has_overprint: bool = False
    color_spaces: List[str] = field(default_factory=list)
    spot_colors: List[str] = field(default_factory=list)
    rendering_intents: List[str] = field(default_factory=list)
    images_count: int = 0
    images_color_spaces: Dict[str, int] = field(default_factory=dict)
    images_profiles: List[str] = field(default_factory=list)

    @property
    def summary_str(self) -> str:
        parts = []
        if self.has_cmyk:
            parts.append("CMYK")
        if self.has_spot:
            n_spots = len(self.spot_colors)
            parts.append(f"{n_spots} Tinta/e piatta/e" if n_spots > 0 else "Spot")
        if self.has_rgb:
            parts.append("RGB (Attenzione)")
        if self.has_gray and not parts:
            parts.append("Scala di grigi")
        return " + ".join(parts) if parts else "Non specificato"


@dataclass
class DocumentColorInspection:
    """Comprehensive document-level color and preflight color characteristics."""
    pdfx_conformance: str = ""
    pdfa_conformance: str = ""
    primary_color_space: str = "Non determinato"
    has_cmyk: bool = False
    has_rgb: bool = False
    has_gray: bool = False
    has_spot: bool = False
    has_rgb_warning: bool = False
    has_transparency: bool = False
    has_overprint: bool = False
    color_spaces_detected: List[str] = field(default_factory=list)
    output_intents: List[OutputIntentInfo] = field(default_factory=list)
    spot_colors: List[SpotColorInfo] = field(default_factory=list)
    embedded_profiles: List[ICCProfileInfo] = field(default_factory=list)
    rendering_intents: List[str] = field(default_factory=list)


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
    color: Optional[PageColorInspection] = None


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
    color: Optional[DocumentColorInspection] = None


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


def _clean_pdf_string(raw: str) -> str:
    """Clean and unescape a raw PDF string or name."""
    if not raw or raw == "null":
        return ""
    val = raw.strip()
    if val.startswith("(") and val.endswith(")"):
        val = val[1:-1]
    val = val.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    val = re.sub(r"#([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), val)
    return val.strip()


def parse_icc_profile_bytes(data: bytes) -> Optional[ICCProfileInfo]:
    """Extract standard header fields and description from raw ICC profile bytes."""
    if not data or len(data) < 128:
        return None
    try:
        p_class_raw = data[12:16].decode("latin1", "ignore").strip()
        color_space = data[16:20].decode("latin1", "ignore").strip()
        major = data[8]
        minor = (data[9] >> 4) & 0x0F
        bugfix = data[9] & 0x0F
        ver_str = f"{major}.{minor}" + (f".{bugfix}" if bugfix else "")

        intent_code = struct.unpack(">I", data[64:68])[0] if len(data) >= 68 else 0
        intents = {
            0: "Percettivo (Perceptual)",
            1: "Colorimetrico Relativo (Relative Colorimetric)",
            2: "Saturazione (Saturation)",
            3: "Colorimetrico Assoluto (Absolute Colorimetric)",
        }
        rendering_intent = intents.get(intent_code, f"Codice {intent_code}")

        classes = {
            "scnr": "Scanner / Acquisizione (scnr)",
            "mntr": "Monitor / Display (mntr)",
            "prtr": "Stampante / Uscita (prtr)",
            "link": "DeviceLink (link)",
            "spac": "Spazio Colore (spac)",
            "abst": "Astratto (abst)",
            "nmcl": "Tinte Piatte / Colori Nominati (nmcl)",
        }
        profile_class = classes.get(p_class_raw, p_class_raw or "Generico")

        name = ""
        if len(data) >= 132:
            tag_count = struct.unpack(">I", data[128:132])[0]
            pos = 132
            for _ in range(tag_count):
                if pos + 12 > len(data):
                    break
                sig, offset, length = struct.unpack(">4sII", data[pos:pos + 12])
                pos += 12
                if sig == b"desc" and offset + length <= len(data):
                    tag_data = data[offset:offset + length]
                    if len(tag_data) >= 12 and tag_data[:4] == b"desc":
                        ascii_len = struct.unpack(">I", tag_data[8:12])[0]
                        if 12 + ascii_len <= len(tag_data):
                            name = tag_data[12:12 + ascii_len].decode("latin1", "ignore").rstrip("\x00").strip()
                    elif len(tag_data) >= 16 and tag_data[:4] == b"mluc":
                        rec_count = struct.unpack(">I", tag_data[8:12])[0]
                        if rec_count > 0 and len(tag_data) >= 28:
                            s_len, s_offset = struct.unpack(">II", tag_data[20:28])
                            if s_offset + s_len <= len(tag_data):
                                name = tag_data[s_offset:s_offset + s_len].decode("utf-16-be", "ignore").rstrip("\x00").strip()
                    break

        if not name:
            name = f"Profilo {color_space or 'ICC'} ({ver_str})"

        return ICCProfileInfo(
            name=name,
            color_space=color_space,
            profile_class=profile_class,
            version=ver_str,
            rendering_intent=rendering_intent,
            size_bytes=len(data),
            is_embedded=True,
        )
    except Exception:
        return None


def extract_output_intents(doc: fitz.Document) -> List[OutputIntentInfo]:
    """Extract OutputIntents dictionaries from the PDF document catalog."""
    intents: List[OutputIntentInfo] = []
    try:
        cat_xref = doc.pdf_catalog()
        k_type, k_val = doc.xref_get_key(cat_xref, "OutputIntents")
        if k_type == "null" or not k_val:
            return []

        oi_xrefs: List[int] = []
        if k_type == "xref":
            m = re.match(r"^(\d+)\s+0\s+R", k_val.strip())
            if m:
                oi_xrefs.append(int(m.group(1)))
        elif k_type == "array":
            oi_xrefs.extend(int(x) for x in re.findall(r"(\d+)\s+0\s+R", k_val))

        for oi_xref in oi_xrefs:
            subtype = _clean_pdf_string(doc.xref_get_key(oi_xref, "S")[1]).lstrip("/")
            ident = _clean_pdf_string(doc.xref_get_key(oi_xref, "OutputConditionIdentifier")[1])
            cond = _clean_pdf_string(doc.xref_get_key(oi_xref, "OutputCondition")[1])
            reg = _clean_pdf_string(doc.xref_get_key(oi_xref, "RegistryName")[1])
            info_val = _clean_pdf_string(doc.xref_get_key(oi_xref, "Info")[1])

            dest_type, dest_val = doc.xref_get_key(oi_xref, "DestOutputProfile")
            profile_info: Optional[ICCProfileInfo] = None
            if dest_type == "xref":
                m_prof = re.match(r"^(\d+)\s+0\s+R", dest_val.strip())
                if m_prof:
                    prof_xref = int(m_prof.group(1))
                    try:
                        if doc.xref_is_stream(prof_xref):
                            prof_data = doc.xref_stream(prof_xref)
                            profile_info = parse_icc_profile_bytes(prof_data)
                    except Exception:
                        pass

            dest_name = profile_info.name if profile_info else (ident or cond or info_val)

            intents.append(OutputIntentInfo(
                subtype=subtype or "OutputIntent",
                output_condition_identifier=ident or "Non specificato",
                output_condition=cond,
                registry_name=reg,
                info=info_val,
                dest_profile_name=dest_name,
                profile=profile_info,
            ))
    except Exception:
        pass
    return intents


def extract_pdfx_pdfa_conformance(
    doc: fitz.Document,
    output_intents: Optional[List[OutputIntentInfo]] = None,
) -> Tuple[str, str]:
    """Detect PDF/X and PDF/A conformance levels."""
    pdfx = ""
    pdfa = ""
    try:
        # Check Info dict via trailer
        trailer_str = doc.pdf_trailer()
        m_info = re.search(r"/Info\s+(\d+)\s+0\s+R", trailer_str)
        if m_info:
            info_xref = int(m_info.group(1))
            for key in ("GTS_PDFXVersion", "GTS_PDFXConformance"):
                t, v = doc.xref_get_key(info_xref, key)
                if t != "null" and v:
                    pdfx = _clean_pdf_string(v)
                    break

        # Check Catalog
        if not pdfx:
            cat = doc.pdf_catalog()
            t, v = doc.xref_get_key(cat, "GTS_PDFXVersion")
            if t != "null" and v:
                pdfx = _clean_pdf_string(v)

        # Check OutputIntents
        if not pdfx and output_intents:
            for oi in output_intents:
                if "GTS_PDFX" in oi.subtype or "PDF/X" in oi.subtype.upper():
                    pdfx = f"PDF/X ({oi.output_condition_identifier})"
                    break

        # Check XMP metadata
        if hasattr(doc, "get_xml_metadata"):
            xmp = doc.get_xml_metadata() or ""
            if xmp:
                if not pdfx:
                    m_x = re.search(r"<(?:pdfx|pdfxid):GTS_PDFXVersion>([^<]+)</", xmp)
                    if m_x:
                        pdfx = m_x.group(1).strip()
                m_part = re.search(r"<pdfaid:part>([^<]+)</", xmp)
                m_conf = re.search(r"<pdfaid:conformance>([^<]+)</", xmp)
                if m_part:
                    part = m_part.group(1).strip()
                    conf = m_conf.group(1).strip() if m_conf else ""
                    pdfa = f"PDF/A-{part}{conf}"
    except Exception:
        pass

    return pdfx or "Nessuna specifica rilevata", pdfa


def _get_resources_xref(doc: fitz.Document, page: fitz.Page) -> Optional[int]:
    """Resolve direct or inherited Resources dictionary xref for a page."""
    try:
        res_type, res_val = doc.xref_get_key(page.xref, "Resources")
        if res_type == "xref":
            return int(res_val.split()[0])
        if res_type == "dict":
            return page.xref
        p_type, p_val = doc.xref_get_key(page.xref, "Parent")
        while p_type == "xref":
            p_xref = int(p_val.split()[0])
            r_type, r_val = doc.xref_get_key(p_xref, "Resources")
            if r_type == "xref":
                return int(r_val.split()[0])
            if r_type == "dict":
                return p_xref
            p_type, p_val = doc.xref_get_key(p_xref, "Parent")
    except Exception:
        pass
    return None


def extract_page_color(
    doc: fitz.Document,
    page: fitz.Page,
    page_index: int,
) -> Tuple[PageColorInspection, List[SpotColorInfo], List[ICCProfileInfo]]:
    """Inspect all color spaces, spot separations, images, and intents on a single page."""
    page_number = page_index + 1
    has_cmyk = False
    has_rgb = False
    has_gray = False
    has_spot = False
    has_transparency = False
    has_overprint = False
    color_spaces: Set[str] = set()
    spot_names: Set[str] = set()
    rendering_intents: Set[str] = set()
    images_color_spaces: Dict[str, int] = {}
    images_profiles: List[str] = []
    page_spots: List[SpotColorInfo] = []
    page_profiles: List[ICCProfileInfo] = []

    try:
        res_xref = _get_resources_xref(doc, page)
        if res_xref:
            # 1. ColorSpace dictionary
            cs_type, cs_val = doc.xref_get_key(res_xref, "ColorSpace")
            cs_dict_xref = int(cs_val.split()[0]) if cs_type == "xref" else (res_xref if cs_type == "dict" else None)
            if cs_dict_xref:
                for k in doc.xref_get_keys(cs_dict_xref):
                    e_type, e_val = doc.xref_get_key(cs_dict_xref, k)
                    obj_str = doc.xref_object(int(e_val.split()[0])) if e_type == "xref" else e_val
                    if not obj_str:
                        continue

                    # Separation
                    if "/Separation" in obj_str:
                        has_spot = True
                        color_spaces.add("Separation (Tinta piatta)")
                        m_sep = re.search(r"/Separation\s*/([^\s/\[\]<>]+)(?:\s*/([^\s/\[\]<>]+))?", obj_str)
                        if m_sep:
                            raw_name = m_sep.group(1)
                            alt_space = m_sep.group(2) or "DeviceCMYK"
                            spot_name = _clean_pdf_string(raw_name)
                            if spot_name not in ("All", "None"):
                                spot_names.add(spot_name)
                                page_spots.append(SpotColorInfo(name=spot_name, alternate_space=_clean_pdf_string(alt_space), pages=(page_number,)))

                    # DeviceN
                    elif "/DeviceN" in obj_str:
                        has_spot = True
                        color_spaces.add("DeviceN (Multi-canale)")
                        m_devn = re.search(r"/DeviceN\s*\[([^\]]+)\](?:\s*/([^\s/\[\]<>]+))?", obj_str)
                        if m_devn:
                            colorants_raw = m_devn.group(1)
                            alt_space = m_devn.group(2) or "DeviceCMYK"
                            for c_match in re.findall(r"/([^\s/\[\]<>]+)", colorants_raw):
                                spot_name = _clean_pdf_string(c_match)
                                if spot_name not in ("All", "None"):
                                    spot_names.add(spot_name)
                                    page_spots.append(SpotColorInfo(name=spot_name, alternate_space=_clean_pdf_string(alt_space), pages=(page_number,)))

                    # ICCBased
                    elif "/ICCBased" in obj_str:
                        color_spaces.add("ICCBased")
                        m_stream = re.search(r"/ICCBased\s*(\d+)\s+0\s+R", obj_str)
                        if m_stream:
                            s_xref = int(m_stream.group(1))
                            try:
                                if doc.xref_is_stream(s_xref):
                                    icc = parse_icc_profile_bytes(doc.xref_stream(s_xref))
                                    if icc:
                                        page_profiles.append(icc)
                                        if "CMYK" in icc.color_space.upper():
                                            has_cmyk = True
                                        elif "RGB" in icc.color_space.upper():
                                            has_rgb = True
                                        elif "GRAY" in icc.color_space.upper():
                                            has_gray = True
                            except Exception:
                                pass

                    if "/DeviceCMYK" in obj_str:
                        has_cmyk = True
                        color_spaces.add("DeviceCMYK")
                    if "/DeviceRGB" in obj_str:
                        has_rgb = True
                        color_spaces.add("DeviceRGB")
                    if "/DeviceGray" in obj_str:
                        has_gray = True
                        color_spaces.add("DeviceGray")

            # 2. ExtGState dictionary
            eg_type, eg_val = doc.xref_get_key(res_xref, "ExtGState")
            eg_dict_xref = int(eg_val.split()[0]) if eg_type == "xref" else (res_xref if eg_type == "dict" else None)
            if eg_dict_xref:
                for k in doc.xref_get_keys(eg_dict_xref):
                    g_type, g_val = doc.xref_get_key(eg_dict_xref, k)
                    if g_type == "xref":
                        gs_xref = int(g_val.split()[0])
                        # Rendering Intent
                        ri_t, ri_v = doc.xref_get_key(gs_xref, "RI")
                        if ri_t != "null" and ri_v:
                            ri_clean = _clean_pdf_string(ri_v).lstrip("/")
                            if ri_clean:
                                rendering_intents.add(ri_clean)
                        # Overprint
                        op_t, op_v = doc.xref_get_key(gs_xref, "OP")
                        op_lower_t, op_lower_v = doc.xref_get_key(gs_xref, "op")
                        if op_v == "true" or op_lower_v == "true":
                            has_overprint = True
                        # Blend mode / transparency
                        bm_t, bm_v = doc.xref_get_key(gs_xref, "BM")
                        if bm_t != "null" and bm_v not in ("null", "/Normal", "/Compatible", ""):
                            has_transparency = True
                        # Opacity ca / CA
                        ca_t, ca_v = doc.xref_get_key(gs_xref, "ca")
                        ca_upper_t, ca_upper_v = doc.xref_get_key(gs_xref, "CA")
                        for cv in (ca_v, ca_upper_v):
                            try:
                                if cv != "null" and float(cv) < 0.999:
                                    has_transparency = True
                            except Exception:
                                pass
    except Exception:
        pass

    # 3. Raster images
    images_count = 0
    try:
        img_list = page.get_image_info(xrefs=True)
        images_count = len(img_list)
        for img in img_list:
            cs_num = img.get("colorspace", 0)
            cs_name = img.get("cs-name", "")
            if cs_num == 4 or "CMYK" in cs_name.upper():
                has_cmyk = True
                color_spaces.add("DeviceCMYK (Immagini)")
                images_color_spaces["CMYK"] = images_color_spaces.get("CMYK", 0) + 1
            elif cs_num == 3 or "RGB" in cs_name.upper():
                has_rgb = True
                color_spaces.add("DeviceRGB (Immagini)")
                images_color_spaces["RGB"] = images_color_spaces.get("RGB", 0) + 1
            elif cs_num == 1 or "GRAY" in cs_name.upper():
                has_gray = True
                color_spaces.add("DeviceGray (Immagini)")
                images_color_spaces["Scala di grigi"] = images_color_spaces.get("Scala di grigi", 0) + 1

            m_icc = re.search(r"ICCBased\([^,]+,\s*([^)]+)\)", cs_name)
            if m_icc:
                prof_name = m_icc.group(1).strip()
                if prof_name and prof_name not in images_profiles:
                    images_profiles.append(prof_name)
    except Exception:
        pass

    # 4. Content stream operators
    try:
        contents = page.read_contents()
        if contents:
            c_text = contents.decode("latin1", "ignore")
            if not has_cmyk and re.search(r"(?:^|\s)[0-9.]+\s+[0-9.]+\s+[0-9.]+\s+[0-9.]+\s+[kK](?:\s|$)", c_text):
                has_cmyk = True
                color_spaces.add("DeviceCMYK (Vettoriale)")
            if not has_rgb and re.search(r"(?:^|\s)[0-9.]+\s+[0-9.]+\s+[0-9.]+\s+[rR][gG](?:\s|$)", c_text):
                has_rgb = True
                color_spaces.add("DeviceRGB (Vettoriale)")
            if not has_gray and re.search(r"(?:^|\s)[0-9.]+\s+[gG](?:\s|$)", c_text):
                has_gray = True
                color_spaces.add("DeviceGray (Vettoriale)")
    except Exception:
        pass

    # 5. Vector drawings opacity
    try:
        for d in page.get_drawings():
            fo = d.get("fill_opacity")
            so = d.get("stroke_opacity")
            if (fo is not None and fo < 0.999) or (so is not None and so < 0.999):
                has_transparency = True
                break
    except Exception:
        pass

    page_color = PageColorInspection(
        page_index=page_index,
        page_number=page_number,
        has_cmyk=has_cmyk,
        has_rgb=has_rgb,
        has_gray=has_gray,
        has_spot=has_spot,
        has_transparency=has_transparency,
        has_overprint=has_overprint,
        color_spaces=sorted(color_spaces),
        spot_colors=sorted(spot_names),
        rendering_intents=sorted(rendering_intents),
        images_count=images_count,
        images_color_spaces=images_color_spaces,
        images_profiles=images_profiles,
    )
    return page_color, page_spots, page_profiles


def inspect_document_color(
    doc: fitz.Document,
    page_data: List[Tuple[PageColorInspection, List[SpotColorInfo], List[ICCProfileInfo]]],
) -> DocumentColorInspection:
    """Aggregate document-level color inspection across all pages and catalog."""
    output_intents = extract_output_intents(doc)
    pdfx_conf, pdfa_conf = extract_pdfx_pdfa_conformance(doc, output_intents)

    has_cmyk = any(p[0].has_cmyk for p in page_data)
    has_rgb = any(p[0].has_rgb for p in page_data)
    has_gray = any(p[0].has_gray for p in page_data)
    has_spot = any(p[0].has_spot for p in page_data)
    has_transparency = any(p[0].has_transparency for p in page_data)
    has_overprint = any(p[0].has_overprint for p in page_data)

    all_spaces: Set[str] = set()
    all_intents: Set[str] = set()
    spots_map: Dict[str, Dict] = {}
    profiles_map: Dict[str, ICCProfileInfo] = {}

    # Include OutputIntent embedded profiles
    for oi in output_intents:
        if oi.profile:
            profiles_map[oi.profile.name] = oi.profile

    for p_col, p_spots, p_profs in page_data:
        all_spaces.update(p_col.color_spaces)
        all_intents.update(p_col.rendering_intents)
        for prof in p_profs:
            if prof.name not in profiles_map:
                profiles_map[prof.name] = prof
        for sp in p_spots:
            if sp.name not in spots_map:
                spots_map[sp.name] = {"alt": sp.alternate_space, "pages": set(sp.pages)}
            else:
                spots_map[sp.name]["pages"].update(sp.pages)

    spot_colors = [
        SpotColorInfo(name=name, alternate_space=data["alt"], pages=tuple(sorted(data["pages"])))
        for name, data in sorted(spots_map.items())
    ]

    # Determine primary color space label
    if has_cmyk and has_spot and not has_rgb:
        primary = f"CMYK + {len(spot_colors)} Tinte Piatte"
    elif has_cmyk and not has_rgb and not has_spot:
        primary = "CMYK (Prestampa Standard)"
    elif has_cmyk and has_rgb:
        primary = "Misto CMYK + RGB (Verificare in prestampa)"
    elif has_rgb and not has_cmyk and not has_spot:
        primary = "RGB (Spazio colore schermo)"
    elif has_spot and not has_cmyk and not has_rgb:
        primary = f"Tinte Piatte ({len(spot_colors)} separazioni)"
    elif has_gray and not has_cmyk and not has_rgb and not has_spot:
        primary = "Scala di Grigi (Monocromatico)"
    else:
        primary = "Non determinato"

    return DocumentColorInspection(
        pdfx_conformance=pdfx_conf,
        pdfa_conformance=pdfa_conf,
        primary_color_space=primary,
        has_cmyk=has_cmyk,
        has_rgb=has_rgb,
        has_gray=has_gray,
        has_spot=has_spot,
        has_rgb_warning=has_rgb,
        has_transparency=has_transparency,
        has_overprint=has_overprint,
        color_spaces_detected=sorted(all_spaces),
        output_intents=output_intents,
        spot_colors=spot_colors,
        embedded_profiles=list(profiles_map.values()),
        rendering_intents=sorted(all_intents),
    )


def inspect_page(doc: fitz.Document, page_index: int) -> PageInspection:
    """Extract page dimensions, all 5 boundary boxes, and color inspection for a given page."""
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

    page_color, _, _ = extract_page_color(doc, page, page_index)

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
        color=page_color,
    )


def inspect_document(doc: fitz.Document, file_path: str = "") -> DocumentInspection:
    """Inspect full document metadata, general info, page boxes, and color preflight."""
    path = file_path or doc.name or ""
    size_bytes = 0
    if path and os.path.isfile(path):
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = 0

    meta = doc.metadata or {}
    pdf_version = meta.get("format") or "PDF"

    pages: List[PageInspection] = []
    page_color_data: List[Tuple[PageColorInspection, List[SpotColorInfo], List[ICCProfileInfo]]] = []

    for i in range(len(doc)):
        page = doc[i]
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

        p_color, p_spots, p_profs = extract_page_color(doc, page, i)
        page_color_data.append((p_color, p_spots, p_profs))

        pages.append(PageInspection(
            page_index=i,
            page_number=i + 1,
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
            color=p_color,
        ))

    doc_color = inspect_document_color(doc, page_color_data)

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
        color=doc_color,
    )
