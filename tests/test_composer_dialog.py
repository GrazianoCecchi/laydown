"""
Tests for ComposerDialog (laydown.ui.composer_dialog).
"""

import os
import pytest
import fitz

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QApplication

from laydown.engine.composer import ComposerIntent
from laydown.ui.composer_dialog import ComposerDialog, _letter_id


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sample_dialog_pdfs(tmp_path):
    path_a = str(tmp_path / "sample_a.pdf")
    doc_a = fitz.open()
    for _ in range(5):
        doc_a.new_page(width=595.28, height=841.89)  # A4
    doc_a.save(path_a)
    doc_a.close()

    path_b = str(tmp_path / "sample_b.pdf")
    doc_b = fitz.open()
    for _ in range(3):
        doc_b.new_page(width=400, height=400)  # Square custom
    doc_b.save(path_b)
    doc_b.close()

    return path_a, path_b


class TestComposerDialog:
    def test_letter_id_generation(self):
        assert _letter_id(0) == "A"
        assert _letter_id(1) == "B"
        assert _letter_id(25) == "Z"
        assert _letter_id(26) == "AA"
        assert _letter_id(27) == "AB"

    def test_dialog_init_empty(self, app):
        dialog = ComposerDialog()
        assert len(dialog._sources) == 0
        assert not dialog._btn_save_as.isEnabled()
        assert not dialog._btn_open_in_laydown.isEnabled()
        assert not dialog._btn_remove.isEnabled()
        assert not dialog._btn_clear.isEnabled()
        assert dialog._radio_view.isChecked()
        assert not dialog._bleed_frame.isEnabled()
        dialog.close()

    def test_dialog_init_with_initial_pdf(self, app, sample_dialog_pdfs):
        path_a, _ = sample_dialog_pdfs
        dialog = ComposerDialog(initial_pdf_path=path_a)
        assert len(dialog._sources) == 1
        assert dialog._sources[0].id == "A"
        assert dialog._sources[0].page_count == 5
        assert dialog._src_table.rowCount() == 1
        assert dialog._seq_input.text() == "A"
        assert dialog._btn_save_as.isEnabled()
        assert dialog._btn_open_in_laydown.isEnabled()
        dialog.close()

    def test_add_multiple_sources_and_reorder(self, app, sample_dialog_pdfs):
        path_a, path_b = sample_dialog_pdfs
        dialog = ComposerDialog()
        dialog._add_source_files([path_a, path_b])

        assert len(dialog._sources) == 2
        assert dialog._sources[0].id == "A"
        assert dialog._sources[0].filename == "sample_a.pdf"
        assert dialog._sources[1].id == "B"
        assert dialog._sources[1].filename == "sample_b.pdf"

        # Diagnosis frame should be visible due to mixed formats (A4 vs custom square)
        assert not dialog._diag_frame.isHidden()
        assert "eterogenei" in dialog._diag_label.text().lower()

        # Select row 1 (B) and move up
        dialog._src_table.selectRow(1)
        assert dialog._btn_up.isEnabled()
        dialog._on_move_up()

        # Now B should be A, and A should be B
        assert dialog._sources[0].id == "A"
        assert dialog._sources[0].filename == "sample_b.pdf"
        assert dialog._sources[1].id == "B"
        assert dialog._sources[1].filename == "sample_a.pdf"

        # Select row 0 (now sample_b) and move down
        dialog._src_table.selectRow(0)
        assert dialog._btn_down.isEnabled()
        dialog._on_move_down()
        assert dialog._sources[0].filename == "sample_a.pdf"
        assert dialog._sources[1].filename == "sample_b.pdf"

        dialog.close()

    def test_geometry_controls_and_config(self, app):
        dialog = ComposerDialog()

        # Default is VIEW mode
        cfg = dialog._get_geometry_config()
        assert cfg.intent == ComposerIntent.VIEW
        assert not cfg.scale_to_common_format

        # Toggle to PRINT mode -> enables bleed frame
        dialog._radio_print.setChecked(True)
        assert dialog._bleed_frame.isEnabled()
        dialog._spin_bleed.setValue(4.5)
        cfg_print = dialog._get_geometry_config()
        assert cfg_print.intent == ComposerIntent.PRINT
        assert cfg_print.bleed_mm == 4.5

        # Toggle scale to common format
        dialog._chk_scale_common.setChecked(True)
        assert dialog._combo_common_fmt.isEnabled()
        dialog._combo_common_fmt.setCurrentText("A3")
        cfg_scale = dialog._get_geometry_config()
        assert cfg_scale.scale_to_common_format
        assert cfg_scale.common_format_name == "A3"

        dialog.close()

    def test_sequence_validation_ui(self, app, sample_dialog_pdfs):
        path_a, path_b = sample_dialog_pdfs
        dialog = ComposerDialog(initial_pdf_path=path_a)
        dialog._add_source_files([path_b])

        dialog._seq_input.setText("A1-3, B2, blank, A5")
        assert dialog._btn_save_as.isEnabled()
        assert dialog._btn_open_in_laydown.isEnabled()
        assert "6 pagine generate" in dialog._status_label.text()

        dialog._seq_input.setText("A1-3, Z1")
        assert not dialog._btn_save_as.isEnabled()
        assert "non presente" in dialog._status_label.text()

        dialog._seq_input.setText("B4")
        assert not dialog._btn_save_as.isEnabled()
        assert "ha 3 pagine" in dialog._status_label.text()

        dialog._seq_input.setText("")
        assert not dialog._btn_save_as.isEnabled()

        dialog.close()

    def test_preview_table_toggle_and_content(self, app, sample_dialog_pdfs):
        path_a, _ = sample_dialog_pdfs
        dialog = ComposerDialog(initial_pdf_path=path_a)
        dialog._seq_input.setText("A1, blank, A3")

        assert dialog._preview_table.isHidden()
        dialog._btn_toggle_preview.setChecked(True)
        assert not dialog._preview_table.isHidden()
        assert dialog._preview_table.rowCount() == 3

        # Row 0: Pagina 1 | [A] sample_a.pdf | pag. 1
        assert dialog._preview_table.item(0, 0).text() == "Pagina 1"
        assert "[A]" in dialog._preview_table.item(0, 1).text()
        assert dialog._preview_table.item(0, 2).text() == "pag. 1"

        dialog.close()

    def test_quick_action_buttons(self, app, sample_dialog_pdfs):
        path_a, path_b = sample_dialog_pdfs
        dialog = ComposerDialog()
        dialog._add_source_files([path_a, path_b])

        dialog._seq_input.setText("")
        dialog._on_set_all_in_order()
        assert dialog._seq_input.text() == "A, B"

        dialog._on_add_blank_token()
        assert dialog._seq_input.text() == "A, B, blank"
        dialog.close()

    def test_open_in_laydown_action(self, app, sample_dialog_pdfs):
        path_a, path_b = sample_dialog_pdfs
        dialog = ComposerDialog(initial_pdf_path=path_a)
        dialog._add_source_files([path_b])
        dialog._seq_input.setText("A1-2, B1")

        dialog._on_open_in_laydown()
        assert dialog.result() == 1  # Accepted
        assert dialog.open_in_laydown_requested is True
        assert dialog.composed_pdf_path is not None
        assert os.path.isfile(dialog.composed_pdf_path)

        doc = fitz.open(dialog.composed_pdf_path)
        assert len(doc) == 3
        doc.close()

        try:
            os.remove(dialog.composed_pdf_path)
        except Exception:
            pass
        dialog.close()

    def test_main_window_compose_action_wired(self, app):
        from laydown.ui.main_window import MainWindow
        win = MainWindow()
        assert hasattr(win, "_act_compose")
        assert hasattr(win, "_btn_compose")
        assert win._act_compose.text() == "&Componi e unisci PDF…"
        assert win._act_compose.shortcut().toString() in ("Ctrl+Shift+M", "Ctrl+M")
        win.close()
