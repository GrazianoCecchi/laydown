"""
Tests for UI error paths (LD-UI-2):
1) Preflight exception produces a visible error finding instead of silent [].
2) Opening an encrypted/password-protected PDF gives a clear message without raising.
3) Export to the source path is refused (source stays byte-identical), and export
   is atomic with no leftover temp files on success or failure.
4) Loading a preset that omits a key resets that key to its schema default.
"""

import os
import pathlib
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PyQt6.QtWidgets import QApplication, QFileDialog

from laydown.engine.preflight import Severity
from laydown.ui.main_window import MainWindow
from laydown.ui.panel import ValueStore
from laydown.ui.presets import load_preset, save_preset
from tests.helpers import make_source_pdf


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class TestPreflightErrors:
    def test_preflight_crash_becomes_visible_finding(self, app, tmp_path, monkeypatch):
        src = make_source_pdf(str(tmp_path / "sample.pdf"), n_pages=2)
        win = MainWindow()
        win.show()
        win.load_pdf(src)

        def bad_preflight(*args, **kwargs):
            raise RuntimeError("preflight calculation failure")

        monkeypatch.setattr("laydown.ui.main_window.preflight", bad_preflight)
        win._run_preflight()

        assert len(win._findings) == 1
        finding = win._findings[0]
        assert finding.severity is Severity.ERROR
        assert "preflight could not run" in finding.message.lower()
        assert "preflight calculation failure" in finding.message
        assert not win._preflight_bar.isHidden()
        assert win._preflight_bar.isVisible()
        assert "preflight could not run" in win._preflight_text.text().lower()


class TestEncryptedPdf:
    def test_open_encrypted_pdf_shows_clear_message_without_exception(self, app, tmp_path):
        doc = fitz.open()
        doc.new_page(width=200, height=200)
        enc_path = str(tmp_path / "encrypted.pdf")
        doc.save(
            enc_path,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw="secret_user",
            owner_pw="secret_owner",
        )
        doc.close()

        win = MainWindow()
        win.show()
        # Must not raise an exception
        win.load_pdf(enc_path)

        status_msg = win.statusBar().currentMessage().lower()
        strip_text = win._preflight_text.text().lower()
        assert "password" in status_msg or "password" in strip_text
        assert not win._preflight_bar.isHidden()
        assert win._preflight_bar.isVisible()
        assert win._source_doc is None
        assert win._source_path == ""
        assert not win._generate.isEnabled()


class TestExportSafety:
    def test_export_to_source_path_is_refused_and_source_unmodified(self, app, tmp_path, monkeypatch):
        src_path = str(tmp_path / "source.pdf")
        make_source_pdf(src_path, n_pages=2)
        orig_bytes = pathlib.Path(src_path).read_bytes()

        win = MainWindow()
        win.load_pdf(src_path)

        # 1. Direct call to export_to with exact source path
        assert win.export_to(src_path) is False
        assert pathlib.Path(src_path).read_bytes() == orig_bytes
        assert "cannot overwrite" in win.statusBar().currentMessage().lower()

        # 2. Path normalization variation (e.g. ./source.pdf)
        alt_path = str(tmp_path / "." / "source.pdf")
        assert win.export_to(alt_path) is False
        assert pathlib.Path(src_path).read_bytes() == orig_bytes

        # 3. Via _on_export dialog slot
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (src_path, "PDF Files (*.pdf)"))
        win._on_export()
        assert pathlib.Path(src_path).read_bytes() == orig_bytes

    def test_export_to_new_path_atomic_and_no_temp_files(self, app, tmp_path):
        src_path = str(tmp_path / "source.pdf")
        out_path = str(tmp_path / "imposed_out.pdf")
        make_source_pdf(src_path, n_pages=2)

        win = MainWindow()
        win.load_pdf(src_path)

        res = win.export_to(out_path)
        assert res is True
        win._export.wait(5000)
        app.processEvents()

        assert os.path.isfile(out_path)
        assert os.path.getsize(out_path) > 0

        # Verify output is a valid imposed PDF
        out_doc = fitz.open(out_path)
        assert len(out_doc) >= 1
        out_doc.close()

        # Check no temporary files were left behind in the target directory
        temp_files = [
            f for f in os.listdir(str(tmp_path))
            if f.endswith(".tmp") or f.startswith(".")
        ]
        assert temp_files == []

    def test_export_failure_cleans_up_temp_file(self, app, tmp_path, monkeypatch):
        src_path = str(tmp_path / "source.pdf")
        out_path = str(tmp_path / "broken_out.pdf")
        make_source_pdf(src_path, n_pages=2)

        win = MainWindow()
        win.load_pdf(src_path)

        def broken_impose(*args, **kwargs):
            raise RuntimeError("disk write failure simulation")

        monkeypatch.setattr("laydown.ui.main_window.impose", broken_impose)

        win.export_to(out_path)
        win._export.wait(5000)
        app.processEvents()

        assert not os.path.exists(out_path)
        temp_files = [
            f for f in os.listdir(str(tmp_path))
            if f.endswith(".tmp") or f.startswith(".")
        ]
        assert temp_files == []


class TestPresetDefaults:
    def test_preset_omitting_key_resets_to_schema_default(self, app):
        store = ValueStore()
        # Schema default for layout.nup is 2
        assert store.get("layout.nup") == 2

        store.set("layout.nup", 4)
        store.set("sheet.preset", "A4")
        assert store.get("layout.nup") == 4
        assert store.get("sheet.preset") == "A4"

        # Load a preset that specifies sheet.preset but omits layout.nup
        store.load({"sheet.preset": "A2"})
        assert store.get("sheet.preset") == "A2"
        assert store.get("layout.nup") == 2, "omitted layout.nup should reset to default 2"

    def test_roundtrip_partial_preset_file(self, app, tmp_path):
        store = ValueStore()
        store.set("layout.nup", 4)
        assert store.get("layout.nup") == 4

        # Save a preset that only has sheet.preset
        preset_file = str(tmp_path / "preset.json")
        save_preset(preset_file, {"sheet.preset": "A2"})

        store.load(load_preset(preset_file))
        assert store.get("sheet.preset") == "A2"
        assert store.get("layout.nup") == 2
