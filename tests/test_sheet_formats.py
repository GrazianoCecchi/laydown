"""
Tests for user custom sheet formats and SheetFormatManager.
"""

import json
import os
import fitz
import pytest

from laydown.engine.data_model import (
    Orientation, Project, SheetSettings, LayoutSettings,
    clear_user_sheet_presets, register_user_sheet_preset,
    USER_SHEET_PRESETS_MM, SHEET_PRESETS_MM,
)
from laydown.engine.impose import impose
from laydown.engine.utils import mm_to_pt
from laydown.ui.sheet_formats import SheetFormatManager


@pytest.fixture(autouse=True)
def clean_user_presets(tmp_path, monkeypatch):
    """Isolate sheet_formats.json to a temporary directory for tests."""
    temp_file = str(tmp_path / "sheet_formats.json")
    monkeypatch.setattr(SheetFormatManager, "get_file_path", classmethod(lambda cls: temp_file))
    SheetFormatManager._cached_formats = None
    clear_user_sheet_presets()
    yield
    SheetFormatManager._cached_formats = None
    clear_user_sheet_presets()


class TestSheetFormatManager:
    def test_empty_catalog_initially(self):
        formats = SheetFormatManager.load_custom_formats(reload=True)
        assert formats == []
        assert USER_SHEET_PRESETS_MM == {}

    def test_save_and_load_custom_format(self):
        rec = SheetFormatManager.save_custom_format(
            name="Copertina 3 Ante",
            width_mm=320.0,
            height_mm=650.0,
            description="Banner a tre ante",
        )
        assert rec["name"] == "Copertina 3 Ante"
        assert rec["width_mm"] == 320.0
        assert rec["height_mm"] == 650.0

        # Verify engine sync
        assert "Copertina 3 Ante" in USER_SHEET_PRESETS_MM
        assert USER_SHEET_PRESETS_MM["Copertina 3 Ante"] == (320.0, 650.0)

        # Verify reload from disk
        loaded = SheetFormatManager.load_custom_formats(reload=True)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Copertina 3 Ante"
        assert loaded[0]["width_mm"] == 320.0
        assert loaded[0]["height_mm"] == 650.0

    def test_canonical_portrait_storage(self):
        # Even if user enters landscape numbers (w > h), stored portrait (w < h)
        SheetFormatManager.save_custom_format("Test Landscape Entry", 650.0, 320.0)
        assert USER_SHEET_PRESETS_MM["Test Landscape Entry"] == (320.0, 650.0)

    def test_delete_custom_format(self):
        SheetFormatManager.save_custom_format("Formato Temporaneo", 200.0, 300.0)
        assert "Formato Temporaneo" in USER_SHEET_PRESETS_MM

        deleted = SheetFormatManager.delete_custom_format("Formato Temporaneo")
        assert deleted is True
        assert "Formato Temporaneo" not in USER_SHEET_PRESETS_MM
        assert SheetFormatManager.load_custom_formats(reload=True) == []

    def test_reserved_and_invalid_names_raise(self):
        with pytest.raises(ValueError, match="riservato"):
            SheetFormatManager.save_custom_format("A4", 210.0, 297.0)

        with pytest.raises(ValueError, match="riservato"):
            SheetFormatManager.save_custom_format("Custom", 210.0, 297.0)

        with pytest.raises(ValueError, match="vuoto"):
            SheetFormatManager.save_custom_format("   ", 210.0, 297.0)

        with pytest.raises(ValueError, match="zero"):
            SheetFormatManager.save_custom_format("ZeroDim", 0.0, 297.0)

    def test_export_and_import_json(self, tmp_path):
        SheetFormatManager.save_custom_format("Formato A", 100.0, 200.0, "Desc A")
        SheetFormatManager.save_custom_format("Formato B", 150.0, 250.0, "Desc B")

        export_path = str(tmp_path / "exported.json")
        SheetFormatManager.export_formats(export_path)
        assert os.path.exists(export_path)

        # Clear and reimport
        SheetFormatManager._cached_formats = []
        clear_user_sheet_presets()

        count = SheetFormatManager.import_formats(export_path)
        assert count == 2
        assert "Formato A" in USER_SHEET_PRESETS_MM
        assert "Formato B" in USER_SHEET_PRESETS_MM

    def test_preset_options_includes_custom_and_builtin(self):
        SheetFormatManager.save_custom_format("Mio Banner", 300.0, 900.0)
        options = SheetFormatManager.get_preset_options()
        keys = [k for k, _ in options]

        assert "A4" in keys
        assert "SRA3" in keys
        assert "A3+" in keys
        assert "70x100" in keys
        assert "Mio Banner" in keys
        assert "Custom" in keys


class TestCustomSheetImposition:
    def test_sheet_settings_resolves_custom_preset(self):
        register_user_sheet_preset("Banner 3 Ante", 320.0, 650.0)
        sheet = SheetSettings(preset="Banner 3 Ante", orientation=Orientation.LANDSCAPE)
        assert sheet.sheet_size_mm() == (650.0, 320.0)

        sheet_p = SheetSettings(preset="Banner 3 Ante", orientation=Orientation.PORTRAIT)
        assert sheet_p.sheet_size_mm() == (320.0, 650.0)

    def test_imposition_with_user_custom_format(self, source_pdf, out_path):
        register_user_sheet_preset("Banner Speciale", 320.0, 650.0)
        src = source_pdf(n_pages=2)
        out = out_path()

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="Banner Speciale", orientation=Orientation.LANDSCAPE)
        project.layout = LayoutSettings(nup=2)

        impose(project, out)
        with fitz.open(out) as doc:
            rect = doc[0].rect

        assert abs(rect.width - mm_to_pt(650.0)) < 0.1
        assert abs(rect.height - mm_to_pt(320.0)) < 0.1
