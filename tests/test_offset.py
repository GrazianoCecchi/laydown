"""
Tests for duplex / front-back registration offset compensation.
"""

import fitz
import pytest

from laydown.engine.capabilities import HONOURED
from laydown.engine.data_model import (
    LayoutSettings,
    LayoutType,
    Orientation,
    Project,
    SheetSettings,
)
from laydown.engine.geometry import cell_grid, sheet_plan
from laydown.engine.impose import impose_detailed
from laydown.engine.utils import mm_to_pt
from laydown.ui.preview_panel import CellInfo, draw_overlays
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _close(a, b, tol=0.05):
    return abs(a - b) < tol


class TestOffsetModelAndCapabilities:
    def test_default_offset_is_zero(self):
        sheet = SheetSettings()
        assert sheet.offset_h_mm == 0.0
        assert sheet.offset_v_mm == 0.0

    def test_offset_targets_are_honoured(self):
        assert "sheet.offset_h_mm" in HONOURED
        assert "sheet.offset_v_mm" in HONOURED


class TestOffsetGeometry:
    def test_cell_grid_shifts_by_offset(self):
        base_project = Project()
        base_project.sheet = SheetSettings(
            preset="A3",
            orientation=Orientation.LANDSCAPE,
            offset_h_mm=0.0,
            offset_v_mm=0.0,
        )
        base_project.layout = LayoutSettings(nup=2)

        base_cells = cell_grid(base_project, 2, 1)

        shifted_project = Project()
        dx_mm = 2.5
        dy_mm = -1.8
        shifted_project.sheet = SheetSettings(
            preset="A3",
            orientation=Orientation.LANDSCAPE,
            offset_h_mm=dx_mm,
            offset_v_mm=dy_mm,
        )
        shifted_project.layout = LayoutSettings(nup=2)

        shifted_cells = cell_grid(shifted_project, 2, 1)

        dx_pt = mm_to_pt(dx_mm)
        dy_pt = mm_to_pt(dy_mm)

        for b, s in zip(base_cells, shifted_cells):
            assert _close(s.x0 - b.x0, dx_pt)
            assert _close(s.x1 - b.x1, dx_pt)
            assert _close(s.y0 - b.y0, dy_pt)
            assert _close(s.y1 - b.y1, dy_pt)

    def test_imposition_output_shifts_placed_pages(self, source_pdf, out_path):
        src = source_pdf(n_pages=2)
        out = out_path()

        dx_mm = 3.0
        dy_mm = 2.0

        project = Project(source_pdf_path=src)
        project.sheet = SheetSettings(
            preset="A3",
            orientation=Orientation.LANDSCAPE,
            offset_h_mm=dx_mm,
            offset_v_mm=dy_mm,
        )
        project.layout = LayoutSettings(nup=2)

        res = impose_detailed(project, out)
        assert res.sheets >= 1
        placed = res.placed[0]

        # Compare with unshifted
        project_zero = Project(source_pdf_path=src)
        project_zero.sheet = SheetSettings(
            preset="A3",
            orientation=Orientation.LANDSCAPE,
            offset_h_mm=0.0,
            offset_v_mm=0.0,
        )
        project_zero.layout = LayoutSettings(nup=2)

        res_zero = impose_detailed(project_zero, out_path())
        placed_zero = res_zero.placed[0]

        dx_pt = mm_to_pt(dx_mm)
        dy_pt = mm_to_pt(dy_mm)

        for p_shift, p_zero in zip(placed, placed_zero):
            assert _close(p_shift.trim.x0 - p_zero.trim.x0, dx_pt)
            assert _close(p_shift.trim.y0 - p_zero.trim.y0, dy_pt)


class TestOffsetOverlayRendering:
    def test_draw_overlays_with_offset_renders_badge(self, qapp):
        img = QImage(800, 600, QImage.Format.Format_RGB888)
        img.fill(0xFFFFFF)

        cells = [CellInfo(x0=50, y0=50, x1=200, y1=300, page_num=1)]
        res_img = draw_overlays(
            img,
            cells,
            scale=1.0,
            show_tops=True,
            show_numbers=True,
            show_frames=True,
            show_previews=True,
            offset_mm=(1.5, -2.0),
        )
        assert res_img is not None
        assert not res_img.isNull()
