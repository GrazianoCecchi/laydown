"""
Tests for the PDF Composer and Merger engine (laydown.engine.composer).
"""

import os
import pytest
import fitz

from laydown.engine.composer import (
    ComposerIntent,
    CompositionGeometryConfig,
    PageSpec,
    ParseResult,
    SourceDocInfo,
    compose_pdf,
    detect_format_name,
    diagnose_sources_geometry,
    inspect_source_pdf,
    parse_page_sequence,
)


@pytest.fixture
def sample_pdfs(tmp_path):
    """Create three sample PDFs with different formats, page counts and bleed setups."""
    # Doc A: 12 pages (A4 standard: 595.28 x 841.89 pt = 210 x 297 mm, NO bleed)
    path_a = str(tmp_path / "doc_a.pdf")
    doc_a = fitz.open()
    for i in range(12):
        page = doc_a.new_page(width=595.28, height=841.89)  # A4
        page.insert_text((50, 100), f"Doc A - Page {i + 1}")
    doc_a.save(path_a)
    doc_a.close()

    # Doc B: 4 pages (A4 with 3mm bleed = 216 x 303 mm = ~612.28 x 858.89 pt, has inner TrimBox)
    path_b = str(tmp_path / "doc_b.pdf")
    doc_b = fitz.open()
    bleed_pt = 8.50  # ~3mm
    for i in range(4):
        # MediaBox: 595.28 + 17 x 841.89 + 17
        page = doc_b.new_page(width=595.28 + 2 * bleed_pt, height=841.89 + 2 * bleed_pt)
        page.set_trimbox(fitz.Rect(bleed_pt, bleed_pt, bleed_pt + 595.28, bleed_pt + 841.89))
        page.insert_text((50 + bleed_pt, 100 + bleed_pt), f"Doc B (bleed) - Page {i + 1}")
    doc_b.save(path_b)
    doc_b.close()

    # Doc C: 5 pages (A3 landscape: 420 x 297 mm = 1190.55 x 841.89 pt)
    path_c = str(tmp_path / "doc_c.pdf")
    doc_c = fitz.open()
    for i in range(5):
        page = doc_c.new_page(width=1190.55, height=841.89)
        page.insert_text((50, 100), f"Doc C (A3) - Page {i + 1}")
    doc_c.save(path_c)
    doc_c.close()

    return {"A": path_a, "B": path_b, "C": path_c}


class TestInspectSourcePdf:
    def test_inspect_net_format(self, sample_pdfs):
        info = inspect_source_pdf(sample_pdfs["A"], doc_id="A")
        assert info.id == "A"
        assert info.page_count == 12
        assert abs(info.media_width_mm - 210.0) < 1.0
        assert abs(info.media_height_mm - 297.0) < 1.0
        assert info.format_name == "A4"
        assert not info.has_bleed

    def test_inspect_with_bleed(self, sample_pdfs):
        info = inspect_source_pdf(sample_pdfs["B"], doc_id="B")
        assert info.id == "B"
        assert info.page_count == 4
        assert abs(info.media_width_mm - 216.0) < 1.5
        assert abs(info.media_height_mm - 303.0) < 1.5
        assert abs(info.trim_width_mm - 210.0) < 1.0
        assert abs(info.trim_height_mm - 297.0) < 1.0
        assert info.format_name == "A4"
        assert info.has_bleed
        assert abs(info.bleed_margin_mm - 3.0) < 0.5

    def test_inspect_different_format_a3(self, sample_pdfs):
        info = inspect_source_pdf(sample_pdfs["C"], doc_id="C")
        assert info.id == "C"
        assert info.format_name == "A3"
        assert abs(info.media_width_mm - 420.0) < 1.0

    def test_inspect_missing_file_raises(self, tmp_path):
        missing = str(tmp_path / "non_existent.pdf")
        with pytest.raises(FileNotFoundError):
            inspect_source_pdf(missing)


class TestGeometryDiagnosis:
    def test_diagnose_mixed_formats_and_bleed(self, sample_pdfs):
        info_a = inspect_source_pdf(sample_pdfs["A"], "A")
        info_b = inspect_source_pdf(sample_pdfs["B"], "B")
        info_c = inspect_source_pdf(sample_pdfs["C"], "C")

        # A & B: same finished format (A4), but one has bleed and one does not
        diag_ab = diagnose_sources_geometry([info_a, info_b])
        assert diag_ab.has_mixed_bleed
        assert "al vivo" in diag_ab.warning_message.lower()

        # A & C: different formats (A4 vs A3)
        diag_ac = diagnose_sources_geometry([info_a, info_c])
        assert diag_ac.has_mixed_formats
        assert "eterogenei" in diag_ac.warning_message.lower()


class TestParsePageSequence:
    def test_empty_expression(self):
        res = parse_page_sequence("", {"A": 10})
        assert res.is_valid
        assert res.total_pages == 0

    def test_user_requested_exact_example(self):
        counts = {"A": 12, "B": 4, "C": 5}
        res = parse_page_sequence("A1-10, B2, A11-12, C1-4", counts)
        assert res.is_valid, res.error_message
        assert res.total_pages == 17
        assert res.summary_by_source == {"A": 12, "B": 1, "C": 4}

    def test_whole_document_shorthand(self):
        counts = {"A": 3, "B": 2}
        res = parse_page_sequence("A, B", counts)
        assert res.is_valid
        assert res.total_pages == 5
        assert [s.source_id for s in res.page_specs] == ["A", "A", "A", "B", "B"]

    def test_open_ended_ranges(self):
        counts = {"A": 10}
        res = parse_page_sequence("A8-", counts)
        assert res.is_valid
        assert [s.source_page_index for s in res.page_specs] == [7, 8, 9]

        res2 = parse_page_sequence("A-3", counts)
        assert res2.is_valid
        assert [s.source_page_index for s in res2.page_specs] == [0, 1, 2]

    def test_reverse_range(self):
        counts = {"A": 5}
        res = parse_page_sequence("A4-2", counts)
        assert res.is_valid
        assert [s.source_page_index for s in res.page_specs] == [3, 2, 1]

    def test_blank_pages(self):
        counts = {"A": 2}
        res = parse_page_sequence("A1, blank, A2, _", counts)
        assert res.is_valid
        assert res.total_pages == 4
        assert res.page_specs[1].is_blank

    def test_missing_document_error(self):
        counts = {"A": 5}
        res = parse_page_sequence("A1-3, Z1", counts)
        assert not res.is_valid
        assert "non presente" in res.error_message

    def test_page_out_of_bounds_error(self):
        counts = {"A": 5}
        res = parse_page_sequence("A6", counts)
        assert not res.is_valid
        assert "ha 5 pagine" in res.error_message


class TestComposePdfGeometry:
    def test_compose_preserve_mode(self, sample_pdfs):
        # In preserve mode, Doc B keeps its bleed and Doc C keeps A3
        parsed = parse_page_sequence("A1, B1, C1", {"A": 12, "B": 4, "C": 5})
        doc = compose_pdf(sample_pdfs, parsed.page_specs, geometry_config=CompositionGeometryConfig(ComposerIntent.PRESERVE))
        try:
            assert len(doc) == 3
            # Page 0 (A1) = A4
            assert abs(doc[0].rect.width - 595.28) < 1.0
            # Page 1 (B1) = A4 + bleed (~612 x 858 pt)
            assert doc[1].rect.width > 600
            # Page 2 (C1) = A3 (~1190 pt)
            assert abs(doc[2].rect.width - 1190.55) < 1.0
        finally:
            doc.close()

    def test_compose_clean_view_mode(self, sample_pdfs):
        # In VIEW mode, Doc B is cropped to its finished TrimBox (A4 ~595.28 x 841.89)
        # while Doc C keeps A3 clean format
        parsed = parse_page_sequence("A1, B1, C1", {"A": 12, "B": 4, "C": 5})
        doc = compose_pdf(sample_pdfs, parsed.page_specs, geometry_config=CompositionGeometryConfig(ComposerIntent.VIEW))
        try:
            assert len(doc) == 3
            # Page 0 (Doc A) is already A4
            assert abs(doc[0].rect.width - 595.28) < 1.0

            # Page 1 (Doc B) was cropped from 216x303 to exact A4 TrimBox (595.28 x 841.89)
            assert abs(doc[1].rect.width - 595.28) < 2.0
            assert abs(doc[1].rect.height - 841.89) < 2.0

            # Page 2 (Doc C) keeps original A3 size
            assert abs(doc[2].rect.width - 1190.55) < 1.0
        finally:
            doc.close()

    def test_compose_prepress_print_mode(self, sample_pdfs):
        # In PRINT mode, Doc A (net A4 without bleed) gets expanded sheet with TrimBox and BleedBox
        # and Doc B (which already has bleed) maintains TrimBox and gets BleedBox calibrated
        parsed = parse_page_sequence("A1, B1", {"A": 12, "B": 4})
        doc = compose_pdf(sample_pdfs, parsed.page_specs, geometry_config=CompositionGeometryConfig(ComposerIntent.PRINT, bleed_mm=3.0))
        try:
            assert len(doc) == 2
            # Doc A now has expanded MediaBox and distinct TrimBox
            p0 = doc[0]
            assert p0.trimbox and not p0.trimbox.is_empty
            assert abs(p0.trimbox.width - 595.28) < 1.0
            assert p0.rect.width > p0.trimbox.width  # expanded with bleed
            assert p0.bleedbox and not p0.bleedbox.is_empty

            # Doc B has valid TrimBox and BleedBox
            p1 = doc[1]
            assert p1.trimbox and not p1.trimbox.is_empty
            assert abs(p1.trimbox.width - 595.28) < 2.0
            assert p1.bleedbox and not p1.bleedbox.is_empty
        finally:
            doc.close()

    def test_compose_scale_to_common_format(self, sample_pdfs):
        # Scale all pages to A4 (including A3 from Doc C)
        parsed = parse_page_sequence("A1, C1", {"A": 12, "C": 5})
        cfg = CompositionGeometryConfig(
            intent=ComposerIntent.VIEW,
            scale_to_common_format=True,
            common_format_name="A4",
        )
        doc = compose_pdf(sample_pdfs, parsed.page_specs, geometry_config=cfg)
        try:
            assert len(doc) == 2
            # Both pages should now be A4 (landscape or portrait)
            for page in doc:
                dims = (min(page.rect.width, page.rect.height), max(page.rect.width, page.rect.height))
                assert abs(dims[0] - 595.28) < 2.0
                assert abs(dims[1] - 841.89) < 2.0
        finally:
            doc.close()
