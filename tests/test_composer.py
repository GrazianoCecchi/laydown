"""
Tests for the PDF Composer and Merger engine (laydown.engine.composer).
"""

import os
import pytest
import fitz

from laydown.engine.composer import (
    PageSpec,
    ParseResult,
    SourceDocInfo,
    compose_pdf,
    inspect_source_pdf,
    parse_page_sequence,
)


@pytest.fixture
def sample_pdfs(tmp_path):
    """Create three sample PDFs with different page counts and distinct text content."""
    # Doc A: 12 pages (A4)
    path_a = str(tmp_path / "doc_a.pdf")
    doc_a = fitz.open()
    for i in range(12):
        page = doc_a.new_page(width=595.28, height=841.89)  # A4
        page.insert_text((50, 100), f"Doc A - Page {i + 1}")
    doc_a.save(path_a)
    doc_a.close()

    # Doc B: 4 pages (A5 landscape: 595.28 x 419.53)
    path_b = str(tmp_path / "doc_b.pdf")
    doc_b = fitz.open()
    for i in range(4):
        page = doc_b.new_page(width=595.28, height=419.53)
        page.insert_text((50, 100), f"Doc B - Page {i + 1}")
    doc_b.save(path_b)
    doc_b.close()

    # Doc C: 5 pages (Square: 400 x 400)
    path_c = str(tmp_path / "doc_c.pdf")
    doc_c = fitz.open()
    for i in range(5):
        page = doc_c.new_page(width=400, height=400)
        page.insert_text((50, 100), f"Doc C - Page {i + 1}")
    doc_c.save(path_c)
    doc_c.close()

    return {"A": path_a, "B": path_b, "C": path_c}


class TestInspectSourcePdf:
    def test_inspect_valid_pdf(self, sample_pdfs):
        info = inspect_source_pdf(sample_pdfs["A"], doc_id="A")
        assert info.id == "A"
        assert info.page_count == 12
        assert abs(info.first_page_width_mm - 210.0) < 1.0
        assert abs(info.first_page_height_mm - 297.0) < 1.0
        assert info.filename == "doc_a.pdf"

    def test_inspect_missing_file_raises(self, tmp_path):
        missing = str(tmp_path / "non_existent.pdf")
        with pytest.raises(FileNotFoundError):
            inspect_source_pdf(missing)


class TestParsePageSequence:
    def test_empty_expression(self):
        res = parse_page_sequence("", {"A": 10})
        assert res.is_valid
        assert res.total_pages == 0

    def test_user_requested_exact_example(self):
        # "A1-10, B2, A11-12, C1-4"
        counts = {"A": 12, "B": 4, "C": 5}
        res = parse_page_sequence("A1-10, B2, A11-12, C1-4", counts)
        assert res.is_valid, res.error_message
        assert res.total_pages == 17
        assert res.summary_by_source == {"A": 12, "B": 1, "C": 4}

        # Check pages:
        # A1-10 -> pages 0..9
        for i in range(10):
            assert res.page_specs[i] == PageSpec("A", i)
        # B2 -> page 1 of B
        assert res.page_specs[10] == PageSpec("B", 1)
        # A11-12 -> pages 10, 11 of A
        assert res.page_specs[11] == PageSpec("A", 10)
        assert res.page_specs[12] == PageSpec("A", 11)
        # C1-4 -> pages 0..3 of C
        for i in range(4):
            assert res.page_specs[13 + i] == PageSpec("C", i)

    def test_whole_document_shorthand(self):
        counts = {"A": 3, "B": 2}
        res = parse_page_sequence("A, B", counts)
        assert res.is_valid
        assert res.total_pages == 5
        assert [s.source_id for s in res.page_specs] == ["A", "A", "A", "B", "B"]
        assert [s.source_page_index for s in res.page_specs] == [0, 1, 2, 0, 1]

    def test_open_ended_ranges(self):
        counts = {"A": 10}
        # A8- -> 8, 9, 10 (indices 7, 8, 9)
        res = parse_page_sequence("A8-", counts)
        assert res.is_valid
        assert [s.source_page_index for s in res.page_specs] == [7, 8, 9]

        # A-3 -> 1, 2, 3 (indices 0, 1, 2)
        res2 = parse_page_sequence("A-3", counts)
        assert res2.is_valid
        assert [s.source_page_index for s in res2.page_specs] == [0, 1, 2]

    def test_reverse_range(self):
        counts = {"A": 5}
        # A4-2 -> 4, 3, 2 (indices 3, 2, 1)
        res = parse_page_sequence("A4-2", counts)
        assert res.is_valid
        assert [s.source_page_index for s in res.page_specs] == [3, 2, 1]

    def test_blank_pages(self):
        counts = {"A": 2}
        res = parse_page_sequence("A1, blank, A2, _", counts)
        assert res.is_valid
        assert res.total_pages == 4
        assert res.page_specs[0] == PageSpec("A", 0)
        assert res.page_specs[1].is_blank
        assert res.page_specs[2] == PageSpec("A", 1)
        assert res.page_specs[3].is_blank

    def test_case_insensitivity_and_separators(self):
        counts = {"A": 4, "B": 3}
        res = parse_page_sequence("a1-2; b1 b3, a4", counts)
        assert res.is_valid
        assert [s.source_id for s in res.page_specs] == ["A", "A", "B", "B", "A"]
        assert [s.source_page_index for s in res.page_specs] == [0, 1, 0, 2, 3]

    def test_missing_document_error(self):
        counts = {"A": 5}
        res = parse_page_sequence("A1-3, Z1", counts)
        assert not res.is_valid
        assert "non presente" in res.error_message
        assert "'Z'" in res.error_message

    def test_page_out_of_bounds_error(self):
        counts = {"A": 5}
        res = parse_page_sequence("A6", counts)
        assert not res.is_valid
        assert "ha 5 pagine" in res.error_message
        assert "6" in res.error_message

    def test_page_zero_error(self):
        counts = {"A": 5}
        res = parse_page_sequence("A0-3", counts)
        assert not res.is_valid
        assert ">= 1" in res.error_message

    def test_malformed_syntax_error(self):
        counts = {"A": 5}
        res = parse_page_sequence("A??", counts)
        assert not res.is_valid


class TestComposePdf:
    def test_compose_pdf_content_and_geometry(self, sample_pdfs, tmp_path):
        out_path = str(tmp_path / "combined.pdf")
        # Let's compose: A1, B1, blank, C1
        counts = {"A": 12, "B": 4, "C": 5}
        parsed = parse_page_sequence("A1, B1, blank, C1", counts)
        assert parsed.is_valid

        doc = compose_pdf(sample_pdfs, parsed.page_specs, output_path=out_path)
        try:
            assert len(doc) == 4
            # Verify page 0 is from Doc A (A4)
            p0 = doc[0]
            assert "Doc A - Page 1" in p0.get_text()
            assert abs(p0.rect.width - 595.28) < 1.0

            # Verify page 1 is from Doc B (A5 landscape)
            p1 = doc[1]
            assert "Doc B - Page 1" in p1.get_text()
            assert abs(p1.rect.height - 419.53) < 1.0

            # Verify page 2 is blank (no text)
            p2 = doc[2]
            assert p2.get_text().strip() == ""

            # Verify page 3 is from Doc C (400x400)
            p3 = doc[3]
            assert "Doc C - Page 1" in p3.get_text()
            assert abs(p3.rect.width - 400.0) < 1.0

            # Verify saved file exists and can be re-opened
            assert os.path.exists(out_path)
            doc_saved = fitz.open(out_path)
            assert len(doc_saved) == 4
            doc_saved.close()
        finally:
            doc.close()

    def test_compose_empty_specs_raises(self, sample_pdfs):
        with pytest.raises(ValueError):
            compose_pdf(sample_pdfs, [])

    def test_compose_invalid_source_type_raises(self):
        with pytest.raises(TypeError):
            compose_pdf({"A": 12345}, [PageSpec("A", 0)])

    def test_compose_missing_key_in_sources_raises(self, sample_pdfs):
        with pytest.raises(KeyError):
            compose_pdf(sample_pdfs, [PageSpec("Z", 0)])

    def test_compose_index_out_of_bounds_raises(self, sample_pdfs):
        with pytest.raises(IndexError):
            compose_pdf(sample_pdfs, [PageSpec("A", 999)])
