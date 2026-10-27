"""
Rotation correctness — /Rotate pages and the Rotate preprocessor.

A page whose /Rotate is 90 or 270 (set by the source file or by Laydown's own
Rotate preprocessor) used to be silently cropped: PyMuPDF's box getters report
unrotated coordinates while page.rect is the rotated page, and show_pdf_page
mishandles rotated sources. These tests pin the fix — every rotated page is
imposed with ALL its artwork and its Trim/Bleed boxes intact.

Expected geometry is derived independently of the engine: the visual trim size
follows from the fixture's known shape (rotation swaps width and height), and
the sheet-side trim rect from tests.helpers.fitted_rect, which deliberately
re-derives the fit maths.
"""

import fitz
import pytest

from laydown.engine.data_model import (
    LayoutSettings,
    LayoutType,
    Orientation,
    PageScaling,
    PreprocessorStep,
    PreprocessorType,
    Project,
    RotateAngle,
    SheetSettings,
    SourceSettings,
)
from laydown.engine.geometry import cell_grid
from laydown.engine.impose import impose, impose_detailed
from laydown.engine.utils import mm_to_pt

from tests.helpers import corner_token, fitted_rect, make_source_pdf, page_token, word_centers

CORNERS = ("TL", "TR", "BL", "BR")


# ── fixtures' helpers ────────────────────────────────

def rotated_copy(src: str, dst: str, angle: int) -> str:
    """A copy of *src* with /Rotate *angle* on every page."""
    doc = fitz.open(src)
    for page in doc:
        page.set_rotation(angle)
    doc.save(dst)
    doc.close()
    return dst


def visual_trim_size(w_mm: float, h_mm: float, inset_mm: float, rotation: int):
    """(width, height) of the trim box as the page is seen, in points.

    make_source_pdf insets the trim box symmetrically by inset_mm, and a
    quarter-turn rotation swaps the visible width and height.
    """
    inset = mm_to_pt(inset_mm)
    tw, th = mm_to_pt(w_mm) - 2 * inset, mm_to_pt(h_mm) - 2 * inset
    if rotation in (90, 270):
        tw, th = th, tw
    return tw, th


def a_project(src: str, *, bleed_mm: float = 0.0, steps=None, scaling=PageScaling.FIT_TO_CELL):
    p = Project(source_pdf_path=src)
    p.source = SourceSettings(bleed_mm=bleed_mm)
    p.sheet = SheetSettings(preset="A3", orientation=Orientation.LANDSCAPE)
    p.layout = LayoutSettings(layout_type=LayoutType.NUP, nup=2, scaling=scaling)
    p.preprocessors = steps or []
    return p


def assert_page_1_intact(out_path: str, project: Project, trim_w: float, trim_h: float):
    """Page 1's centre and all four corner tokens are on sheet 1, inside the trim.

    Returns the expected trim rect on the sheet, derived without the engine's
    placement maths (helpers.fitted_rect is an independent re-derivation).
    """
    out = fitz.open(out_path)
    assert len(out) == 1, "2 pages 2-up must make exactly one sheet"
    found = word_centers(out[0])
    out.close()

    cell = cell_grid(project, 2, 1)[0]
    expected_trim = fitted_rect(cell, trim_w, trim_h)

    tokens = [page_token(1)] + [corner_token(1, c) for c in CORNERS]
    for token in tokens:
        assert token in found, f"{token} was lost from the imposed sheet"
        centre = found[token]
        assert expected_trim.x0 - 0.5 <= centre.x <= expected_trim.x1 + 0.5, (
            f"{token} at x={centre.x:.1f} is outside the trim rect {expected_trim}"
        )
        assert expected_trim.y0 - 0.5 <= centre.y <= expected_trim.y1 + 0.5, (
            f"{token} at y={centre.y:.1f} is outside the trim rect {expected_trim}"
        )
    return expected_trim


# ── /Rotate in the source file ──────────────────────

class TestRotatedSourceFile:
    @pytest.mark.parametrize("angle", [0, 90, 180, 270])
    def test_plain_a4_keeps_all_artwork(self, tmp_path, angle):
        src = make_source_pdf(str(tmp_path / "plain.pdf"), n_pages=2)
        rot = rotated_copy(src, str(tmp_path / f"rot{angle}.pdf"), angle)
        out = str(tmp_path / "out.pdf")

        project = a_project(rot)
        assert impose(project, out) == 1
        tw, th = visual_trim_size(210, 297, 0, angle)
        assert_page_1_intact(out, project, tw, th)

    @pytest.mark.parametrize("angle", [0, 90, 180, 270])
    def test_trim_box_keeps_all_artwork_and_boxes(self, tmp_path, angle):
        src = make_source_pdf(str(tmp_path / "boxed.pdf"), n_pages=2, trim_inset_mm=10)
        rot = rotated_copy(src, str(tmp_path / f"rot{angle}.pdf"), angle)
        out = str(tmp_path / "out.pdf")

        project = a_project(rot)
        assert impose(project, out) == 1
        tw, th = visual_trim_size(210, 297, 10, angle)
        assert_page_1_intact(out, project, tw, th)

    def test_rotated_trim_and_bleed_keeps_its_trim_size(self, tmp_path):
        # The placed trim rect must be the rotated page's trim, letterboxed in
        # the cell — not the cell, not the unrotated page, not a cropped square.
        src = make_source_pdf(
            str(tmp_path / "boxed.pdf"), n_pages=2, trim_inset_mm=10, bleed_mm=5)
        rot = rotated_copy(src, str(tmp_path / "rot90.pdf"), 90)
        out = str(tmp_path / "out.pdf")

        project = a_project(rot, bleed_mm=3.0)
        placed = impose_detailed(project, out).placed[0][0]
        tw, th = visual_trim_size(210, 297, 10, 90)

        cell = cell_grid(project, 2, 1)[0]
        expected = fitted_rect(cell, tw, th)
        assert (placed.trim.width, placed.trim.height) == pytest.approx(
            (expected.width, expected.height), abs=0.1)
        assert placed.trim.width / placed.trim.height == pytest.approx(tw / th)
        assert placed.trim.width > placed.trim.height, (
            "a /Rotate 90 A4 is visually landscape and must stay landscape"
        )

    def test_the_source_file_on_disk_is_untouched(self, tmp_path):
        src = make_source_pdf(str(tmp_path / "boxed.pdf"), n_pages=2, trim_inset_mm=10)
        rot = rotated_copy(src, str(tmp_path / "rot90.pdf"), 90)
        out = str(tmp_path / "out.pdf")

        impose(a_project(rot), out)

        doc = fitz.open(rot)
        assert doc[0].rotation == 90, "imposition must not rewrite the source file"
        inset = mm_to_pt(10)
        trim = doc[0].trimbox  # unrotated space: inset from the unrotated A4 page
        assert (trim.x0, trim.y0) == pytest.approx((inset, inset))
        assert (trim.x1, trim.y1) == pytest.approx(
            (mm_to_pt(210) - inset, mm_to_pt(297) - inset))
        doc.close()


# ── rotation from the Rotate preprocessor ───────────

class TestRotatePreprocessor:
    @pytest.mark.parametrize("angle_enum,angle", [
        (RotateAngle.CW_90, 90),
        (RotateAngle.CW_270, 270),
    ])
    @pytest.mark.parametrize("inset", [0.0, 10.0])
    def test_preprocessor_rotation_keeps_all_artwork(self, tmp_path, angle_enum, angle, inset):
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2, trim_inset_mm=inset)
        out = str(tmp_path / "out.pdf")

        project = a_project(src, steps=[
            PreprocessorStep(type=PreprocessorType.ROTATE_PAGES, rotate_angle=angle_enum),
        ])
        assert impose(project, out) == 1
        tw, th = visual_trim_size(210, 297, inset, angle)
        assert_page_1_intact(out, project, tw, th)

    def test_scale_after_rotate_keeps_all_artwork(self, tmp_path):
        # _scale re-places pages with show_pdf_page, which mishandles rotated
        # sources — the chain Rotate → Scale must not crop either.
        src = make_source_pdf(str(tmp_path / "src.pdf"), n_pages=2, trim_inset_mm=10)
        out = str(tmp_path / "out.pdf")

        project = a_project(src, steps=[
            PreprocessorStep(type=PreprocessorType.ROTATE_PAGES, rotate_angle=RotateAngle.CW_90),
            PreprocessorStep(type=PreprocessorType.SCALE_PAGES, scale_factor=0.5),
        ])
        assert impose(project, out) == 1
        # Scaled 50% first: the page the sheet sees is half the trim, then turned.
        tw, th = visual_trim_size(210, 297, 10, 0)
        tw, th = tw * 0.5, th * 0.5
        tw, th = th, tw  # the Rotate preprocessor's quarter turn
        assert_page_1_intact(out, project, tw, th)
