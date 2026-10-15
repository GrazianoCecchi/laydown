"""
Press sheet presets — the SRA sizes are the most common press sheets, so they must
be one click away instead of typed in as Custom.
"""

import fitz
import pytest

from laydown.cli import build_project
from laydown.engine.data_model import (
    Project, SheetSettings, LayoutSettings, Orientation, SHEET_PRESETS_MM,
)
from laydown.engine.impose import impose
from laydown.engine.utils import mm_to_pt
from laydown.ui.presets import load_preset, save_preset
from laydown.ui.schema import all_controls, defaults


def _close(a, b):
    return abs(a - b) < 0.01


class TestPresetTable:
    def test_sra_sizes(self):
        assert SHEET_PRESETS_MM["SRA4"] == (225.0, 320.0)
        assert SHEET_PRESETS_MM["SRA3"] == (320.0, 450.0)
        assert SHEET_PRESETS_MM["SRA2"] == (450.0, 640.0)

    def test_dropdown_reads_naturally(self):
        control = next(c for c in all_controls() if c.target == "sheet.preset")
        assert [key for key, _ in control.options] == [
            "A5", "A4", "SRA4", "A3", "SRA3", "A2", "SRA2",
            "Letter", "Legal", "Tabloid", "Custom",
        ]

    def test_default_stays_a3(self):
        assert defaults()["sheet.preset"] == "A3"
        assert SheetSettings().preset == "A3"

    @pytest.mark.parametrize("name", list(SHEET_PRESETS_MM))
    def test_presets_are_stored_portrait(self, name):
        w, h = SHEET_PRESETS_MM[name]
        assert w < h


class TestSRA3Imposes:
    @pytest.mark.parametrize("orientation,expected_mm", [
        (Orientation.PORTRAIT, (320.0, 450.0)),
        (Orientation.LANDSCAPE, (450.0, 320.0)),
    ])
    def test_sheet_is_sra3(self, source_pdf, out_path, orientation, expected_mm):
        src = source_pdf(n_pages=4)
        out = out_path()
        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(preset="SRA3", orientation=orientation)
        project.layout = LayoutSettings(nup=2)
        assert project.sheet.sheet_size_mm() == expected_mm

        impose(project, out)
        with fitz.open(out) as doc:
            rect = doc[0].rect
        assert _close(rect.width, mm_to_pt(expected_mm[0]))
        assert _close(rect.height, mm_to_pt(expected_mm[1]))


class TestPresetPlumbing:
    def test_cli_accepts_sra3(self, source_pdf):
        project = build_project(source_pdf(n_pages=2), set_args=["sheet.preset=SRA3"])
        assert project.sheet.preset == "SRA3"
        assert sorted(project.sheet.sheet_size_mm()) == [320.0, 450.0]

    def test_saved_preset_round_trips_sra2(self, tmp_path):
        path = str(tmp_path / "p.json")
        save_preset(path, {"sheet.preset": "SRA2"})
        assert load_preset(path)["sheet.preset"] == "SRA2"
