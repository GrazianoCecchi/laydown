"""
The preview's memory and responsiveness.

A 600-page 2-up job is 300 A3 sheets — ~1.6 GB of pixmaps at 96 DPI and
~14 GB at 288 DPI, all held alive by the canvas. And a settings change used
to call cancel()+wait() on the GUI thread, freezing the window until the old
render finished. These tests pin the two fixes: the render DPI is derived
from sheet count x sheet size against a fixed memory budget, and a render
superseded by a newer request is dropped, never waited for.
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from laydown.engine.data_model import (
    Project, LayoutSettings, LayoutType, SheetSettings, Orientation,
    SourceSettings, SourceBox,
)
from laydown.engine.utils import mm_to_pt
from laydown.ui.preview_panel import (
    SheetCanvas, budgeted_dpi,
    PREVIEW_MEMORY_BUDGET_BYTES, PREVIEW_DPI_FLOOR,
)

A3_LANDSCAPE_PT = (mm_to_pt(420.0), mm_to_pt(297.0))
A4_PORTRAIT_PT = (mm_to_pt(210.0), mm_to_pt(297.0))


def estimated_bytes(sheet_count, sheet_w_pt, sheet_h_pt, dpi):
    """What the canvas would allocate for the whole job at *dpi* (RGB888)."""
    px_w = sheet_w_pt * dpi / 72.0
    px_h = sheet_h_pt * dpi / 72.0
    return sheet_count * px_w * px_h * 3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_canvas(app):
    """Factory: a SheetCanvas whose debounce fires immediately, cleaned up
    after the test (joining any worker still in flight)."""
    made = []

    def _make(cls=SheetCanvas):
        canvas = cls()
        canvas._debounce.setInterval(0)
        made.append(canvas)
        return canvas

    yield _make
    for canvas in made:
        canvas.cleanup()


class _CountingCanvas(SheetCanvas):
    """Counts results the canvas actually accepted (stale ones excluded)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.displayed_results = 0

    def _on_done(self, images, render_dpi, generation):
        if generation == self._generation:
            self.displayed_results += 1
        super()._on_done(images, render_dpi, generation)


def _project(src):
    p = Project(source_pdf_path=src)
    p.source = SourceSettings(box=SourceBox.TRIM)
    p.sheet = SheetSettings(preset="A3", orientation=Orientation.LANDSCAPE)
    p.layout = LayoutSettings(layout_type=LayoutType.NUP, nup=2)
    return p


def _pump(app, cond, timeout=15.0, what="condition"):
    """processEvents until cond() is true; never hangs past *timeout*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return cond()


# ── the pure DPI function ───────────────────────────


class TestBudgetedDpi:
    def test_300_a3_sheets_stay_within_the_budget(self):
        dpi = budgeted_dpi(300, *A3_LANDSCAPE_PT, 300)
        assert dpi < 300, "the budget should bite on a 300-sheet job"
        assert estimated_bytes(300, *A3_LANDSCAPE_PT, dpi) <= PREVIEW_MEMORY_BUDGET_BYTES

    def test_small_jobs_render_at_the_normal_target_dpi(self, app):
        canvas = SheetCanvas()
        try:
            for zoom in (1.0, 2.0, 4.0):
                canvas._zoom = zoom
                target = canvas._target_dpi()
                assert budgeted_dpi(4, *A4_PORTRAIT_PT, target) == target, (
                    "4 A4 sheets fit the budget; the DPI must stay at the "
                    "zoom-driven target"
                )
        finally:
            canvas.cleanup()

    def test_dpi_never_drops_below_the_floor(self):
        assert budgeted_dpi(200000, *A3_LANDSCAPE_PT, 300) == PREVIEW_DPI_FLOOR
        assert budgeted_dpi(300, *A3_LANDSCAPE_PT, 300) >= PREVIEW_DPI_FLOOR


# ── rapid settings changes ──────────────────────────


class TestRapidUpdates:
    def test_two_rapid_updates_display_only_the_second_project(
            self, app, source_pdf, make_canvas):
        canvas = make_canvas(_CountingCanvas)
        first = _project(source_pdf(n_pages=2))    # 1 sheet
        second = _project(source_pdf(n_pages=6))  # 3 sheets

        canvas.update_project(first)
        canvas.update_project(second)

        assert _pump(
            app,
            lambda: len(canvas._pixmaps) == 3 and len(canvas._sheet_widgets) == 3,
            what="the second project to appear",
        ), "the preview never showed the second project"
        assert canvas.displayed_results == 1, (
            "two rapid updates should produce exactly one displayed result"
        )

    def test_stale_render_result_is_ignored(self, app, source_pdf, make_canvas):
        canvas = make_canvas(_CountingCanvas)
        first = _project(source_pdf(n_pages=2))    # 1 sheet
        second = _project(source_pdf(n_pages=6))  # 3 sheets

        canvas._project = first
        canvas._do_render()          # generation 1
        canvas._project = second
        canvas._do_render()          # generation 2; must not wait for gen 1

        # Generation 1's result arrives late — it must be dropped, not shown.
        stale = QImage(4, 4, QImage.Format.Format_RGB888)
        canvas._on_done([stale], 96, 1)
        assert canvas._pixmaps == [], "a stale result overwrote the display"

        assert _pump(app, lambda: len(canvas._pixmaps) == 3,
                     what="the second project to appear"), (
            "the newest settings did not win"
        )
        assert canvas.displayed_results == 1

    def test_do_render_does_not_block_on_a_running_render(
            self, app, source_pdf, make_canvas, monkeypatch):
        import laydown.ui.preview_panel as preview_panel
        real_impose = preview_panel.impose_to_temp

        def slow_impose(project):
            time.sleep(1.0)
            return real_impose(project)

        monkeypatch.setattr(preview_panel, "impose_to_temp", slow_impose)

        canvas = make_canvas()
        canvas.update_project(_project(source_pdf(n_pages=2)))
        assert _pump(
            app,
            lambda: canvas._worker is not None and canvas._worker.isRunning(),
            what="the first render to start",
        ), "the first render never started"

        canvas._project = _project(source_pdf(n_pages=6))
        start = time.monotonic()
        canvas._do_render()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, (
            f"_do_render blocked the GUI thread for {elapsed:.2f}s waiting "
            "for the previous render"
        )

        monkeypatch.undo()
        assert _pump(app, lambda: len(canvas._pixmaps) == 3, timeout=30,
                     what="the second project to appear")
