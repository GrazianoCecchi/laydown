"""
Unit tests for central Presets Catalog, PresetManager, PresetSaveDialog and PresetManagerDialog.
"""

import os
import tempfile
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from laydown.engine.data_model import Orientation, PageScaling
from laydown.ui.presets import (
    PresetManager,
    _get_factory_presets,
    get_presets_file_path,
    save_preset,
    load_preset,
)
from laydown.ui.preset_dialogs import PresetSaveDialog, PresetManagerDialog
from laydown.ui.schema import defaults


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def custom_catalog_path(tmp_path, monkeypatch):
    catalog_file = str(tmp_path / "presets_test.json")
    monkeypatch.setattr(PresetManager, "get_catalog_path", classmethod(lambda cls: catalog_file))
    PresetManager._cached_catalog = None
    yield catalog_file
    PresetManager._cached_catalog = None


class TestPresetManager:
    def test_initial_catalog_creates_factory_presets(self, custom_catalog_path):
        assert not os.path.exists(custom_catalog_path)
        catalog = PresetManager.load_catalog()
        assert os.path.exists(custom_catalog_path)
        assert len(catalog) >= 5
        assert "Standard (2-up su A3)" in catalog
        assert "Biglietti da visita (10-up su A3)" in catalog
        assert "Volantini A5 (4-up su A3)" in catalog
        assert "Opuscolo A4 punto metallico (su A3)" in catalog
        assert "Locandina A3 (1-up a vivo)" in catalog

    def test_save_and_retrieve_preset(self, custom_catalog_path):
        vals = defaults()
        vals["sheet.preset"] = "SRA3"
        vals["layout.nup"] = 8

        PresetManager.save_preset("Test SRA3 8-up", vals, description="Preset per test unitario")

        names = PresetManager.list_names()
        assert "Test SRA3 8-up" in names

        retrieved = PresetManager.get_preset("Test SRA3 8-up")
        assert retrieved is not None
        assert retrieved["sheet.preset"] == "SRA3"
        assert retrieved["layout.nup"] == 8

        meta = PresetManager.get_preset_meta("Test SRA3 8-up")
        assert meta["description"] == "Preset per test unitario"
        assert meta["is_factory"] is False

    def test_rename_preset(self, custom_catalog_path):
        vals = defaults()
        PresetManager.save_preset("Vecchio Nome", vals, description="Desc 1")
        assert "Vecchio Nome" in PresetManager.list_names()

        success = PresetManager.rename_preset("Vecchio Nome", "Nuovo Nome", new_description="Desc 2")
        assert success is True
        assert "Vecchio Nome" not in PresetManager.list_names()
        assert "Nuovo Nome" in PresetManager.list_names()

        meta = PresetManager.get_preset_meta("Nuovo Nome")
        assert meta["description"] == "Desc 2"

    def test_delete_preset(self, custom_catalog_path):
        vals = defaults()
        PresetManager.save_preset("Da Eliminare", vals)
        assert "Da Eliminare" in PresetManager.list_names()

        deleted = PresetManager.delete_preset("Da Eliminare")
        assert deleted is True
        assert "Da Eliminare" not in PresetManager.list_names()

    def test_import_and_export_preset(self, custom_catalog_path, tmp_path):
        vals = defaults()
        vals["layout.scaling"] = PageScaling.FIT_TO_CELL

        export_path = str(tmp_path / "esportato.laydown.json")
        PresetManager.save_preset("Preset Esportabile", vals, description="Esportami")
        PresetManager.export_to_file("Preset Esportabile", export_path)

        assert os.path.exists(export_path)
        loaded = load_preset(export_path)
        assert loaded["layout.scaling"] == PageScaling.FIT_TO_CELL

        # Import with another name
        imported_name = PresetManager.import_from_file(export_path)
        assert "esportato" in imported_name
        assert imported_name in PresetManager.list_names()

    def test_reset_to_factory(self, custom_catalog_path):
        PresetManager.save_preset("Custom da cancellare", defaults())
        assert "Custom da cancellare" in PresetManager.list_names()

        PresetManager.reset_to_factory()
        assert "Custom da cancellare" not in PresetManager.list_names()
        assert "Standard (2-up su A3)" in PresetManager.list_names()


class TestPresetDialogs:
    def test_save_dialog_validation_and_save(self, app, custom_catalog_path):
        vals = defaults()
        dlg = PresetSaveDialog(current_values=vals, default_name="Mio Lavoro Speciale")
        try:
            assert dlg._btn_save.isEnabled()
            dlg._name_edit.setText("")
            assert not dlg._btn_save.isEnabled()

            dlg._name_edit.setText("Mio Lavoro Speciale")
            dlg._desc_edit.setText("Descrizione test")
            dlg._on_save()

            assert dlg.saved_name == "Mio Lavoro Speciale"
            assert "Mio Lavoro Speciale" in PresetManager.list_names()
        finally:
            dlg.close()

    def test_manager_dialog_selection_and_meta(self, app, custom_catalog_path):
        vals = defaults()
        PresetManager.save_preset("Speciale", vals, description="Dettaglio")

        dlg = PresetManagerDialog(current_values=vals)
        try:
            assert dlg._table.rowCount() >= 1
            dlg._refresh_list(select_name="Speciale")
            assert dlg._selected_name() == "Speciale"
            assert "Speciale" in dlg._det_title.text()
            assert dlg._btn_apply.isEnabled()
        finally:
            dlg.close()
