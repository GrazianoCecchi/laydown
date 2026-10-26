"""
Tests for PDF Inspector engine (laydown.engine.inspector).
"""

import sys
import fitz
import pytest

from laydown.engine.inspector import (
    clean_pdf_date,
    format_file_size,
    inspect_document,
    inspect_page,
)


def test_no_qt_imported():
    """Verify that laydown.engine.inspector does not import Qt."""
    for mod in list(sys.modules.keys()):
        if "inspector" in mod and "laydown.engine" in mod:
            with open(sys.modules[mod].__file__, "r", encoding="utf-8") as f:
                code = f.read()
            assert "import PyQt" not in code
            assert "from PyQt" not in code
            assert "import PySide" not in code
            assert "from PySide" not in code
            assert "import Qt" not in code
            assert "from Qt" not in code


def test_format_file_size():
    assert format_file_size(0) == "0 B"
    assert format_file_size(500) == "500 B"
    assert format_file_size(1024) == "1.00 KB"
    assert format_file_size(1536) == "1.50 KB"
    assert format_file_size(1048576) == "1.00 MB"
    assert format_file_size(2621440) == "2.50 MB"


def test_clean_pdf_date():
    assert clean_pdf_date("D:20261012143000+02'00'") == "2026-10-12 14:30:00"
    assert clean_pdf_date("D:20261012") == "2026-10-12"
    assert clean_pdf_date("2026-10-12") == "2026-10-12"
    assert clean_pdf_date("") == ""


def test_inspect_document_plain_pdf(tmp_path):
    pdf_path = str(tmp_path / "plain.pdf")
    doc = fitz.open()
    doc.new_page(width=200, height=300)
    doc.new_page(width=400, height=600)
    doc.save(pdf_path)
    doc.close()

    doc = fitz.open(pdf_path)
    insp = inspect_document(doc, pdf_path)
    doc.close()

    assert insp.page_count == 2
    assert insp.file_name == "plain.pdf"
    assert insp.file_size_bytes > 0
    assert "KB" in insp.file_size_formatted or "B" in insp.file_size_formatted
    assert "PDF" in insp.pdf_version

    p1 = insp.pages[0]
    assert p1.page_number == 1
    assert p1.rotation == 0
    assert p1.width_pt == 200.0
    assert p1.height_pt == 300.0
    assert p1.mediabox.width_pt == 200.0
    assert p1.mediabox.height_pt == 300.0
    assert p1.trimbox.width_pt == 200.0
    # On a plain PDF, TrimBox is not explicitly declared
    assert p1.trimbox.is_explicit is False


def test_inspect_document_with_explicit_boxes(tmp_path):
    pdf_path = str(tmp_path / "boxed.pdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.set_bleedbox(fitz.Rect(10, 10, 290, 390))
    page.set_trimbox(fitz.Rect(20, 20, 280, 380))
    doc.set_metadata({
        "title": "Documento di Prova",
        "author": "Antigravity Team",
        "creator": "Laydown Engine",
    })
    doc.save(pdf_path)
    doc.close()

    doc = fitz.open(pdf_path)
    insp = inspect_document(doc, pdf_path)
    doc.close()

    assert insp.title == "Documento di Prova"
    assert insp.author == "Antigravity Team"
    assert insp.creator == "Laydown Engine"

    p = insp.pages[0]
    assert p.mediabox.width_pt == 300.0
    assert p.mediabox.height_pt == 400.0

    assert p.bleedbox.is_explicit is True
    assert p.bleedbox.width_pt == 280.0
    assert p.bleedbox.height_pt == 380.0

    assert p.trimbox.is_explicit is True
    assert p.trimbox.width_pt == 260.0
    assert p.trimbox.height_pt == 360.0


def test_inspect_rotated_page(tmp_path):
    pdf_path = str(tmp_path / "rotated.pdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=500)
    page.set_trimbox(fitz.Rect(10, 20, 290, 480))
    page.set_rotation(90)
    doc.save(pdf_path)
    doc.close()

    doc = fitz.open(pdf_path)
    insp = inspect_document(doc, pdf_path)
    doc.close()

    p = insp.pages[0]
    assert p.rotation == 90
    # In visual space rotated by 90°, width and height swap
    assert p.width_pt == 500.0
    assert p.height_pt == 300.0
    assert p.trimbox.is_explicit is True
    assert p.trimbox.width_pt == 460.0
    assert p.trimbox.height_pt == 280.0
