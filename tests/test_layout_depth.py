"""
Layout depth (ROADMAP.md Phase 5) — the features the Layout tab used to collect
and the engine used to ignore: arbitrary N-Up grids, auto-rotate, right-to-left,
filler placement, signatures/perfect binding, and creep.
"""

import fitz
import pytest

from laydown.engine.data_model import (
    Project, LayoutSettings, LayoutType, SheetSettings, Orientation, BookletMode,
    PageScaling, RepeatMode,
)
from laydown.engine.geometry import (
    booklet_page_order, grid_for, padded_slots, signature_groups, creep_shift_pt,
)
from laydown.engine.impose import impose, impose_detailed
from laydown.engine.utils import mm_to_pt

from tests.helpers import page_token, corner_token, word_centers


def a_project(src, *, preset="A3", orientation=Orientation.LANDSCAPE, **layout_kw):
    p = Project(source_pdf_path=src)
    p.sheet = SheetSettings(preset=preset, orientation=orientation)
    p.layout = LayoutSettings(**layout_kw)
    return p


def booklet_layout(**kw):
    return LayoutSettings(layout_type=LayoutType.BOOKLET, **kw)


# ── N-Up grids ───────────────────────────────────────

class TestNUpGrids:
    @pytest.mark.parametrize("nup,expected", [
        (1, (1, 1)), (2, (2, 1)), (4, (2, 2)), (6, (3, 2)), (8, (4, 2)), (9, (3, 3)),
        (10, (2, 5)), (12, (3, 4)), (16, (4, 4)), (20, (4, 5)), (24, (4, 6)), (25, (5, 5)), (32, (4, 8)),
    ])
    def test_named_grids(self, nup, expected):
        assert grid_for(LayoutSettings(nup=nup)) == expected

    def test_explicit_rows_and_cols_win(self):
        assert grid_for(LayoutSettings(nup=2, rows=3, cols=5)) == (5, 3)

    def test_custom_grid_with_nup_zero(self):
        assert grid_for(LayoutSettings(nup=0, rows=4, cols=5)) == (5, 4)

    def test_unknown_nup_is_rejected(self):
        with pytest.raises(ValueError, match="nup must be"):
            grid_for(LayoutSettings(nup=7))

    def test_nine_up_places_nine_pages_on_one_sheet(self, source_pdf, out_path):
        src = source_pdf(n_pages=9)
        out = out_path()
        assert impose(a_project(src, nup=9), out) == 1
        found = word_centers(fitz.open(out)[0])
        for i in range(1, 10):
            assert page_token(i) in found

    def test_explicit_grid_places_in_reading_order(self, source_pdf, out_path):
        src = source_pdf(n_pages=6)
        out = out_path()
        assert impose(a_project(src, rows=2, cols=3), out) == 1
        found = word_centers(fitz.open(out)[0])
        # row-major: 1 2 3 / 4 5 6
        assert found[page_token(1)].x < found[page_token(2)].x < found[page_token(3)].x
        assert found[page_token(1)].y < found[page_token(4)].y
        assert found[page_token(4)].x < found[page_token(5)].x < found[page_token(6)].x

    def test_grid_spills_onto_more_sheets(self, source_pdf, out_path):
        src = source_pdf(n_pages=10)
        assert impose(a_project(src, nup=4), out_path()) == 3


# ── auto-rotate ──────────────────────────────────────

class TestAutoRotate:
    def _placed_trim(self, src, auto_rotate, out_path):
        project = a_project(
            src, preset="A4", orientation=Orientation.PORTRAIT,
            nup=2, auto_rotate=auto_rotate,
        )
        return impose_detailed(project, out_path()).placed[0][0].trim

    def test_landscape_page_is_turned_for_a_tall_cell(self, source_pdf, out_path):
        # A4-portrait sheet 2-up gives narrow, tall cells; a landscape source fits
        # far better on its side.
        src = source_pdf(n_pages=2, w_mm=297, h_mm=210)
        upright = self._placed_trim(src, False, out_path)
        turned = self._placed_trim(src, True, out_path)

        assert upright.width > upright.height, "fixture should be landscape"
        assert turned.height > turned.width, "auto-rotate did not turn the page"
        assert turned.get_area() > upright.get_area() * 1.5, (
            "the turned page should fill much more of its cell"
        )

    def test_auto_rotate_leaves_a_page_alone_when_it_already_fits_better(self, source_pdf, out_path):
        src = source_pdf(n_pages=2, w_mm=210, h_mm=297)
        upright = self._placed_trim(src, False, out_path)
        turned = self._placed_trim(src, True, out_path)
        assert (turned.width, turned.height) == pytest.approx((upright.width, upright.height))

    def test_rotated_bleed_margins_travel_with_the_page(self):
        # Asymmetric bleed under rotation: the margins must rotate with the artwork,
        # or the target no longer matches the clip's shape and the page is stretched.
        # Symmetric bleed hides this — it looks identical whichever way the mapping goes.
        from laydown.engine.geometry import place_page
        cell = fitz.Rect(0, 0, 400, 200)        # wide cell, so turning genuinely wins
        place = fitz.Rect(20, 5, 120, 205)      # 100 × 200, tall
        clip = fitz.Rect(0, 0, 130, 210)        # left 20, top 5, right 10, bottom 5
        target, trim, rotate = place_page(
            cell, place, clip, allow_rotate=True, scaling=PageScaling.FIT_TO_CELL)

        assert rotate == 90, "a tall page in a wide cell should turn"
        assert target.width / target.height == pytest.approx(clip.height / clip.width), (
            "target must keep the rotated clip's proportions, or the page is distorted"
        )
        scale = trim.width / place.height
        assert trim.x0 - target.x0 == pytest.approx(5 * scale)    # source top -> sheet left
        assert target.y1 - trim.y1 == pytest.approx(20 * scale)   # source left -> sheet bottom

    def test_rotation_actually_moves_the_content(self, source_pdf, out_path):
        src = source_pdf(n_pages=2, w_mm=297, h_mm=210)
        out = out_path()
        impose(a_project(src, preset="A4", orientation=Orientation.PORTRAIT,
                         nup=2, auto_rotate=True), out)
        found = word_centers(fitz.open(out)[0])
        # The quarter turn puts the source's top edge down the left of the sheet: the
        # top-left corner lands bottom-left and the top-right corner lands top-left.
        # In the source TL/TR share a y and TL/BL share an x, so both checks discriminate.
        assert found[corner_token(1, "TL")].y > found[corner_token(1, "TR")].y
        assert found[corner_token(1, "TL")].x < found[corner_token(1, "BL")].x


# ── padding and filler placement ─────────────────────

class TestFillers:
    def test_padding_goes_to_the_end_by_default(self):
        assert padded_slots(6) == [0, 1, 2, 3, 4, 5, -1, -1]

    def test_fillers_in_the_middle_keep_the_back_cover(self):
        assert padded_slots(6, fillers_in_middle=True) == [0, 1, 2, -1, -1, 3, 4, 5]

    def test_no_padding_when_already_a_multiple_of_four(self):
        assert padded_slots(8) == list(range(8))
        assert padded_slots(8, fillers_in_middle=True) == list(range(8))

    def test_fillers_in_the_middle_change_the_printed_order(self):
        default = booklet_page_order(6, booklet_layout())
        middle = booklet_page_order(6, booklet_layout(fillers_in_middle=True))
        assert default != middle
        # Either way every real page is still imposed exactly once.
        for order in (default, middle):
            flat = sorted(p for pair in order for p in pair if p >= 0)
            assert flat == list(range(6))

    def test_fillers_in_the_middle_put_the_blank_side_innermost(self):
        order = booklet_page_order(6, booklet_layout(fillers_in_middle=True))
        assert order[-1] == (-1, -1), "the blanks should end up on the centre spread"

    # An odd blank in the centre would push every later page onto the other side of
    # the spread (left-hand pages printing as right-hand ones). Pairs go in the
    # centre; the odd one sits just before the back cover.
    def test_one_blank_goes_before_the_back_cover(self):
        assert padded_slots(7, fillers_in_middle=True) == [0, 1, 2, 3, 4, 5, -1, 6]

    def test_three_blanks_pair_in_the_middle_and_one_before_the_back_cover(self):
        assert padded_slots(5, fillers_in_middle=True) == [0, 1, -1, -1, 2, 3, -1, 4]

    def test_even_blanks_still_all_go_in_the_middle(self):
        assert padded_slots(6, fillers_in_middle=True) == [0, 1, 2, -1, -1, 3, 4, 5]
        assert padded_slots(10, fillers_in_middle=True) == [
            0, 1, 2, 3, 4, -1, -1, 5, 6, 7, 8, 9]

    def test_odd_blanks_at_the_end_by_default_are_unchanged(self):
        assert padded_slots(7) == [0, 1, 2, 3, 4, 5, 6, -1]
        assert padded_slots(5) == [0, 1, 2, 3, 4, -1, -1, -1]

    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_tiny_documents_keep_the_front_cover_first(self, n):
        slots = padded_slots(n, fillers_in_middle=True)
        assert len(slots) == 4
        assert slots[0] == 0
        assert sorted(p for p in slots if p >= 0) == list(range(n))

    @pytest.mark.parametrize("n", range(2, 30))
    def test_every_interior_page_keeps_its_side(self, n):
        slots = padded_slots(n, fillers_in_middle=True)
        assert len(slots) % 4 == 0
        assert slots[0] == 0 and slots[-1] == n - 1, "front and back covers stay outside"
        for page in range(1, n - 1):
            assert slots.index(page) % 2 == page % 2, f"page {page} changed side"

    @pytest.mark.parametrize("n", [5, 6, 7, 9, 11])
    def test_booklet_order_still_pairs_correctly(self, n):
        slots = padded_slots(n, fillers_in_middle=True)
        total = len(slots)
        order = booklet_page_order(n, booklet_layout(fillers_in_middle=True))
        assert len(order) == total // 2
        flat = sorted(p for pair in order for p in pair if p >= 0)
        assert flat == list(range(n))
        # Covers share the outermost side, and facing slots always sum to total - 1.
        assert order[0] == (n - 1, 0)
        for left, right in order:
            if left >= 0 and right >= 0:
                assert slots.index(left) + slots.index(right) == total - 1


# ── right to left ────────────────────────────────────

class TestRightToLeft:
    def test_rtl_swaps_every_side(self):
        ltr = booklet_page_order(8, booklet_layout())
        rtl = booklet_page_order(8, booklet_layout(right_to_left=True))
        assert rtl == [(r, l) for l, r in ltr]

    def test_rtl_puts_the_first_page_on_the_left(self):
        rtl = booklet_page_order(8, booklet_layout(right_to_left=True))
        assert rtl[0] == (0, 7)

    def test_rtl_still_imposes_every_page_once(self):
        flat = sorted(p for pair in booklet_page_order(12, booklet_layout(right_to_left=True))
                      for p in pair if p >= 0)
        assert flat == list(range(12))

    def test_rtl_reaches_the_sheet(self, source_pdf, out_path):
        src = source_pdf(n_pages=8)
        out = out_path()
        project = a_project(src)
        project.layout = booklet_layout(right_to_left=True)
        impose(project, out)
        found = word_centers(fitz.open(out)[0])
        assert found[page_token(1)].x < found[page_token(8)].x


# ── signatures / perfect binding ─────────────────────

class TestSignatures:
    def test_saddle_stitch_is_one_signature(self):
        assert len(signature_groups(16, booklet_layout())) == 1

    def test_perfect_binding_splits_into_gathered_signatures(self):
        groups = signature_groups(16, booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=2))
        assert groups == [list(range(8)), list(range(8, 16))]

    def test_each_signature_is_nested_on_its_own(self):
        order = booklet_page_order(16, booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=2))
        assert order == [
            (7, 0), (1, 6), (5, 2), (3, 4),        # first gathered section
            (15, 8), (9, 14), (13, 10), (11, 12),  # second
        ]

    def test_saddle_stitch_nests_the_whole_document_instead(self):
        order = booklet_page_order(16, booklet_layout())
        assert order[0] == (15, 0), "saddle stitch wraps the last page around the first"

    def test_facing_pages_sum_to_a_constant_within_each_signature(self):
        order = booklet_page_order(16, booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=2))
        for sig in (order[:4], order[4:]):
            sums = {l + r for l, r in sig}
            assert len(sums) == 1, "a signature must still fold into reading order"

    def test_a_short_last_signature_is_padded(self):
        groups = signature_groups(10, booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=1))
        assert all(len(g) % 4 == 0 for g in groups)
        flat = sorted(p for g in groups for p in g if p >= 0)
        assert flat == list(range(10))

    def test_perfect_binding_imposes_every_page_once(self):
        order = booklet_page_order(20, booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=2))
        flat = sorted(p for pair in order for p in pair if p >= 0)
        assert flat == list(range(20))

    def test_perfect_bound_output_has_the_expected_sheet_count(self, source_pdf, out_path):
        src = source_pdf(n_pages=16)
        project = a_project(src)
        project.layout = booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=2)
        # 16 pages = 4 sheets = 8 sides, whichever way they are grouped.
        assert impose(project, out_path()) == 8

    def test_sheet_numbering_runs_across_signatures(self, source_pdf, out_path):
        src = source_pdf(n_pages=16)
        project = a_project(src)
        project.layout = booklet_layout(
            booklet_mode=BookletMode.PERFECT_BOUND, signature_sheets=2)
        from laydown.engine.geometry import sheet_plan
        plan = sheet_plan(project, list(range(16)))
        assert [s.number for s in plan] == [1, 1, 2, 2, 3, 3, 4, 4]
        assert all(s.total == 4 for s in plan)


# ── creep ────────────────────────────────────────────

class TestCreep:
    def test_disabled_creep_shifts_nothing(self):
        assert creep_shift_pt(booklet_layout(creep_inner_mm=5), 3, 4) == 0.0

    def test_creep_interpolates_from_outer_to_inner(self):
        layout = booklet_layout(creep_enabled=True, creep_outer_mm=0.0, creep_inner_mm=3.0)
        assert creep_shift_pt(layout, 0, 4) == pytest.approx(0.0)
        assert creep_shift_pt(layout, 3, 4) == pytest.approx(mm_to_pt(3.0))
        assert creep_shift_pt(layout, 1, 4) == pytest.approx(mm_to_pt(1.0))
        assert creep_shift_pt(layout, 2, 4) == pytest.approx(mm_to_pt(2.0))

    def test_single_sheet_signature_uses_the_inner_value(self):
        layout = booklet_layout(creep_enabled=True, creep_outer_mm=0.0, creep_inner_mm=2.0)
        assert creep_shift_pt(layout, 0, 1) == pytest.approx(mm_to_pt(2.0))

    def test_creep_moves_inner_pages_towards_the_spine(self, source_pdf, out_path):
        src = source_pdf(n_pages=16)
        project = a_project(src)
        project.layout = booklet_layout(
            creep_enabled=True, creep_outer_mm=0.0, creep_inner_mm=3.0)
        placed = impose_detailed(project, out_path()).placed

        outer_left, outer_right = placed[0][0].trim, placed[0][1].trim
        inner_left, inner_right = placed[6][0].trim, placed[6][1].trim

        # Left page's spine is on its right, so it moves right; the right page moves left.
        assert inner_left.x0 - outer_left.x0 == pytest.approx(mm_to_pt(3.0), abs=0.5)
        assert inner_right.x0 - outer_right.x0 == pytest.approx(-mm_to_pt(3.0), abs=0.5)

    def test_creep_does_not_move_pages_vertically(self, source_pdf, out_path):
        src = source_pdf(n_pages=16)
        project = a_project(src)
        project.layout = booklet_layout(
            creep_enabled=True, creep_outer_mm=0.0, creep_inner_mm=3.0)
        placed = impose_detailed(project, out_path()).placed
        assert placed[6][0].trim.y0 == pytest.approx(placed[0][0].trim.y0)

    def test_creep_off_keeps_every_sheet_aligned(self, source_pdf, out_path):
        src = source_pdf(n_pages=16)
        project = a_project(src)
        project.layout = booklet_layout(creep_enabled=False, creep_inner_mm=3.0)
        placed = impose_detailed(project, out_path()).placed
        assert placed[6][0].trim.x0 == pytest.approx(placed[0][0].trim.x0)


# ── Step & Repeat / Duplication ───────────────────────

class TestStepAndRepeat:
    def test_fill_sheet_replicates_single_page_to_all_cells(self, source_pdf, out_path):
        """A 1-page business card under 4-Up with FILL_SHEET fills all 4 cells."""
        src = source_pdf(n_pages=1)
        project = a_project(
            src, nup=4,
            repeat_mode=RepeatMode.FILL_SHEET,
        )
        res = impose_detailed(project, out_path())
        assert res.sheets == 1
        assert len(res.placed[0]) == 4
        # Every placement on the sheet points to source page 1
        for placed_page in res.placed[0]:
            assert placed_page.page_number == 1

    def test_fill_sheet_replicates_each_page_to_its_own_full_sheet(self, source_pdf, out_path):
        """A 2-page document (front and back) under 4-Up fills sheet 1 with page 1, sheet 2 with page 2."""
        src = source_pdf(n_pages=2)
        project = a_project(
            src, nup=4,
            repeat_mode=RepeatMode.FILL_SHEET,
        )
        res = impose_detailed(project, out_path())
        assert res.sheets == 2
        # Sheet 1: 4 copies of page 1
        assert len(res.placed[0]) == 4
        for p in res.placed[0]:
            assert p.page_number == 1
        # Sheet 2: 4 copies of page 2
        assert len(res.placed[1]) == 4
        for p in res.placed[1]:
            assert p.page_number == 2

    def test_custom_copies_replicates_specified_count(self, source_pdf, out_path):
        """Setting COPIES with repeat_count=5 in a 4-Up produces 2 sheets: 4 on sheet 1, 1 on sheet 2."""
        src = source_pdf(n_pages=1)
        project = a_project(
            src, nup=4,
            repeat_mode=RepeatMode.COPIES,
            repeat_count=5,
        )
        res = impose_detailed(project, out_path())
        assert res.sheets == 2
        assert len(res.placed[0]) == 4
        assert len(res.placed[1]) == 1
        assert res.placed[1][0].page_number == 1

    def test_fill_sheet_with_business_card_grid_20up(self, source_pdf, out_path):
        """20-Up grid with FILL_SHEET produces exactly 20 copies on 1 sheet."""
        src = source_pdf(n_pages=1)
        project = a_project(
            src, nup=20,
            repeat_mode=RepeatMode.FILL_SHEET,
        )
        res = impose_detailed(project, out_path())
        assert res.sheets == 1
        assert len(res.placed[0]) == 20
        for p in res.placed[0]:
            assert p.page_number == 1

    def test_step_and_repeat_adjacent_cells_touch_when_gutters_zero(self, source_pdf, out_path):
        """When gutters are zero, adjacent cells touch edge-to-edge for single-cut trimming."""
        src = source_pdf(n_pages=1, w_mm=85.0, h_mm=55.0)
        project = a_project(
            src, cols=4, rows=5,
            repeat_mode=RepeatMode.FILL_SHEET,
            gutter_h_mm=0.0,
            gutter_v_mm=0.0,
        )
        res = impose_detailed(project, out_path())
        placed = res.placed[0]
        # Check horizontal adjacency: col 0 right edge equals col 1 left edge
        for r in range(5):
            for c in range(3):
                idx = r * 4 + c
                next_idx = idx + 1
                assert placed[idx].trim.x1 == pytest.approx(placed[next_idx].trim.x0, abs=0.01)
        # Check vertical adjacency: row 0 bottom edge equals row 1 top edge
        for r in range(4):
            for c in range(4):
                idx = r * 4 + c
                below_idx = (r + 1) * 4 + c
                assert placed[idx].trim.y1 == pytest.approx(placed[below_idx].trim.y0, abs=0.01)

    def test_step_and_repeat_gutter_spacing_matches_settings(self, source_pdf, out_path):
        """Distance between adjacent cells matches gutter_h_mm and gutter_v_mm exactly."""
        src = source_pdf(n_pages=1, w_mm=85.0, h_mm=55.0)
        project = a_project(
            src, cols=3, rows=3,
            repeat_mode=RepeatMode.FILL_SHEET,
            gutter_h_mm=4.0,
            gutter_v_mm=2.5,
        )
        res = impose_detailed(project, out_path())
        placed = res.placed[0]
        # Horizontal gap
        gap_h = placed[1].trim.x0 - placed[0].trim.x1
        assert gap_h == pytest.approx(mm_to_pt(4.0), abs=0.01)
        # Vertical gap
        gap_v = placed[3].trim.y0 - placed[0].trim.y1
        assert gap_v == pytest.approx(mm_to_pt(2.5), abs=0.01)

    def test_step_and_repeat_centered_on_sheet_minus_margins(self, source_pdf, out_path):
        """The entire placed grid is centered within sheet boundaries minus margins."""
        src = source_pdf(n_pages=1, w_mm=85.0, h_mm=55.0)
        project = a_project(
            src, cols=4, rows=5,
            repeat_mode=RepeatMode.FILL_SHEET,
            gutter_h_mm=2.0,
            gutter_v_mm=2.0,
        )
        project.sheet.margin_left_mm = 20.0
        project.sheet.margin_right_mm = 10.0
        project.sheet.margin_top_mm = 15.0
        project.sheet.margin_bottom_mm = 5.0

        from laydown.engine.geometry import sheet_size_pt, margins_pt
        sheet_w, sheet_h = sheet_size_pt(project)
        ml, mr, mt, mb = margins_pt(project)

        res = impose_detailed(project, out_path())
        placed = res.placed[0]

        min_x = min(p.trim.x0 for p in placed)
        max_x = max(p.trim.x1 for p in placed)
        min_y = min(p.trim.y0 for p in placed)
        max_y = max(p.trim.y1 for p in placed)

        left_dist = min_x - ml
        right_dist = (sheet_w - mr) - max_x
        top_dist = min_y - mt
        bottom_dist = (sheet_h - mb) - max_y

        assert left_dist == pytest.approx(right_dist, abs=0.05)
        assert top_dist == pytest.approx(bottom_dist, abs=0.05)

    def test_step_and_repeat_respects_source_box(self, tmp_path, out_path):
        """Cell dimensions and spacing adapt to the selected SourceBox (TrimBox vs MediaBox)."""
        import fitz
        from laydown.engine.data_model import SourceBox

        pdf_path = str(tmp_path / "boxes_test.pdf")
        doc = fitz.open()
        p = doc.new_page(width=mm_to_pt(100), height=mm_to_pt(70))
        # Set TrimBox smaller than MediaBox (85 x 55 mm centered inside 100 x 70 mm)
        trim_r = fitz.Rect(mm_to_pt(7.5), mm_to_pt(7.5), mm_to_pt(92.5), mm_to_pt(62.5))
        p.set_trimbox(trim_r)
        doc.save(pdf_path)
        doc.close()

        # Test with SourceBox.TRIM
        project_trim = a_project(
            pdf_path, cols=2, rows=2,
            repeat_mode=RepeatMode.FILL_SHEET,
            gutter_h_mm=0.0,
            gutter_v_mm=0.0,
        )
        project_trim.source.box = SourceBox.TRIM
        res_trim = impose_detailed(project_trim, out_path())
        t0 = res_trim.placed[0][0].trim
        assert t0.width == pytest.approx(mm_to_pt(85.0), abs=0.05)
        assert t0.height == pytest.approx(mm_to_pt(55.0), abs=0.05)

        # Test with SourceBox.MEDIA
        project_media = a_project(
            pdf_path, cols=2, rows=2,
            repeat_mode=RepeatMode.FILL_SHEET,
            gutter_h_mm=0.0,
            gutter_v_mm=0.0,
        )
        project_media.source.box = SourceBox.MEDIA
        res_media = impose_detailed(project_media, out_path())
        m0 = res_media.placed[0][0].trim
        assert m0.width == pytest.approx(mm_to_pt(100.0), abs=0.05)
        assert m0.height == pytest.approx(mm_to_pt(70.0), abs=0.05)


