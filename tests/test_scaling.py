"""
Page scaling (LD-SCALE-1) — Actual size by default, Fit to cell opt-in.

fitted_rect used to scale every page to FILL its cell, so a 90x50 mm card on an
A4 2x4 grid printed at 100x55.6 mm, a 1-Up blew it up to 200x111 mm, and the
Scale Pages preprocessor changed nothing (the page was re-fitted to its cell
afterwards anyway). These tests pin the two modes and the interactions that
were silently wrong before.
"""

import fitz
import pytest

from laydown.cli import build_project, coerce_setting, main as cli_main
from laydown.engine.data_model import (
    LayoutSettings,
    LayoutType,
    Orientation,
    PageScaling,
    PreprocessorStep,
    PreprocessorType,
    Project,
    SheetSettings,
)
from laydown.engine.geometry import place_page
from laydown.engine.impose import impose_detailed
from laydown.engine.preflight import Severity, preflight
from laydown.engine.utils import mm_to_pt
from laydown.ui.presets import save_preset
from laydown.ui.schema import defaults

from tests.helpers import make_source_pdf


PT_PER_MM = mm_to_pt(1.0)


def mm(pt: float) -> float:
    return pt / PT_PER_MM


def a_card_project(src, scaling, *, nup=None, rows=0, cols=0, steps=None):
    """A 90x50 mm card job on A4 portrait, the fixture for every size check."""
    p = Project(source_pdf_path=src)
    p.sheet = SheetSettings(preset="A4", orientation=Orientation.PORTRAIT)
    p.layout = LayoutSettings(
        layout_type=LayoutType.NUP, nup=nup or 2, rows=rows, cols=cols,
        scaling=scaling)
    p.preprocessors = steps or []
    return p


def first_trim(project, out):
    """The trim of the first placed page, as (width_mm, height_mm)."""
    placed = impose_detailed(project, out).placed[0][0]
    return mm(placed.trim.width), mm(placed.trim.height)


class TestActualSize:
    def test_a_card_prints_at_its_real_size(self, source_pdf, out_path):
        # A4 portrait 2x4: cells are 100x71.75 mm. Filling the cell blew the
        # 90x50 card up to 100x55.6; actual size keeps it 90x50.
        src = source_pdf(n_pages=2, w_mm=90, h_mm=50)
        w, h = first_trim(a_card_project(src, PageScaling.ACTUAL_SIZE), out_path())
        assert (w, h) == pytest.approx((90.0, 50.0), abs=0.1)

    def test_fit_to_cell_still_fills(self, source_pdf, out_path):
        # The opt-in keeps today's behaviour exactly: scale 100/90 = 111%.
        src = source_pdf(n_pages=2, w_mm=90, h_mm=50)
        w, h = first_trim(a_card_project(src, PageScaling.FIT_TO_CELL), out_path())
        assert (w, h) == pytest.approx((100.0, 55.6), abs=0.1)

    def test_one_up_actual_vs_fit(self, source_pdf, out_path):
        # 1-Up on A4 portrait leaves a 200x287 mm cell: actual size stays 90x50,
        # fit to cell scales by 200/90 = 222%.
        src = source_pdf(n_pages=1, w_mm=90, h_mm=50)
        w, h = first_trim(a_card_project(src, PageScaling.ACTUAL_SIZE, nup=1), out_path())
        assert (w, h) == pytest.approx((90.0, 50.0), abs=0.1)
        w, h = first_trim(a_card_project(src, PageScaling.FIT_TO_CELL, nup=1), out_path())
        assert (w, h) == pytest.approx((200.0, 111.1), abs=0.1)

    def test_a_page_too_big_for_the_cell_shrinks_identically_in_both_modes(
            self, source_pdf, out_path):
        # The cap only limits enlarging. An A3 page in a 200x287 mm 1-Up cell
        # must shrink to the same size either way.
        src = source_pdf(n_pages=1, w_mm=297, h_mm=420)
    # both modes place it by the same min() — assert equal, and smaller than source
        actual = first_trim(a_card_project(src, PageScaling.ACTUAL_SIZE, nup=1), out_path())
        fit = first_trim(a_card_project(src, PageScaling.FIT_TO_CELL, nup=1), out_path())
        assert actual == pytest.approx(fit, abs=0.1)
        assert actual[0] < 297.0 and actual[1] < 420.0


class TestScalePreprocessor:
    def test_scale_pages_now_has_an_effect_at_actual_size(self, source_pdf, out_path):
        # Before: the preprocessor halved the page, then the fit re-blew it up to
        # fill the cell, so 50% changed nothing. At actual size it must show.
        src = source_pdf(n_pages=2, w_mm=90, h_mm=50)
        steps = [PreprocessorStep(type=PreprocessorType.SCALE_PAGES, scale_factor=0.5)]
        w, h = first_trim(
            a_card_project(src, PageScaling.ACTUAL_SIZE, steps=steps), out_path())
        assert (w, h) == pytest.approx((45.0, 25.0), abs=0.1)

    def test_fit_to_cell_still_absorbs_the_preprocessor(self, source_pdf, out_path):
        # Under fit-to-cell the re-fit wins, as it always did: the cell is filled
        # whatever the preprocessor did to the page.
        src = source_pdf(n_pages=2, w_mm=90, h_mm=50)
        steps = [PreprocessorStep(type=PreprocessorType.SCALE_PAGES, scale_factor=0.5)]
        w, h = first_trim(
            a_card_project(src, PageScaling.FIT_TO_CELL, steps=steps), out_path())
        assert (w, h) == pytest.approx((100.0, 55.6), abs=0.1)


class TestAutoRotateAtActualSize:
    # An A4-portrait 2x4 cell: 100 x 71.75 mm.
    CELL_WH_MM = (100.0, 71.75)

    def _cell(self):
        w, h = (mm_to_pt(v) for v in self.CELL_WH_MM)
        return fitz.Rect(0, 0, w, h)

    def _place(self, w_mm, h_mm, scaling):
        w, h = mm_to_pt(w_mm), mm_to_pt(h_mm)
        place = fitz.Rect(0, 0, w, h)
        return place_page(self._cell(), place, place, allow_rotate=True, scaling=scaling)

    def test_a_page_that_fits_upright_is_not_turned(self):
        # Landscape card, upright fit 111% -> capped at 100%. Turned it would
        # shrink to 79.7%, so turning is pure loss: it must stay upright.
        target, trim, rotate = self._place(90, 50, PageScaling.ACTUAL_SIZE)
        assert rotate == 0
        assert (mm(trim.width), mm(trim.height)) == pytest.approx((90.0, 50.0), abs=0.1)

    def test_turning_still_happens_when_it_avoids_shrinking(self):
        # Portrait card, upright fit 79.7%; turned it fits at 100%. Turning now
        # avoids shrinking, so it must turn — and print at actual size.
        target, trim, rotate = self._place(50, 90, PageScaling.ACTUAL_SIZE)
        assert rotate == 90
        assert (mm(trim.width), mm(trim.height)) == pytest.approx((90.0, 50.0), abs=0.1)

    def test_a_tie_prefers_upright(self):
        # Square card: both orientations fit at 100%, so the tie goes to upright.
        target, trim, rotate = self._place(50, 50, PageScaling.ACTUAL_SIZE)
        assert rotate == 0

    def test_fit_to_cell_still_turns_to_fill_better(self):
        # The old rule, unchanged under the opt-in: turn when that fills more.
        # For a 35x50 mm card in a 100x71.75 mm cell: upright fit 143.5% vs turned 200%.
        target, trim, rotate = self._place(35, 50, PageScaling.FIT_TO_CELL)
        assert rotate == 90
        assert (mm(trim.width), mm(trim.height)) == pytest.approx((100.0, 70.0), abs=0.1)


class TestBleedAtActualSize:
    def test_bleed_spills_around_the_actual_size_trim(self):
        # 3 mm of bleed around a 90x50 place box. The scale is 1.0 now, so the
        # target must be the trim plus exactly those 3 mm — not scaled up.
        w, h = mm_to_pt(90), mm_to_pt(50)
        b = mm_to_pt(3)
        place = fitz.Rect(0, 0, w, h)
        clip = fitz.Rect(-b, -b, w + b, h + b)
        cell = fitz.Rect(0, 0, mm_to_pt(100), mm_to_pt(71.75))
        target, trim, rotate = place_page(cell, place, clip, scaling=PageScaling.ACTUAL_SIZE)
        assert rotate == 0
        assert (mm(trim.width), mm(trim.height)) == pytest.approx((90.0, 50.0), abs=0.1)
        assert (mm(target.width), mm(target.height)) == pytest.approx((96.0, 56.0), abs=0.1)


class TestBooklet:
    def test_booklet_pages_stay_at_actual_size(self, source_pdf, out_path):
        # A6 pages on an A4-landscape booklet cell (143.5x287 mm after margins):
        # filling the cell would scale to 143.5/105 = 137%.
        src = source_pdf(n_pages=4, w_mm=105, h_mm=148)
        p = Project(source_pdf_path=src)
        p.sheet = SheetSettings(preset="A4", orientation=Orientation.LANDSCAPE)
        p.layout = LayoutSettings(
            layout_type=LayoutType.BOOKLET, scaling=PageScaling.ACTUAL_SIZE)
        w, h = first_trim(p, out_path())
        assert (w, h) == pytest.approx((105.0, 148.0), abs=0.1)

    def test_booklet_fit_to_cell_fills(self, source_pdf, out_path):
        src = source_pdf(n_pages=4, w_mm=105, h_mm=148)
        p = Project(source_pdf_path=src)
        p.sheet = SheetSettings(preset="A4", orientation=Orientation.LANDSCAPE)
        p.layout = LayoutSettings(
            layout_type=LayoutType.BOOKLET, scaling=PageScaling.FIT_TO_CELL)
        w, h = first_trim(p, out_path())
        assert (w, h) == pytest.approx((141.9, 200.0), abs=0.1)


class TestPresets:
    def test_a_preset_without_the_key_loads_as_actual_size(self, source_pdf, tmp_path):
        # Presets load on top of schema defaults, so one saved before this
        # setting existed gets the new default rather than an error.
        src = source_pdf(n_pages=2)
        preset = str(tmp_path / "old.json")
        save_preset(preset, {"layout.nup": 4, "sheet.preset": "A4"})
        project = build_project(src, preset_path=preset)
        assert project.layout.scaling is PageScaling.ACTUAL_SIZE
        assert project.layout.nup == 4

    def test_a_preset_with_the_key_round_trips(self, source_pdf, tmp_path):
        src = source_pdf(n_pages=2)
        preset = str(tmp_path / "fit.json")
        save_preset(preset, {"layout.scaling": PageScaling.FIT_TO_CELL})
        project = build_project(src, preset_path=preset)
        assert project.layout.scaling is PageScaling.FIT_TO_CELL


class TestCLI:
    def test_set_fit_to_cell_produces_the_fit_result(self, source_pdf, out_path, capsys):
        # The CLI coerces --set by the default's type; the enum is matched by
        # member name or value, exactly. (The task file's shorthand "fit" does
        # not and cannot match without a CLI change — see the task report.)
        src = source_pdf(n_pages=1, w_mm=90, h_mm=50)
        out = out_path("cli_fit.pdf")
        rc = cli_main(["impose", src, "-o", out, "--set", "layout.scaling=fit_to_cell"])
        assert rc == 0, capsys.readouterr().out + capsys.readouterr().err

        page = fitz.open(out)[0]
        # In 2-Up, cell 1 has the real page and cell 2 has the placeholder simulating it.
        # Check that the placed card in cell 1 was scaled to fit the cell.
        card1_rects = [d["rect"] for d in page.get_drawings() if d["rect"].x1 <= page.rect.width / 2 + 10]
        xs0 = [r.x0 for r in card1_rects]
        ys0 = [r.y0 for r in card1_rects]
        xs1 = [r.x1 for r in card1_rects]
        ys1 = [r.y1 for r in card1_rects]
        bbox = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1))
        assert (mm(bbox.width), mm(bbox.height)) == pytest.approx((205.0, 113.9), abs=0.1)

    def test_the_default_via_the_cli_is_actual_size(self, source_pdf, out_path):
        src = source_pdf(n_pages=1, w_mm=90, h_mm=50)
        out = out_path("cli_actual.pdf")
        rc = cli_main(["impose", src, "-o", out])
        assert rc == 0

        page = fitz.open(out)[0]
        # In 2-Up, cell 1 has the real page and cell 2 has the placeholder simulating it.
        # Check that card 1 printed at its actual size (90x50 mm).
        card1_rects = [d["rect"] for d in page.get_drawings() if d["rect"].x1 <= page.rect.width / 2 + 10]
        xs0 = [r.x0 for r in card1_rects]
        ys0 = [r.y0 for r in card1_rects]
        xs1 = [r.x1 for r in card1_rects]
        ys1 = [r.y1 for r in card1_rects]
        bbox = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1))
        assert (mm(bbox.width), mm(bbox.height)) == pytest.approx((90.0, 50.0), abs=0.1)

    def test_coercion_spellings(self):
        d = defaults()
        assert coerce_setting("layout.scaling", "fit_to_cell", d) is PageScaling.FIT_TO_CELL
        assert coerce_setting("layout.scaling", "FIT_TO_CELL", d) is PageScaling.FIT_TO_CELL
        assert coerce_setting("layout.scaling", "Fit to cell", d) is PageScaling.FIT_TO_CELL
        assert coerce_setting("layout.scaling", "actual_size", d) is PageScaling.ACTUAL_SIZE


class TestPreflightScaling:
    def test_enlargement_warning_only_under_fit_to_cell(self, source_pdf):
        # A 90x50 mm card in a 100x71.75 mm cell: under FIT_TO_CELL it is enlarged to 111%,
        # triggering the enlargement warning. Under ACTUAL_SIZE it prints at 100% so no
        # enlargement is reported.
        src = source_pdf(n_pages=1, w_mm=90, h_mm=50)
        doc = fitz.open(src)
        try:
            p_actual = a_card_project(src, PageScaling.ACTUAL_SIZE)
            findings_actual = preflight(p_actual, doc)
            enlarged_actual = [f for f in findings_actual if "enlarged" in f.message.lower()]
            assert not enlarged_actual

            p_fit = a_card_project(src, PageScaling.FIT_TO_CELL)
            findings_fit = preflight(p_fit, doc)
            enlarged_fit = [f for f in findings_fit if "enlarged" in f.message.lower()]
            assert len(enlarged_fit) == 1
            assert enlarged_fit[0].severity == Severity.WARNING
            assert "111%" in enlarged_fit[0].message
        finally:
            doc.close()

    def test_shrink_reporting_preserved_under_actual_size(self, source_pdf):
        # An A3 page (297x420) on an A4 sheet (200x287 cell) must shrink in both modes,
        # so preflight reports the scale reduction under ACTUAL_SIZE.
        src = source_pdf(n_pages=1, w_mm=297, h_mm=420)
        doc = fitz.open(src)
        try:
            p = a_card_project(src, PageScaling.ACTUAL_SIZE, nup=1)
            findings = preflight(p, doc)
            scaled = [f for f in findings if "scaled" in f.message.lower()]
            assert len(scaled) == 1
            assert scaled[0].severity == Severity.WARNING
        finally:
            doc.close()
