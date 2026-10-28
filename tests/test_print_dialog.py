"""
Unit tests for PrintDialog, helpers, and print worker.
"""

import os
import tempfile
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtPrintSupport import QPrinter

from laydown.engine.data_model import Project
from laydown.ui import win_print
from laydown.ui.print_dialog import (
    parse_page_range,
    compute_sheet_placement,
    find_best_matching_paper_size,
    PrintDialog,
    _PrintWorker,
)
from PyQt6.QtGui import QPageSize


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sample_pdf():
    """Create a minimal 3-page PDF for testing."""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595.28, height=841.89)  # A4
        page.draw_rect([50, 50, 200, 200], fill=(0.1 * i, 0.5, 0.8))
        page.insert_text((100, 100), f"Sheet {i + 1}")
    doc.save(path)
    doc.close()
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def sample_project(sample_pdf):
    return Project(source_pdf_path=sample_pdf)


# ── Range Parsing Tests ────────────────────────────────────

class TestParsePageRange:
    def test_empty_or_whitespace_returns_all(self):
        assert parse_page_range("", 5) == [0, 1, 2, 3, 4]
        assert parse_page_range("   ", 3) == [0, 1, 2]

    def test_single_page(self):
        assert parse_page_range("1", 4) == [0]
        assert parse_page_range("3", 4) == [2]

    def test_range(self):
        assert parse_page_range("1-3", 5) == [0, 1, 2]
        assert parse_page_range("2-4", 5) == [1, 2, 3]

    def test_reversed_range_normalizes(self):
        assert parse_page_range("3-1", 5) == [0, 1, 2]

    def test_mixed_ranges_and_singles_deduplicated(self):
        assert parse_page_range("1, 3-4, 2, 3", 6) == [0, 1, 2, 3]

    def test_out_of_bounds_filtered_out(self):
        # 10 is ignored since total is 4
        assert parse_page_range("1, 10", 4) == [0]

    def test_all_out_of_bounds_raises_value_error(self):
        with pytest.raises(ValueError, match="Nessun foglio valido"):
            parse_page_range("10-20", 4)

    def test_invalid_syntax_raises_value_error(self):
        with pytest.raises(ValueError, match="Valore numerico non valido"):
            parse_page_range("foo", 4)
        with pytest.raises(ValueError, match="Intervallo non valido"):
            parse_page_range("1-2-3", 4)


# ── Paper Matching Tests ───────────────────────────────────

class TestFindBestMatchingPaperSize:
    def test_exact_a3_match(self):
        sizes = [QPageSize(QPageSize.PageSizeId.A4), QPageSize(QPageSize.PageSizeId.A3), QPageSize(QPageSize.PageSizeId.Letter)]
        idx = find_best_matching_paper_size(sizes, 420.0, 297.0, preset="A3")
        assert idx == 1
        assert sizes[idx].id() == QPageSize.PageSizeId.A3

    def test_a3_sheet_on_a4_only_printer_prefers_a4_over_legal(self):
        # A4 printer with Letter, Legal, A4, Executive
        sizes = [
            QPageSize(QPageSize.PageSizeId.Letter),
            QPageSize(QPageSize.PageSizeId.Legal),
            QPageSize(QPageSize.PageSizeId.A4),
        ]
        # For A3 sheet (420 x 297 mm), Legal (356 x 216) must NOT be chosen over A4
        idx = find_best_matching_paper_size(sizes, 420.0, 297.0, preset="A3")
        assert sizes[idx].id() == QPageSize.PageSizeId.A4

    def test_exact_dimension_match(self):
        sizes = [QPageSize(QPageSize.PageSizeId.Letter), QPageSize(QPageSize.PageSizeId.A4)]
        idx = find_best_matching_paper_size(sizes, 210.0, 297.0, preset="Custom")
        assert sizes[idx].id() == QPageSize.PageSizeId.A4


# ── Sheet Placement Geometry Tests ─────────────────────────

class TestComputeSheetPlacement:
    def test_actual_size_100_percent_centered(self):
        target_w, target_h, off_x, off_y = compute_sheet_placement(
            paper_w_pt=1000.0,
            paper_h_pt=800.0,
            sheet_w_pt=600.0,
            sheet_h_pt=400.0,
            scaling_mode="actual",
            center=True,
        )
        assert target_w == 600.0
        assert target_h == 400.0
        assert off_x == (1000.0 - 600.0) / 2.0  # 200.0
        assert off_y == (800.0 - 400.0) / 2.0   # 200.0

    def test_actual_size_not_centered(self):
        _, _, off_x, off_y = compute_sheet_placement(
            paper_w_pt=1000.0,
            paper_h_pt=800.0,
            sheet_w_pt=600.0,
            sheet_h_pt=400.0,
            scaling_mode="actual",
            center=False,
        )
        assert off_x == 0.0
        assert off_y == 0.0

    def test_fit_to_page_scales_proportionally(self):
        # Paper 500x500, sheet 1000x500 -> scale should be 0.5
        target_w, target_h, off_x, off_y = compute_sheet_placement(
            paper_w_pt=500.0,
            paper_h_pt=500.0,
            sheet_w_pt=1000.0,
            sheet_h_pt=500.0,
            scaling_mode="fit",
            center=True,
        )
        assert target_w == 500.0
        assert target_h == 250.0
        assert off_x == 0.0
        assert off_y == 125.0

    def test_shrink_oversized_keeps_100_percent_when_fits(self):
        target_w, target_h, _, _ = compute_sheet_placement(
            paper_w_pt=1000.0,
            paper_h_pt=1000.0,
            sheet_w_pt=400.0,
            sheet_h_pt=300.0,
            scaling_mode="shrink",
            center=True,
        )
        assert target_w == 400.0
        assert target_h == 300.0

    def test_shrink_oversized_scales_down_when_exceeds(self):
        target_w, target_h, _, _ = compute_sheet_placement(
            paper_w_pt=500.0,
            paper_h_pt=500.0,
            sheet_w_pt=1000.0,
            sheet_h_pt=500.0,
            scaling_mode="shrink",
            center=True,
        )
        assert target_w == 500.0
        assert target_h == 250.0


# ── PrintDialog Lifecycle & UI Tests ───────────────────────

class TestPrintDialogUI:
    def test_dialog_instantiation(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            assert dlg.windowTitle() == "Stampa — Laydown"
            assert dlg._total_sheets == 3
            assert dlg._combo_scale.count() >= 3
            assert dlg._combo_duplex.count() >= 3
            assert dlg._combo_dpi.count() >= 3
            assert dlg._btn_print is not None
            assert dlg._preview is not None
        finally:
            dlg.close_document()
            dlg.close()

    def test_preview_paint_event(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            # Trigger paint requested explicitly
            printer = QPrinter()
            printer.setFullPage(True)
            dlg._on_paint_requested(printer)
            # Should not raise any exceptions
        finally:
            dlg.close_document()
            dlg.close()

    def test_range_selection_changes(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            # Select "Foglio corrente"
            dlg._radio_current.setChecked(True)
            assert dlg._current_sheet_indices == [0]

            # Select "Intervallo personalizzato"
            dlg._radio_custom.setChecked(True)
            dlg._range_edit.setText("2-3")
            assert dlg._current_sheet_indices == [1, 2]

            # Invalid range disables print button
            dlg._range_edit.setText("invalid")
            assert not dlg._btn_print.isEnabled()

            # Fix range
            dlg._range_edit.setText("1")
            assert dlg._current_sheet_indices == [0]
            if dlg._available_printers:
                assert dlg._btn_print.isEnabled()
        finally:
            dlg.close_document()
            dlg.close()

    def test_orientation_and_duplex_options(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            dlg._combo_orient.setCurrentIndex(1)  # Landscape
            dlg._on_orientation_changed()

            dlg._combo_duplex.setCurrentIndex(0)  # Simplex
            dlg._on_duplex_changed(0)
            assert dlg._printer.duplex() == QPrinter.DuplexMode.DuplexNone
        finally:
            dlg.close_document()
            dlg.close()

    def test_view_mode_toggle(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            dlg._set_facing_pages_view()
            assert dlg._btn_facing_view.isChecked()
            assert not dlg._btn_single_view.isChecked()

            dlg._set_single_page_view()
            assert dlg._btn_single_view.isChecked()
            assert not dlg._btn_facing_view.isChecked()
        finally:
            dlg.close_document()
            dlg.close()

    def test_draft_and_production_buttons_and_dialog_stays_open(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            assert hasattr(dlg, "_btn_print_draft")
            assert hasattr(dlg, "_btn_print")
            assert "Bozza" in dlg._btn_print_draft.text()
            assert "Alta Definizione" in dlg._btn_print.text()

            # Simulate completion of worker
            dlg._last_was_draft = True
            dlg._on_worker_finished()

            # Dialog must stay open (isVisible/not rejected or accepted)
            assert dlg._btn_cancel.text() == "Chiudi"
            assert "✅" in dlg._status_lbl.text()
            assert "Bozza veloce" in dlg._status_lbl.text()
            assert dlg._btn_print.isEnabled()
            assert dlg._btn_print_draft.isEnabled()
        finally:
            dlg.close_document()
            dlg.close()

    def test_print_worker_cancellation(self, app, sample_pdf):
        printer = QPrinter()
        worker = _PrintWorker(
            printer=printer,
            pdf_path=sample_pdf,
            sheet_indices=[0, 1],
            scaling_mode="actual",
            center=True,
            resolution=96,
        )
        worker.cancel()
        assert worker._cancel is True


# ── Windows Driver & WinPrint Integration Tests ────────────

class TestWinPrintIntegration:
    def test_map_qpagesize_to_dmpaper(self):
        from laydown.ui import win_print
        # A3
        pid, _, _ = win_print.map_qpagesize_to_dmpaper(QPageSize(QPageSize.PageSizeId.A3))
        assert pid == win_print.DMPAPER_A3

        # A4
        pid, _, _ = win_print.map_qpagesize_to_dmpaper(QPageSize(QPageSize.PageSizeId.A4))
        assert pid == win_print.DMPAPER_A4

        # Letter
        pid, _, _ = win_print.map_qpagesize_to_dmpaper(QPageSize(QPageSize.PageSizeId.Letter))
        assert pid == win_print.DMPAPER_LETTER

        # Custom size
        custom_ps = QPageSize(QPageSize.PageSizeId.Custom)
        # None
        pid, _, _ = win_print.map_qpagesize_to_dmpaper(None)
        assert pid == win_print.DMPAPER_A4

    def test_devmode_merge_and_parse_on_windows(self):
        from laydown.ui import win_print
        if not win_print.is_windows():
            pytest.skip("Test requires Windows winspool")

        from PyQt6.QtPrintSupport import QPrinterInfo
        default_name = QPrinterInfo.defaultPrinterName()
        if not default_name:
            pytest.skip("No default printer found on system")

        devmode = win_print.get_native_devmode(default_name)
        if not devmode:
            pytest.skip("Could not retrieve DEVMODE for default printer")

        # Merge A3 Landscape
        merged = win_print.merge_devmode(
            printer_name=default_name,
            in_devmode=devmode,
            paper_size_id=win_print.DMPAPER_A3,
            orientation=win_print.DMORIENT_LANDSCAPE,
            copies=2,
        )
        assert merged is not None

        settings = win_print.parse_devmode_settings(merged)
        assert settings.get("orientation") == win_print.DMORIENT_LANDSCAPE
        assert settings.get("copies") == 2

    def test_sync_controls_from_devmode(self, app, sample_project, sample_pdf):
        from laydown.ui import win_print
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            # Create a mock devmode buffer
            if win_print.is_windows() and win_print.DEVMODEW:
                import ctypes
                dev = win_print.DEVMODEW()
                dev.dmSize = ctypes.sizeof(win_print.DEVMODEW)
                dev.dmFields = win_print.DM_ORIENTATION | win_print.DM_PAPERSIZE | win_print.DM_COPIES | win_print.DM_DUPLEX
                dev.dmOrientation = win_print.DMORIENT_LANDSCAPE
                dev.dmPaperSize = win_print.DMPAPER_A4
                dev.dmCopies = 3
                dev.dmDuplex = win_print.DMDUP_VERTICAL

                dlg._sync_controls_from_devmode(bytes(dev))

                assert dlg._combo_orient.currentData() == "landscape"
                assert dlg._spin_copies.value() == 3
                assert dlg._combo_duplex.currentIndex() == 1  # Long side
        finally:
            dlg.close_document()
            dlg.close()

    def test_direct_gdi_spooling_to_pdf_on_windows(self, sample_pdf):
        from laydown.ui import win_print
        if not win_print.is_windows():
            pytest.skip("Test requires Windows winspool")

        from PyQt6.QtPrintSupport import QPrinterInfo
        printers = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        if "Microsoft Print to PDF" not in printers:
            pytest.skip("Microsoft Print to PDF not installed")

        devmode = win_print.get_native_devmode("Microsoft Print to PDF")
        if not devmode:
            pytest.skip("Could not read DEVMODE for Microsoft Print to PDF")

        devmode_a3 = win_print.merge_devmode(
            printer_name="Microsoft Print to PDF",
            in_devmode=devmode,
            paper_size_id=win_print.DMPAPER_A3,
            orientation=win_print.DMORIENT_LANDSCAPE,
        )

        fd, out_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        if os.path.exists(out_path):
            os.unlink(out_path)

        try:
            win_print.print_pdf_direct_gdi(
                printer_name="Microsoft Print to PDF",
                devmode_bytes=devmode_a3,
                doc_path=sample_pdf,
                sheet_indices=[0],
                scaling_mode="actual",
                center=True,
                resolution=150,
                output_file=out_path,
            )

            assert os.path.exists(out_path)
            res = fitz.open(out_path)
            assert len(res) == 1
            # Check dimensions match A3 Landscape (420 x 297 mm, tolerance 3mm)
            w_mm = res[0].rect.width * 25.4 / 72.0
            h_mm = res[0].rect.height * 25.4 / 72.0
            assert abs(w_mm - 420.0) < 3.0
            assert abs(h_mm - 297.0) < 3.0
            res.close()
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_print_dialog_quality_options(self, app, sample_project, sample_pdf):
        dlg = PrintDialog(project=sample_project, pdf_path=sample_pdf)
        try:
            assert hasattr(dlg, "_combo_dpi")
            assert dlg._combo_dpi.count() == 3
            # Check 600 DPI exists
            idx_600 = dlg._combo_dpi.findData(600)
            assert idx_600 >= 0
            # Check 1200 DPI exists
            idx_1200 = dlg._combo_dpi.findData(1200)
            assert idx_1200 >= 0
            # Check Send to RIP button exists
            assert hasattr(dlg, "_btn_send_rip")
            assert "RIP" in dlg._btn_send_rip.text()
        finally:
            dlg.close_document()

    def test_hot_folder_manager_and_dispatch(self, tmp_path, sample_pdf):
        from laydown.ui.hot_folder import HotFolderManager
        hf_dir = str(tmp_path / "roland_in")
        os.makedirs(hf_dir, exist_ok=True)

        # Add test hot folder
        HotFolderManager.add_folder("Roland VersaWorks", hf_dir)
        folders = HotFolderManager.get_folders()
        assert any(f["name"] == "Roland VersaWorks" and f["path"] == hf_dir for f in folders)

        # Dispatch sample pdf
        dest = HotFolderManager.dispatch_pdf(
            source_pdf_path=sample_pdf,
            destination_folder=hf_dir,
            job_name="Biglietti",
            preset="A3",
        )
        assert os.path.exists(dest)
        assert "Biglietti" in os.path.basename(dest)
        assert os.path.dirname(dest) == hf_dir

        # Cleanup
        HotFolderManager.remove_folder("Roland VersaWorks")

