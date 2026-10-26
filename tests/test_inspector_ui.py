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
