"""
LD-PF-1 — preflight evaluates the job that will actually be imposed.

Preflight must look at the same pages the imposition will place: preprocessors
applied (on a copy — the caller's document is never touched), the page range
selected, and every imposed page's boxes read in the space the page renders in.
And it must never raise for a document it cannot read — an ERROR finding is the
operator's only warning.
"""

import fitz
import pytest

from laydown.engine.data_model import (
    LayoutSettings, LayoutType, MarkItem, MarkType, Orientation, PageScaling,
    PreprocessorStep, PreprocessorType, Project, RotateAngle, SheetSettings,
    SourceBox, SourceSettings,
)
from laydown.engine.preflight import Severity, preflight
from laydown.engine.utils import mm_to_pt

from tests.helpers import make_source_pdf


def a_project(src="", **kw):
    p = Project(source_pdf_path=src)
    p.source = SourceSettings(box=kw.pop("box", SourceBox.TRIM),
                              bleed_mm=kw.pop("bleed", 0.0))
    p.sheet = SheetSettings(
        preset=kw.pop("preset", "A3"),
        orientation=kw.pop("orientation", Orientation.LANDSCAPE),
        margin_top_mm=kw.pop("margin", 5.0),
    )
    for side in ("bottom", "left", "right"):
        setattr(p.sheet, f"margin_{side}_mm", p.sheet.margin_top_mm)
    p.layout = LayoutSettings(**kw)
    return p


def run_preflight(project, src=None):
    """preflight() with the source opened and closed around it."""
    doc = fitz.open(src) if src else None
    try:
        return preflight(project, doc)
    finally:
        if doc:
            doc.close()


def with_messages(findings, *words):
    return [f for f in findings if all(w.lower() in f.message.lower() for w in words)]


def rotated_source(path, angle=90, **kw):
    """make_source_pdf, then every page given a /Rotate entry."""
    make_source_pdf(path, **kw)
    doc = fitz.open(path)
    for page in doc:
        page.set_rotation(angle)
    doc.saveIncr()
    doc.close()
    return path


def encrypted_source(path):
    doc = fitz.open()
    doc.new_page(width=mm_to_pt(210), height=mm_to_pt(297))
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
             user_pw="secret", owner_pw="secret")
    doc.close()
    return path


def annotated_source(path, page_no=0, annot=True, field=True):
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=mm_to_pt(210), height=mm_to_pt(297))
    page = doc[page_no]
    if annot:
        page.add_text_annot(fitz.Point(50, 50), "a sticky note")
    if field:
        w = fitz.Widget()
        w.field_name = "customer"
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(50, 80, 150, 100)
        page.add_widget(w)
    doc.save(path)
    doc.close()
    return path


# ── page range and preprocessors ────────────────────


class TestTheJobAsImposed:
    def test_a_page_range_that_folds_needs_no_padding_note(self, tmp_path):
        # The old check counted the raw document: a 1-8 range of 10 pages said
        # "10 pages will be padded to 12".
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=10)
        project = a_project(src, layout_type=LayoutType.BOOKLET, page_range="1-8")
        assert not with_messages(run_preflight(project, src), "padded")

    def test_the_padding_note_counts_the_selected_pages(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=10)
        project = a_project(src, layout_type=LayoutType.BOOKLET, page_range="1-6")
        assert any("6 pages will be padded to 8" in f.message
                   for f in run_preflight(project, src))

    def test_a_page_range_off_the_document_is_an_error_not_an_exception(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=10)
        project = a_project(src, page_range="1-99")
        findings = run_preflight(project, src)
        errors = [f for f in findings if f.severity is Severity.ERROR]
        assert errors and "page range" in errors[0].message.lower()

    def test_preprocessors_change_what_preflight_sees(self, tmp_path):
        # Halved A4 pages in an A3 2-up cell are enlarged to ~193%, not printed
        # at the raw document's ~97%.
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=4)
        project = a_project(src, scaling=PageScaling.FIT_TO_CELL)
        project.preprocessors = [
            PreprocessorStep(type=PreprocessorType.SCALE_PAGES, scale_factor=0.5)]
        findings = run_preflight(project, src)
        enlarged = with_messages(findings, "enlarged")
        assert enlarged and enlarged[0].severity is Severity.WARNING
        assert "193%" in enlarged[0].message

    def test_preprocessors_run_on_a_copy_not_the_callers_document(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2)
        project = a_project(src)
        project.preprocessors = [
            PreprocessorStep(type=PreprocessorType.ROTATE_PAGES,
                             rotate_angle=RotateAngle.CW_90)]
        doc = fitz.open(src)
        try:
            findings = preflight(project, doc)
            assert findings  # the job was inspected, not skipped
            assert len(doc) == 2  # and the caller's document survived intact
            assert doc[0].rotation == 0
            assert doc[1].rotation == 0
        finally:
            doc.close()

    def test_a_broken_preprocessor_is_an_error_not_an_exception(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2)
        project = a_project(src)
        project.preprocessors = [
            PreprocessorStep(type=PreprocessorType.REORDER_PAGES, page_order="1,5")]
        findings = run_preflight(project, src)
        errors = [f for f in findings if f.severity is Severity.ERROR]
        assert errors and "preprocessor" in errors[0].message.lower()


# ── rotated pages ───────────────────────────────────


class TestRotatedBoxChecks:
    def test_a_rotated_page_with_a_trim_box_is_not_flagged(self, tmp_path):
        src = rotated_source(str(tmp_path / "rot.pdf"), n_pages=2, trim_inset_mm=10)
        findings = run_preflight(a_project(src, gutter_h_mm=20), src)
        assert not with_messages(findings, "no trim box")
        assert not with_messages(findings, "different page sizes")
        assert not with_messages(findings, "carries")

    def test_a_rotated_page_without_a_trim_box_still_gets_the_note(self, tmp_path):
        # The old comparison was trimbox == rect across rotated and unrotated
        # spaces, so a turned page without a trim box escaped the note entirely.
        src = rotated_source(str(tmp_path / "rot.pdf"), n_pages=2)
        notes = with_messages(run_preflight(a_project(src), src), "no trim box")
        assert notes and notes[0].severity is Severity.NOTE

    @pytest.mark.parametrize("angle", [90, 270])
    def test_bleed_availability_is_measured_in_the_rendered_space(self, tmp_path, angle):
        src = rotated_source(str(tmp_path / "rot.pdf"), angle=angle,
                             n_pages=2, trim_inset_mm=10)
        # 10 mm of artwork exists beyond the trim on every side, rotation or not.
        findings = run_preflight(a_project(src, bleed=5.0, gutter_h_mm=20), src)
        assert not with_messages(findings, "carries")

        findings = run_preflight(a_project(src, bleed=20.0, gutter_h_mm=60), src)
        tight = with_messages(findings, "carries")
        assert tight and tight[0].severity is Severity.WARNING
        assert "10.0 mm" in tight[0].message


# ── enlargement ─────────────────────────────────────


class TestEnlargement:
    def test_pages_enlarged_to_fill_the_cell_get_a_warning_with_the_percentage(
            self, tmp_path):
        # 90x50 mm cards in an A3 2-up cell (205x287 mm after margins) are
        # blown up to 205/90 = 228%.
        src = make_source_pdf(str(tmp_path / "cards.pdf"), n_pages=4,
                              w_mm=90, h_mm=50)
        findings = run_preflight(a_project(src, scaling=PageScaling.FIT_TO_CELL), src)
        enlarged = with_messages(findings, "enlarged")
        assert enlarged and enlarged[0].severity is Severity.WARNING
        assert "228%" in enlarged[0].message

    def test_the_false_full_size_text_is_gone(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=4)
        findings = run_preflight(a_project(src), src)
        assert findings
        assert not any("full size" in f.detail.lower() for f in findings)
        # The honest text says pages are scaled to fill the cell, both ways.
        scaled = with_messages(findings, "scaled to")
        assert scaled and "fill their cell" in scaled[0].detail


# ── encrypted sources ───────────────────────────────


class TestEncrypted:
    def test_an_encrypted_source_is_an_error_not_an_exception(self, tmp_path):
        src = encrypted_source(str(tmp_path / "secret.pdf"))
        project = a_project(src)
        findings = run_preflight(project, src)
        errors = [f for f in findings if f.severity is Severity.ERROR]
        assert errors and "password" in errors[0].message.lower()
        assert errors[0].detail

    def test_no_other_findings_are_invented_for_a_locked_document(self, tmp_path):
        src = encrypted_source(str(tmp_path / "secret.pdf"))
        findings = run_preflight(a_project(str(src)), src)
        assert [f.severity for f in findings] == [Severity.ERROR]


# ── custom marks ────────────────────────────────────


class TestCustomMarks:
    def test_a_configured_mark_pdf_that_does_not_exist_is_warned_about(self, tmp_path):
        project = a_project()
        project.marks = [MarkItem(mark_type=MarkType.CUSTOM_MARK,
                                  mark_pdf_path=str(tmp_path / "absent.pdf"))]
        missing = with_messages(preflight(project), "custom mark")
        assert missing and missing[0].severity is Severity.WARNING
        assert "does not exist" in missing[0].message

    def test_a_corrupt_mark_pdf_is_warned_about(self, tmp_path):
        junk = tmp_path / "junk.pdf"
        junk.write_bytes(b"this is not a PDF at all")
        project = a_project()
        project.marks = [MarkItem(mark_type=MarkType.CUSTOM_MARK, mark_pdf_path=str(junk))]
        broken = with_messages(preflight(project), "custom mark")
        assert broken and broken[0].severity is Severity.WARNING

    def test_a_readable_mark_pdf_stays_quiet(self, tmp_path):
        art = tmp_path / "art.pdf"
        doc = fitz.open()
        doc.new_page(width=100, height=100)
        doc.save(str(art))
        doc.close()
        project = a_project()
        project.marks = [MarkItem(mark_type=MarkType.CUSTOM_MARK, mark_pdf_path=str(art))]
        assert not with_messages(preflight(project), "custom mark")

    def test_a_disabled_mark_is_not_checked(self, tmp_path):
        project = a_project()
        project.marks = [MarkItem(mark_type=MarkType.CUSTOM_MARK, enabled=False,
                                  mark_pdf_path=str(tmp_path / "absent.pdf"))]
        assert not with_messages(preflight(project), "custom mark")


# ── annotations and form fields ─────────────────────


class TestAnnotationsAndForms:
    def test_annotations_and_form_fields_are_warned_about(self, tmp_path):
        src = annotated_source(str(tmp_path / "sticky.pdf"))
        findings = run_preflight(a_project(src), src)
        carried = with_messages(findings, "will not appear in the output")
        assert carried and carried[0].severity is Severity.WARNING
        assert "1 annotation" in carried[0].message
        assert "1 form field" in carried[0].message

    def test_a_plain_source_stays_quiet(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2)
        findings = run_preflight(a_project(src), src)
        assert not with_messages(findings, "will not appear in the output")

    def test_annotations_outside_the_page_range_are_ignored(self, tmp_path):
        src = annotated_source(str(tmp_path / "sticky.pdf"), page_no=1)
        project = a_project(src, page_range="1")
        assert not with_messages(run_preflight(project, src),
                                 "will not appear in the output")
        project = a_project(src, page_range="1-2")
        assert with_messages(run_preflight(project, src),
                             "will not appear in the output")


# ── bleed meeting its neighbours ────────────────────


class TestBleedOverlap:
    def test_bleed_wider_than_half_the_gutter_is_warned_about(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2)
        findings = run_preflight(a_project(src, bleed=3.0), src)
        overlap = with_messages(findings, "half the gutter")
        assert overlap and overlap[0].severity is Severity.WARNING

    def test_bleed_within_half_the_gutter_stays_quiet(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2)
        project = a_project(src, bleed=3.0, gutter_h_mm=6.0)
        assert not with_messages(run_preflight(project, src), "half the gutter")

    def test_a_single_cell_has_no_neighbour_to_print_on(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=1)
        project = a_project(src, bleed=3.0, nup=1)
        assert not with_messages(run_preflight(project, src), "half the gutter")

    def test_bleed_on_a_booklet_is_warned_about_at_the_fold(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=8)
        project = a_project(src, bleed=3.0, layout_type=LayoutType.BOOKLET)
        findings = run_preflight(project, src)
        fold = with_messages(findings, "fold")
        assert fold and fold[0].severity is Severity.WARNING

    def test_a_booklet_without_bleed_stays_quiet_at_the_fold(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=8)
        project = a_project(src, layout_type=LayoutType.BOOKLET)
        assert not with_messages(run_preflight(project, src), "fold")
