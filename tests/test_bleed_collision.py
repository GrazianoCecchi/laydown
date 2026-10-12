"""
Bleed collision — bleed and creep must stay off the neighbour (LD-BLEED-1).

Bleed is ink printed past the trim so a slightly-off cut still hits colour. It
is welcome wherever it lands on bare paper, but never over a neighbouring
page's live area or across a booklet fold: the neighbour is cut there, so the
stray ink would end up on the finished piece.

These tests render real impositions and sample pixels, because vector
inspection ignores clipping (see docs/ai/GOTCHAS.md). Sources are built with
distinct solid colours per page so a sample identifies whose ink it hit.
"""

import fitz

from laydown.engine.data_model import (
    LayoutSettings,
    LayoutType,
    Orientation,
    Project,
    SheetSettings,
    SourceBox,
    SourceSettings,
)
from laydown.engine.impose import impose_detailed
from laydown.engine.utils import mm_to_pt

MM = mm_to_pt(1.0)  # one millimetre in points

# Pure 0/1 colours only, so a rendered byte is exactly round(c * 255).
RED = (1, 0, 0)
BLUE = (0, 0, 1)
GREEN = (0, 1, 0)
YELLOW = (1, 1, 0)
CYAN = (0, 1, 1)
MAGENTA = (1, 0, 1)
PALETTE = [RED, BLUE, GREEN, YELLOW, CYAN, MAGENTA]


def colour_source(path, n_pages, w_mm, h_mm, bleed_mm, bands=None):
    """
    Write a source whose pages are solid colour out to the bleed edge.

    Page *i* gets ``PALETTE[(i - 1) % len(PALETTE)]``. With *bands* — a
    (top, right, bottom, left) tuple of colours — the four bleed margins are
    painted in those colours instead, so a rotated placement can be checked
    for which source side its bleed came from.
    """
    doc = fitz.open()
    bleed = mm_to_pt(bleed_mm)
    w, h = mm_to_pt(w_mm) + 2 * bleed, mm_to_pt(h_mm) + 2 * bleed
    for i in range(1, n_pages + 1):
        colour = PALETTE[(i - 1) % len(PALETTE)]
        page = doc.new_page(width=w, height=h)
        trim = fitz.Rect(bleed, bleed, w - bleed, h - bleed)
        if bands is None:
            page.draw_rect(
                fitz.Rect(trim.x0 - bleed, trim.y0 - bleed,
                          trim.x1 + bleed, trim.y1 + bleed),
                color=None, fill=colour)
        else:
            top, right, bottom, left = bands
            page.draw_rect(fitz.Rect(trim.x0 - bleed, trim.y0 - bleed,
                                     trim.x1 + bleed, trim.y0), color=None, fill=top)
            page.draw_rect(fitz.Rect(trim.x0 - bleed, trim.y1,
                                     trim.x1 + bleed, trim.y1 + bleed), color=None, fill=bottom)
            page.draw_rect(fitz.Rect(trim.x0 - bleed, trim.y0,
                                     trim.x0, trim.y1), color=None, fill=left)
            page.draw_rect(fitz.Rect(trim.x1, trim.y0,
                                     trim.x1 + bleed, trim.y1), color=None, fill=right)
        # With bands, the trim interior gets a colour no band uses, so a
        # sample can tell trim ink from bleed ink.
        page.draw_rect(trim, color=None, fill=MAGENTA if bands else colour)
        page.set_trimbox(trim)
        # Clamped to the page: trim + bleed can overshoot the media box by an
        # ulp, and PyMuPDF rejects a box that is not inside it.
        page.set_bleedbox(fitz.Rect(trim.x0 - bleed, trim.y0 - bleed,
                                    trim.x1 + bleed, trim.y1 + bleed) & page.rect)
    doc.save(path)
    doc.close()
    return path


def a_project(src, *, layout, bleed=3.0, preset="A4",
              orientation=Orientation.LANDSCAPE, margin=0.0):
    project = Project(source_pdf_path=src)
    project.source = SourceSettings(box=SourceBox.TRIM, bleed_mm=bleed)
    project.sheet = SheetSettings(
        preset=preset, orientation=orientation,
        margin_left_mm=margin, margin_right_mm=margin,
        margin_top_mm=margin, margin_bottom_mm=margin,
    )
    project.layout = layout
    return project


def assert_pixel(page, x_pt, y_pt, colour, what):
    """Sample the rendered *page* at (x, y) points and demand *colour*."""
    dpi = 144
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    s = dpi / 72.0
    got = pix.pixel(int(x_pt * s), int(y_pt * s))[:3]
    want = tuple(round(c * 255) for c in colour)
    assert got == want, f"{what}: sampled {got}, expected {want}"


def page_colour(page_number):
    return PALETTE[(page_number - 1) % len(PALETTE)]


class TestNUpBleed:
    def test_zero_gutter_leaves_each_trim_its_own_colour(self, tmp_path):
        # (a) 2-up, 3 mm bleed, no gutter: the shared edge has no room for any
        # bleed, so 1 mm inside either trim must be that page's own colour.
        src = colour_source(str(tmp_path / "src.pdf"), 2, 148, 210, 3.0)
        project = a_project(src, layout=LayoutSettings(nup=2, gutter_h_mm=0.0))
        result = impose_detailed(project, str(tmp_path / "out.pdf"))

        page = fitz.open(str(tmp_path / "out.pdf"))[0]
        left, right = result.placed[0][0], result.placed[0][1]
        y = (left.trim.y0 + left.trim.y1) / 2
        assert_pixel(page, left.trim.x1 - 1 * MM, y, page_colour(left.page_number),
                     "1 mm inside the left page's trim on the shared edge")
        assert_pixel(page, right.trim.x0 + 1 * MM, y, page_colour(right.page_number),
                     "1 mm inside the right page's trim on the shared edge")

    def test_gutter_splits_the_bleed_between_the_neighbours(self, tmp_path):
        # (b) 2-up, 3 mm bleed, 4 mm gutter: each side may bleed half the
        # gutter (2 mm) — present, but no further.
        src = colour_source(str(tmp_path / "src.pdf"), 2, 148, 210, 3.0)
        project = a_project(src, layout=LayoutSettings(nup=2, gutter_h_mm=4.0))
        result = impose_detailed(project, str(tmp_path / "out.pdf"))

        page = fitz.open(str(tmp_path / "out.pdf"))[0]
        left, right = result.placed[0][0], result.placed[0][1]
        y = (left.trim.y0 + left.trim.y1) / 2
        assert_pixel(page, left.trim.x1 - 1 * MM, y, page_colour(left.page_number),
                     "1 mm inside the left page's trim")
        assert_pixel(page, right.trim.x0 + 1 * MM, y, page_colour(right.page_number),
                     "1 mm inside the right page's trim")
        assert_pixel(page, left.trim.x1 + 1.5 * MM, y, page_colour(left.page_number),
                     "the left page's bleed must be present in the gutter")
        assert_pixel(page, right.trim.x0 - 1.5 * MM, y, page_colour(right.page_number),
                     "the right page's bleed must be present in the gutter")
        # 2.5 mm out is past the 2 mm cap: it belongs to the facing page's
        # bleed, so an uncapped 3 mm bleed shows up here as the wrong colour.
        assert_pixel(page, left.trim.x1 + 2.5 * MM, y, page_colour(right.page_number),
                     "the left page's bleed must stop at half the gutter")
        assert_pixel(page, right.trim.x0 - 2.5 * MM, y, page_colour(left.page_number),
                     "the right page's bleed must stop at half the gutter")


class TestBookletBleed:
    def test_spine_bleed_never_crosses_the_fold(self, tmp_path):
        # (c) A5 on A4 booklet, 3 mm bleed, no gutter: the fold side gets no
        # bleed at all, so 1.5 mm inside each spine is that page's own colour.
        src = colour_source(str(tmp_path / "src.pdf"), 4, 148, 210, 3.0)
        project = a_project(
            src, layout=LayoutSettings(layout_type=LayoutType.BOOKLET, gutter_h_mm=0.0))
        result = impose_detailed(project, str(tmp_path / "out.pdf"))

        page = fitz.open(str(tmp_path / "out.pdf"))[0]
        left, right = result.placed[0][0], result.placed[0][1]
        y = (left.trim.y0 + left.trim.y1) / 2
        assert_pixel(page, left.trim.x1 - 1.5 * MM, y, page_colour(left.page_number),
                     "1.5 mm inside the left page's spine")
        assert_pixel(page, right.trim.x0 + 1.5 * MM, y, page_colour(right.page_number),
                     "1.5 mm inside the right page's spine")

    def test_creep_never_prints_across_the_fold(self, tmp_path):
        # (d) 8 mm inner creep pushes the innermost trims 7.75 mm past the
        # fold. The drawn area must stop at the fold — content that would
        # cross is lost, as on press — while the trim rects keep the shift.
        src = colour_source(str(tmp_path / "src.pdf"), 8, 148, 210, 3.0)
        project = a_project(src, layout=LayoutSettings(
            layout_type=LayoutType.BOOKLET, gutter_h_mm=0.0,
            creep_enabled=True, creep_outer_mm=0.0, creep_inner_mm=8.0))
        result = impose_detailed(project, str(tmp_path / "out.pdf"))

        page = fitz.open(str(tmp_path / "out.pdf"))[-1]
        left, right = result.placed[-1][0], result.placed[-1][1]
        fold_x = left.trim.x1 + (right.trim.x0 - left.trim.x1) / 2  # gutter 0
        y = (left.trim.y0 + left.trim.y1) / 2

        # The creep shift itself is unchanged: the trims still overlap the fold.
        assert left.trim.x1 > fold_x and right.trim.x0 < fold_x, (
            "creep must keep shifting the trims towards the spine"
        )
        for d in (0.5, 1.0, 1.5, 2.0):
            assert_pixel(page, fold_x - d * MM, y, page_colour(left.page_number),
                         f"{d} mm short of the fold must be the left page's ink")
            assert_pixel(page, fold_x + d * MM, y, page_colour(right.page_number),
                         f"{d} mm past the fold must be the right page's ink")


class TestRotatedBleed:
    def test_auto_rotation_keeps_the_bleed_sides_and_their_caps(self, tmp_path):
        # (e) A landscape page auto-rotated into portrait cells: the bleed
        # margins must travel with the quarter turn (source top -> sheet
        # left, right -> top, bottom -> right, left -> bottom), and the cap
        # applies to the sheet side, not the source side.
        bands = (RED, YELLOW, BLUE, GREEN)  # top, right, bottom, left
        src = colour_source(str(tmp_path / "src.pdf"), 2, 200, 100, 5.0, bands=bands)
        project = a_project(
            src, bleed=5.0, preset="A4", orientation=Orientation.PORTRAIT, margin=5.0,
            layout=LayoutSettings(nup=2, gutter_h_mm=6.0, auto_rotate=True))
        result = impose_detailed(project, str(tmp_path / "out.pdf"))

        page = fitz.open(str(tmp_path / "out.pdf"))[0]
        trim = result.placed[0][0].trim
        mid_x = (trim.x0 + trim.x1) / 2
        mid_y = (trim.y0 + trim.y1) / 2

        assert_pixel(page, mid_x, mid_y, MAGENTA, "the trim itself")
        assert_pixel(page, trim.x0 - 2 * MM, mid_y, RED,
                     "sheet left must show the source's top bleed band")
        assert_pixel(page, mid_x, trim.y0 - 2 * MM, YELLOW,
                     "sheet top must show the source's right bleed band")
        assert_pixel(page, mid_x, trim.y1 + 2 * MM, GREEN,
                     "sheet bottom must show the source's left bleed band")
        # The sheet-right side faces the neighbour across a 6 mm gutter, so
        # its bleed (the source's bottom band) is capped at 3 mm — present,
        # but the facing page's bleed owns the rest of the gutter.
        assert_pixel(page, trim.x1 + 1.5 * MM, mid_y, BLUE,
                     "the capped side keeps its half-gutter of bleed")
        assert_pixel(page, trim.x1 + 3.5 * MM, mid_y, RED,
                     "past the cap the gutter belongs to the facing page's bleed")
