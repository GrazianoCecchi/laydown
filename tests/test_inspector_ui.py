"""
Tests for PDF Inspector UI and its MainWindow integration.
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PyQt6.QtWidgets import QApplication

from laydown.ui.inspector_dialog import PdfInspectorDialog
from laydown.ui.main_window import MainWindow
from tests.helpers import make_source_pdf


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_inspector_action_and_button_lifecycle(app, tmp_path):
    pdf_path = make_source_pdf(str(tmp_path / "test_doc.pdf"), n_pages=3)
    win = MainWindow()

    # Before opening: inspect action and button are disabled
    assert not win._act_inspect.isEnabled()
    assert not win._btn_inspect.isEnabled()

    # Load PDF: inspect action and button become enabled
    win.load_pdf(pdf_path)
    assert win._act_inspect.isEnabled()
    assert win._btn_inspect.isEnabled()

    # Close PDF: inspect action and button become disabled
    win._on_close_pdf()
    assert not win._act_inspect.isEnabled()
    assert not win._btn_inspect.isEnabled()
    win.close()


def test_inspector_dialog_rendering_and_interaction(app, tmp_path):
    doc_path = str(tmp_path / "boxes_doc.pdf")
    doc = fitz.open()
    p1 = doc.new_page(width=300, height=400)
    p1.set_cropbox(fitz.Rect(10, 10, 290, 390))
    p1.set_bleedbox(fitz.Rect(5, 5, 295, 395))
    p1.set_trimbox(fitz.Rect(15, 15, 285, 385))

    p2 = doc.new_page(width=200, height=300)
    doc.save(doc_path)

    # Reopen to test inspector dialog
    source_doc = fitz.open(doc_path)
    dialog = PdfInspectorDialog(source_doc, doc_path)

    # Check page count and initial selection
    assert dialog._page_spin.maximum() == 2
    assert dialog._page_spin.value() == 1
    assert dialog._canvas._page_info is not None
    assert dialog._canvas._page_info.page_number == 1

    # Check table rows (5 boxes)
    assert dialog._boxes_table.rowCount() == 5

    # Navigate to next page
    dialog._on_next_page()
    assert dialog._page_spin.value() == 2
    assert dialog._canvas._page_info.page_number == 2

    # Toggle box visibility checkbox
    cb_trim = dialog._box_checks.get("TrimBox")
    assert cb_trim is not None
    assert dialog._canvas._active_boxes.get("TrimBox") is True
    cb_trim.setChecked(False)
    assert dialog._canvas._active_boxes.get("TrimBox") is False

    # Verify tooltips on checkboxes and table items
    assert "TrimBox" in cb_trim.toolTip()
    assert "taglio" in cb_trim.toolTip().lower() or "rifilo" in cb_trim.toolTip().lower()

    item_0 = dialog._boxes_table.item(0, 0)
    assert item_0 is not None
    assert "TrimBox" in item_0.toolTip()
    assert "Dimensioni:" in item_0.toolTip()

    # Table row selection highlights box on canvas
    dialog._boxes_table.selectRow(0)
    assert dialog._canvas._highlighted_box == "TrimBox"

    # Test canvas hover / mouse event handling
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    ev_move = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(50.0, 50.0),
        QPointF(50.0, 50.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    dialog._canvas.mouseMoveEvent(ev_move)
    # Leave event clears highlight
    from PyQt6.QtCore import QEvent
    ev_leave = QEvent(QEvent.Type.Leave)
    dialog._canvas.leaveEvent(ev_leave)
    assert dialog._canvas._highlighted_box is None

    # Test zoom triggers without error
    dialog._canvas.zoom_in()
    dialog._canvas.zoom_out()
    dialog._canvas.reset_zoom()

    # Verify pixmap is rendered
    assert dialog._canvas._pixmap is not None
    assert not dialog._canvas._pixmap.isNull()

    dialog.close()
    source_doc.close()


def test_inspector_color_tab_integration(app, tmp_path):
    doc_path = str(tmp_path / "color_doc.pdf")
    doc = fitz.open()
    p1 = doc.new_page(width=300, height=400)
    # Add CMYK shape on page 1
    s1 = p1.new_shape()
    s1.draw_rect(fitz.Rect(10, 10, 50, 50))
    s1.finish(fill=(0.2, 0.4, 0.6, 0.1))
    s1.commit()

    # Add OutputIntent
    cat = doc.pdf_catalog()
    oi_xref = doc.get_new_xref()
    doc.update_object(oi_xref, """<<
      /Type /OutputIntent
      /S /GTS_PDFX
      /OutputConditionIdentifier (FOGRA39)
      /Info (Coated FOGRA39)
    >>""")
    doc.xref_set_key(cat, "OutputIntents", f"[{oi_xref} 0 R]")

    # Page 2: RGB line
    p2 = doc.new_page(width=300, height=400)
    s2 = p2.new_shape()
    s2.draw_line(fitz.Point(10, 10), fitz.Point(100, 100))
    s2.finish(color=(1.0, 0, 0))
    s2.commit()

    doc.save(doc_path)
    doc.close()

    source_doc = fitz.open(doc_path)
    dialog = PdfInspectorDialog(source_doc, doc_path)

    # Check tab widget
    assert hasattr(dialog, "_tabs_widget")
    assert dialog._tabs_widget.count() == 3
    assert dialog._tabs_widget.tabText(0) == "Riquadri"
    assert dialog._tabs_widget.tabText(1) == "Colore"
    assert dialog._tabs_widget.tabText(2) == "Metadati"

    # Switch to Color tab
    dialog._tabs_widget.setCurrentIndex(1)
    assert dialog._tabs_widget.currentIndex() == 1

    # Check page 1 stats
    assert "CMYK" in dialog._lbl_pg_summary.text()

    # Switch to page 2 (index 1)
    dialog._on_next_page()
    assert dialog._current_page_idx == 1
    assert "RGB" in dialog._lbl_pg_summary.text()

    dialog.close()
    source_doc.close()

