"""
Output-side mark checks — the press correctness of the marks themselves.

The 2026-10-12 review found every mark drawn in DeviceRGB, a colour bar that
wasn't process CMYK, and a Text Label whose custom text the engine never
read. Owner decision 2026-10-12: crop, gap-crop, registration, folding,
perforation and collating marks print in the Registration separation
(/Separation /All — every plate inks), the colour bar is DeviceCMYK solids,
the label is K-only CMYK. These tests pin that behaviour on the *output*:
the page resources, the raw content operators, the extracted words and the
rendered pixels.
"""

import re

import fitz

from laydown.engine.data_model import (
    LayoutSettings, LayoutType, MarkItem, MarkType, Orientation, Project,
    SheetSettings, SourceBox, SourceSettings,
)
from laydown.engine.impose import impose, impose_detailed
from laydown.engine.utils import mm_to_pt


def a_project(src="", **kw):
    p = Project(source_pdf_path=src)
    p.source = SourceSettings(box=kw.pop("box", SourceBox.TRIM))
    p.sheet = SheetSettings(
        preset=kw.pop("preset", "A3"),
        orientation=kw.pop("orientation", Orientation.LANDSCAPE),
        margin_top_mm=kw.pop("margin", 5.0),
    )
    for side in ("bottom", "left", "right"):
        setattr(p.sheet, f"margin_{side}_mm", p.sheet.margin_top_mm)
    p.layout = LayoutSettings(**kw)
    return p


def _page_content(path):
    """The page's own content streams (not the placed XObjects), as text."""
    return fitz.open(path)[0].read_contents().decode("latin-1")


def _csreg_resource(doc, page):
    """The /CSReg colour-space resource string, resolving indirect dicts."""
    holder = page.xref
    for key in ("Resources", "ColorSpace"):
        kind, value = doc.xref_get_key(holder, key)
        if kind == "xref":
            holder = int(value.split()[0])
    return doc.xref_get_key(holder, "ColorSpace/CSReg")


# ── the Registration separation ─────────────────────


class TestRegistrationColour:
    def test_marks_use_the_registration_separation(self, source_pdf, out_path):
        src = source_pdf(n_pages=4)
        project = a_project(src, margin=12.0)
        project.marks = [MarkItem(mark_type=t) for t in (
            MarkType.CROP_MARKS, MarkType.REGISTRATION,
        )]
        out = out_path()
        impose(project, out)

        doc = fitz.open(out)
        page = doc[0]
        # the resource: a /Separation /All space whose tint 1 is 100% of
        # every ink — the mark prints on every plate the press pulls
        kind, cs = _csreg_resource(doc, page)
        assert kind != "null", "no /CSReg colour space on the output page"
        compact = cs.replace(" ", "")
        assert "/Separation/All" in compact
        assert "C1[1111]" in compact, "tint 1 must be 100% of all four inks"

        content = page.read_contents().decode("latin-1")
        assert "/CSReg CS 1 SCN" in content, "marks are not stroked in Registration"
        assert not re.search(r"\bRG\b", content), "a DeviceRGB stroke operator survived"
        assert not re.search(r"\brg\b", content), "a DeviceRGB fill operator survived"

    def test_every_line_and_shape_mark_paints_registration(self, source_pdf, out_path):
        # crop, gap-crop, trim-line (a DeviceGray guide, still not RGB),
        # registration bull's-eyes and the collating step — one ganged sheet
        src = source_pdf(n_pages=4)
        project = a_project(src, margin=12.0)
        project.marks = [MarkItem(mark_type=t) for t in (
            MarkType.CROP_MARKS, MarkType.GAP_CROP_MARKS, MarkType.TRIM_LINE,
            MarkType.REGISTRATION, MarkType.COLLATING_MARKS,
        )]
        out = out_path()
        impose(project, out)

        content = _page_content(out)
        assert "/CSReg CS 1 SCN" in content   # strokes (crop/gap/reg lines)
        assert "/CSReg cs 1 scn" in content   # fills (the collating step)
        assert not re.search(r"\bRG\b", content)
        assert not re.search(r"\brg\b", content)

        # the bull's-eye circle travelled as Béziers in the same colour
        page = fitz.open(out)[0]
        curved = [d for d in page.get_drawings()
                  if any(item[0] == "c" for item in d["items"])]
        assert curved and all(d.get("color") == (0.0, 0.0, 0.0) for d in curved)

    def test_fold_and_perforation_marks_paint_registration(self, source_pdf, out_path):
        src = source_pdf(n_pages=8)
        project = a_project(src, layout_type=LayoutType.BOOKLET, margin=12.0)
        project.marks = [MarkItem(mark_type=t) for t in (
            MarkType.FOLDING_MARKS, MarkType.PERFORATION_MARKS,
        )]
        out = out_path()
        impose(project, out)

        content = _page_content(out)
        assert "/CSReg CS 1 SCN" in content
        assert not re.search(r"\bRG\b", content)
        # the dashed and dotted rules kept their patterns
        assert "[2 2] 0 d" in content, "folding marks lost their dash pattern"
        assert "[1 3] 0 d" in content, "perforation marks lost their dot pattern"

    def test_marks_are_visible_ink_on_the_sheet(self, source_pdf, out_path):
        # What actually reaches paper: render the sheet and sample the pixel
        # under a known crop-mark segment.
        src = source_pdf(n_pages=2, w_mm=210, h_mm=297)
        project = a_project(src, margin=12.0)
        project.marks = [MarkItem(mark_type=MarkType.CROP_MARKS)]
        out = out_path()
        result = impose_detailed(project, out)

        page = fitz.open(out)[0]
        pix = page.get_pixmap(dpi=150)
        s = 150 / 72
        trim = result.placed[0][0].trim
        # the horizontal segment running left of the trim's top-left corner
        x = trim.x0 - mm_to_pt(3.0) - mm_to_pt(5.0) / 2
        y = trim.y0
        samples = [pix.pixel(int((x + dx) * s), int((y + dy) * s))[:3]
                   for dx in (-0.3, 0.0, 0.3) for dy in (-0.3, 0.0, 0.3)]
        assert any(sum(p) < 380 for p in samples), (
            f"no ink at the crop mark ({x:.1f}, {y:.1f}) pt — nearest samples {samples}"
        )


# ── the colour bar ──────────────────────────────────


class TestColourBar:
    def test_the_bar_is_devicecmyk_process_solids(self, source_pdf, out_path):
        src = source_pdf(n_pages=2)
        project = a_project(src, margin=12.0)
        project.marks = [MarkItem(mark_type=MarkType.COLOR_BAR, patch_size_mm=5.0)]
        out = out_path()
        impose(project, out)

        content = _page_content(out)
        # every process solid and overprint, as DeviceCMYK `k` fills
        for cmyk in ("1 0 0 0", "0 1 0 0", "0 0 1 0", "0 0 0 1",
                     "1 1 0 0", "0 1 1 0", "1 0 1 0", "1 1 1 0"):
            assert f"{cmyk} k" in content, f"missing DeviceCMYK solid {cmyk} in the bar"
        assert not re.search(r"\brg\b", content), "the bar still fills in DeviceRGB"

        # same count and geometry as the old bar: one patch per entry
        page = fitz.open(out)[0]
        filled = [d for d in page.get_drawings() if d.get("fill")]
        assert len(filled) == 8


# ── the text label ──────────────────────────────────


class TestTextLabel:
    def test_custom_label_text_reaches_the_sheet(self, source_pdf, out_path):
        src = source_pdf(n_pages=2)
        project = a_project(src, margin=12.0)
        project.marks = [MarkItem(mark_type=MarkType.TEXT_LABEL, label_text="JOB4711")]
        out = out_path()
        impose(project, out)

        page = fitz.open(out)[0]
        words = {w[4] for w in page.get_text("words")}
        assert "JOB4711" in words, "the custom label text never reached the sheet"
        # custom text replaces the auto text, and the sheet counter stays
        assert "src.pdf" not in words
        assert {"Sheet", "1", "of", "1"} <= words

        # the label is K-only CMYK, not DeviceRGB
        content = page.read_contents().decode("latin-1")
        assert "0 0 0 .4 k" in content
        assert not re.search(r"\brg\b", content)

    def test_empty_label_keeps_the_auto_text(self, source_pdf, out_path):
        src = source_pdf(n_pages=2)
        project = a_project(src, margin=12.0)
        project.marks = [MarkItem(mark_type=MarkType.TEXT_LABEL)]  # "" = auto
        out = out_path()
        impose(project, out)

        words = {w[4] for w in fitz.open(out)[0].get_text("words")}
        assert "src.pdf" in words, "the auto filename label is gone"
        assert {"Sheet", "1", "of", "1"} <= words
