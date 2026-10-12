"""
Tests for printable PDF annotations and form field values flattening (LD-ANNOT-1).
"""

import os
import fitz
import pytest

from laydown.engine.data_model import (
    LayoutSettings,
    Orientation,
    PreprocessorStep,
    PreprocessorType,
    Project,
    RotateAngle,
    SheetSettings,
)
from laydown.engine.impose import impose
from laydown.engine.preflight import Severity, preflight
from laydown.engine.preprocessors import apply_preprocessors


def _create_annotated_source(
    path: str,
    rotation: int = 0,
    include_print_annot: bool = True,
    include_screen_annot: bool = True,
    include_form: bool = True,
    include_hidden_annot: bool = False,
    include_noview_annot: bool = False,
) -> str:
    """Create a test PDF with various annotations and form fields."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if rotation:
        page.set_rotation(rotation)

    page.insert_text((72, 100), "BODYTEXT", fontsize=14)

    if include_print_annot:
        a = page.add_freetext_annot(
            fitz.Rect(72, 150, 300, 190), "ANNOTTEXT", fontsize=14
        )
        a.set_flags(fitz.PDF_ANNOT_IS_PRINT)
        a.update()

    if include_screen_annot:
        a = page.add_freetext_annot(
            fitz.Rect(72, 210, 300, 250), "SCREENONLY", fontsize=14
        )
        a.set_flags(0)  # screen-only: no print flag
        a.update()

    if include_form:
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = "test_field"
        w.rect = fitz.Rect(72, 270, 300, 300)
        w.field_value = "FORMVALUE"
        w.text_fontsize = 14
        page.add_widget(w)

    if include_hidden_annot:
        a = page.add_freetext_annot(
            fitz.Rect(72, 320, 300, 360), "HIDDENTEXT", fontsize=14
        )
        a.set_flags(fitz.PDF_ANNOT_IS_PRINT | fitz.PDF_ANNOT_IS_HIDDEN)
        a.update()

    if include_noview_annot:
        a = page.add_freetext_annot(
            fitz.Rect(72, 380, 300, 420), "NOVIEWTEXT", fontsize=14
        )
        a.set_flags(fitz.PDF_ANNOT_IS_PRINT | fitz.PDF_ANNOT_IS_NO_VIEW)
        a.update()

    doc.save(path)
    doc.close()
    return path


def _page_words(path: str, page_no: int = 0) -> set:
    with fitz.open(path) as d:
        return {w[4] for w in d[page_no].get_text("words")}


class TestAnnotationBaking:
    def test_printable_annotations_and_forms_appear_in_output(self, tmp_path):
        """
        A source with print-flagged FreeText 'ANNOTTEXT', screen-only 'SCREENONLY',
        and filled form 'FORMVALUE' -> output has ANNOTTEXT and FORMVALUE, not SCREENONLY;
        source file is byte-identical; preflight returns NOTE (not WARNING).
        """
        src = str(tmp_path / "src.pdf")
        out = str(tmp_path / "out.pdf")
        _create_annotated_source(src)

        before_bytes = open(src, "rb").read()

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="A4", orientation=Orientation.PORTRAIT)
        project.layout = LayoutSettings(nup=1, rows=1, cols=1)

        # Preflight check
        with fitz.open(src) as doc:
            findings = preflight(project, doc)

        note_findings = [
            f for f in findings
            if f.severity is Severity.NOTE and "flattened" in f.message
        ]
        warn_findings = [
            f for f in findings
            if f.severity is Severity.WARNING
            and ("annotation" in f.message or "form field" in f.message)
        ]

        assert len(note_findings) == 1
        assert "2 annotations and 1 form field" in note_findings[0].message
        assert len(warn_findings) == 0

        # Impose check
        impose(project, out)

        after_bytes = open(src, "rb").read()
        assert before_bytes == after_bytes, "Source file on disk was modified"

        words = _page_words(out)
        assert "BODYTEXT" in words
        assert "ANNOTTEXT" in words
        assert "FORMVALUE" in words
        assert "SCREENONLY" not in words

    def test_rotated_source_shows_annottext_on_sheet(self, tmp_path):
        """The same source with /Rotate 90 still shows ANNOTTEXT on the sheet."""
        src = str(tmp_path / "src_rot90.pdf")
        out = str(tmp_path / "out_rot90.pdf")
        _create_annotated_source(src, rotation=90)

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="A4", orientation=Orientation.PORTRAIT)
        project.layout = LayoutSettings(nup=1, rows=1, cols=1)

        with fitz.open(src) as doc:
            findings = preflight(project, doc)

        assert any(
            f.severity is Severity.NOTE and "flattened" in f.message
            for f in findings
        )
        assert not any(
            f.severity is Severity.WARNING
            and ("annotation" in f.message or "form field" in f.message)
            for f in findings
        )

        impose(project, out)
        words = _page_words(out)
        assert "BODYTEXT" in words
        assert "ANNOTTEXT" in words
        assert "FORMVALUE" in words
        assert "SCREENONLY" not in words

    @pytest.mark.parametrize("angle", [180, 270])
    def test_rotated_source_other_angles(self, tmp_path, angle):
        """Source rotated 180 or 270 degrees still flattens correctly."""
        src = str(tmp_path / f"src_rot{angle}.pdf")
        out = str(tmp_path / f"out_rot{angle}.pdf")
        _create_annotated_source(src, rotation=angle)

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="A4", orientation=Orientation.PORTRAIT)
        project.layout = LayoutSettings(nup=1, rows=1, cols=1)

        impose(project, out)
        words = _page_words(out)
        assert "ANNOTTEXT" in words
        assert "FORMVALUE" in words
        assert "SCREENONLY" not in words

    def test_hidden_annotation_is_dropped(self, tmp_path):
        """Annotations with PDF_ANNOT_IS_HIDDEN are dropped from output."""
        src = str(tmp_path / "src_hidden.pdf")
        out = str(tmp_path / "out_hidden.pdf")
        _create_annotated_source(src, include_hidden_annot=True)

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="A4", orientation=Orientation.PORTRAIT)
        project.layout = LayoutSettings(nup=1, rows=1, cols=1)

        impose(project, out)
        words = _page_words(out)
        assert "ANNOTTEXT" in words
        assert "HIDDENTEXT" not in words
        assert "SCREENONLY" not in words

    def test_noview_print_flagged_annotation_is_baked(self, tmp_path):
        """Annotations with PDF_ANNOT_IS_NO_VIEW and PDF_ANNOT_IS_PRINT are kept."""
        src = str(tmp_path / "src_noview.pdf")
        out = str(tmp_path / "out_noview.pdf")
        _create_annotated_source(src, include_noview_annot=True)

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="A4", orientation=Orientation.PORTRAIT)
        project.layout = LayoutSettings(nup=1, rows=1, cols=1)

        impose(project, out)
        words = _page_words(out)
        assert "ANNOTTEXT" in words
        assert "NOVIEWTEXT" in words

    def test_plain_pdf_untouched_in_apply_preprocessors(self, tmp_path):
        """Documents without annotations or widgets are not baked or altered."""
        src = str(tmp_path / "plain.pdf")
        doc = fitz.open()
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), "PLAIN", fontsize=14)
        doc.save(src)
        doc.close()

        opened = fitz.open(src)
        stream_before = opened[0].read_contents()
        apply_preprocessors(opened, [])
        stream_after = opened[0].read_contents()
        opened.close()

        assert stream_before == stream_after

    def test_preflight_page_range_filters_annotations(self, tmp_path):
        """Preflight only counts annotations on pages in the active page range."""
        src = str(tmp_path / "multipage.pdf")
        doc = fitz.open()
        # Page 0: no annotations
        doc.new_page(width=595, height=842)
        # Page 1: with annotations
        p2 = doc.new_page(width=595, height=842)
        a = p2.add_freetext_annot(fitz.Rect(72, 100, 300, 140), "PAGE2TEXT")
        a.set_flags(fitz.PDF_ANNOT_IS_PRINT)
        a.update()
        doc.save(src)
        doc.close()

        project = Project(source_pdf_path=src)
        project.layout.page_range = "1"
        with fitz.open(src) as d:
            findings = preflight(project, d)
        assert not any("flattened" in f.message for f in findings)

        project.layout.page_range = "1-2"
        with fitz.open(src) as d:
            findings = preflight(project, d)
        assert any("flattened" in f.message for f in findings)
