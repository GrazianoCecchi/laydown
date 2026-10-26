"""
What the engine actually honours.

Laydown 0.2.0 shipped a Layout tab that collected booklet modes, right-to-left,
signatures and page creep, and an engine that silently ignored all of them. The
settings existed, the UI wrote them, nothing read them, and the operator only found
out at the press. That failure was possible because "the model has a field" and
"the engine reads it" were two unrelated facts that nobody checked.

This module makes them one fact. Every setting in the data model is classified
here, and the tests in ``tests/test_capabilities.py`` enforce three rules:

1. every control the UI shows targets an HONOURED setting;
2. every path named here really exists on ``Project``, so typos can't hide;
3. every field of the model is classified — a new setting cannot be added without
   deciding, in public, whether the engine honours it.

Adding a field to the data model therefore *fails the build* until it is listed.
Put it in NOT_IMPLEMENTED and the UI is forbidden from offering it.

The list-valued settings — ``marks`` and ``preprocessors`` — get the same law one
level down, per item type: the ``MARK_*`` / ``PREPROCESSOR_*`` tables below
classify every field of ``MarkItem`` and ``PreprocessorStep`` *for each mark /
preprocessor type*. A field the Marks or Preprocessors tab lets the operator
edit for a type must sit in that type's HONOURED table; the tests read those
tabs (``ui/marks_tab.py``, ``ui/preprocessors_tab.py``) directly, so wiring a
control to a field the engine ignores for that type fails the build too.
"""

from dataclasses import fields, is_dataclass
from typing import Any, Dict, Set

from laydown.engine.data_model import MarkType, PreprocessorType, Project


# Settings the engine reads and acts on. The UI may offer these.
HONOURED: Set[str] = {
    "source_pdf_path",

    # Source pages — engine/geometry.source_boxes
    "source.box",
    "source.bleed_mm",

    # Preprocessors — engine/preprocessors.apply_preprocessors
    "preprocessors",

    # Layout — engine/geometry.sheet_plan
    "layout.layout_type",
    "layout.nup",
    "layout.rows",
    "layout.cols",
    "layout.auto_rotate",
    "layout.repeat_mode",
    "layout.repeat_count",
    "layout.scaling",
    "layout.booklet_mode",
    "layout.signature_sheets",
    "layout.right_to_left",
    "layout.fillers_in_middle",
    "layout.gutter_h_mm",
    "layout.gutter_v_mm",
    "layout.page_range",
    "layout.creep_enabled",
    "layout.creep_outer_mm",
    "layout.creep_inner_mm",

    # Sheet — engine/geometry.sheet_size_pt, margins_pt
    "sheet.preset",
    "sheet.custom_width_mm",
    "sheet.custom_height_mm",
    "sheet.orientation",
    "sheet.margin_top_mm",
    "sheet.margin_bottom_mm",
    "sheet.margin_left_mm",
    "sheet.margin_right_mm",

    # Marks — engine/marks.draw_marks
    "marks",
}


# Declared in the model but not implemented. The UI must NOT offer these: a control
# that cannot do anything is worse than a missing feature, because the operator
# believes it worked. Move an entry up to HONOURED in the same change that makes
# the engine read it, never before.
NOT_IMPLEMENTED: Set[str] = set()


# ── per-item classification ─────────────────────────
#
# "marks" / "preprocessors" above classify the lists as a whole; the tables
# below classify every *field* of every list item, per item type. The same two
# rules hold one level down: a field appears in exactly one of the two tables
# for its type, and the tab that edits the list may only edit fields sitting
# in that type's HONOURED table. Fields are listed explicitly (not derived as
# a complement) so an unhonoured field is a decision made in public, not a
# leftover.


MARK_HONOURED: Dict[MarkType, Set[str]] = {
    # mark_type/enabled are read for every item before the type is dispatched.
    MarkType.CROP_MARKS: {
        "mark_type", "enabled", "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only",
    },
    MarkType.GAP_CROP_MARKS: {
        "mark_type", "enabled", "crop_offset_mm", "crop_width_pt",
    },
    MarkType.TRIM_LINE: {"mark_type", "enabled"},
    MarkType.REGISTRATION: {"mark_type", "enabled"},
    MarkType.FOLDING_MARKS: {"mark_type", "enabled", "fold_line_length_mm"},
    MarkType.PERFORATION_MARKS: {"mark_type", "enabled"},
    MarkType.COLLATING_MARKS: {"mark_type", "enabled"},
    MarkType.COLOR_BAR: {"mark_type", "enabled", "patch_size_mm"},
    MarkType.TEXT_LABEL: {"mark_type", "enabled", "label_text", "label_font_size"},
    MarkType.CUSTOM_MARK: {
        "mark_type", "enabled", "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
}

MARK_NOT_IMPLEMENTED: Dict[MarkType, Set[str]] = {
    MarkType.CROP_MARKS: {
        "label_text", "label_font_size", "fold_line_length_mm", "patch_size_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    # The Marks tab greys the Length control out for gap crop marks — these run
    # to the sheet edge and have no length — so it edits nothing unhonoured.
    MarkType.GAP_CROP_MARKS: {
        "crop_length_mm", "crop_outer_only", "label_text", "label_font_size", "fold_line_length_mm",
        "patch_size_mm", "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.TRIM_LINE: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "fold_line_length_mm", "patch_size_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.REGISTRATION: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "fold_line_length_mm", "patch_size_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.FOLDING_MARKS: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "patch_size_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.PERFORATION_MARKS: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "fold_line_length_mm", "patch_size_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.COLLATING_MARKS: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "fold_line_length_mm", "patch_size_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.COLOR_BAR: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "fold_line_length_mm",
        "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.TEXT_LABEL: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "fold_line_length_mm",
        "patch_size_mm", "mark_pdf_path", "mark_width_mm", "mark_x_mm", "mark_y_mm",
    },
    MarkType.CUSTOM_MARK: {
        "crop_length_mm", "crop_offset_mm", "crop_width_pt", "crop_outer_only", "label_text",
        "label_font_size", "fold_line_length_mm", "patch_size_mm",
    },
}


PREPROCESSOR_HONOURED: Dict[PreprocessorType, Set[str]] = {
    PreprocessorType.ROTATE_PAGES: {"type", "enabled", "rotate_angle"},
    PreprocessorType.SCALE_PAGES: {"type", "enabled", "scale_factor"},
    PreprocessorType.REORDER_PAGES: {"type", "enabled", "page_order"},
    # The remaining types are declared in the model but the pipeline rejects
    # them with a ValueError rather than silently running them, so not even
    # "type" is honoured — enabled is still read before the dispatch.
    PreprocessorType.CLONE_PAGES: {"enabled"},
    PreprocessorType.N_PLUS_1_PAGES: {"enabled"},
    PreprocessorType.SPLIT_PAGES: {"enabled"},
    PreprocessorType.SHUFFLE_PAGES: {"enabled"},
    PreprocessorType.RESIZE_PAGES: {"enabled"},
    PreprocessorType.OVERRIDE_BOX: {"enabled"},
    PreprocessorType.SETUP_BLEEDS: {"enabled"},
    PreprocessorType.CENTER_AND_CROP: {"enabled"},
    PreprocessorType.SLICE_PAGES: {"enabled"},
}

PREPROCESSOR_NOT_IMPLEMENTED: Dict[PreprocessorType, Set[str]] = {
    PreprocessorType.ROTATE_PAGES: {"scale_factor", "page_order"},
    PreprocessorType.SCALE_PAGES: {"rotate_angle", "page_order"},
    PreprocessorType.REORDER_PAGES: {"rotate_angle", "scale_factor"},
    PreprocessorType.CLONE_PAGES: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.N_PLUS_1_PAGES: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.SPLIT_PAGES: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.SHUFFLE_PAGES: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.RESIZE_PAGES: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.OVERRIDE_BOX: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.SETUP_BLEEDS: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.CENTER_AND_CROP: {"type", "rotate_angle", "scale_factor", "page_order"},
    PreprocessorType.SLICE_PAGES: {"type", "rotate_angle", "scale_factor", "page_order"},
}


def _walk(obj: Any, prefix: str = "") -> Set[str]:
    """Every settable path in the model tree, dotted."""
    found: Set[str] = set()
    for f in fields(obj):
        path = f"{prefix}{f.name}"
        value = getattr(obj, f.name)
        if is_dataclass(value) and not isinstance(value, type):
            found |= _walk(value, f"{path}.")
        else:
            found.add(path)
    return found


def model_paths() -> Set[str]:
    """Every setting the data model exposes, discovered by walking a default Project."""
    return _walk(Project())


def resolve(project: Project, path: str) -> Any:
    """Read a dotted path off a Project. Raises AttributeError if it doesn't exist."""
    value: Any = project
    for part in path.split("."):
        value = getattr(value, part)
    return value


def assign(project: Project, path: str, value: Any) -> None:
    """Write a dotted path on a Project."""
    parts = path.split(".")
    target = project
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)
