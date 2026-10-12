"""
The UI/engine contract (ROADMAP.md Phase 3).

0.2.0's central defect: the Layout tab collected booklet modes, right-to-left,
signatures and page creep, and the engine ignored every one of them. Nothing was
wrong with either half in isolation — the bug lived in the gap between them, where
no test could see it.

These tests are that gap. They are the reason the defect cannot come back.
"""

import ast
import os
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from laydown.engine.capabilities import (
    HONOURED, NOT_IMPLEMENTED, model_paths, resolve, assign,
    MARK_HONOURED, MARK_NOT_IMPLEMENTED,
    PREPROCESSOR_HONOURED, PREPROCESSOR_NOT_IMPLEMENTED,
)
from laydown.engine.data_model import (
    MarkItem, MarkType, PreprocessorStep, PreprocessorType, Project,
)
from laydown.ui.schema import (
    SCHEMA, ControlType, all_controls, all_sections, all_targets, defaults, is_visible,
)


class TestEveryControlIsBackedByTheEngine:
    def test_no_control_promises_something_the_engine_ignores(self):
        offenders = sorted(set(all_targets()) - HONOURED)
        assert not offenders, (
            "these controls edit settings the engine does not read, so the user would "
            f"set them and nothing would happen: {offenders}"
        )

    def test_unimplemented_settings_are_not_offered(self):
        offered = sorted(set(all_targets()) & NOT_IMPLEMENTED)
        assert not offered, (
            f"these settings are declared unimplemented but the UI shows them: {offered}"
        )

    def test_every_control_target_exists_on_the_project(self):
        project = Project()
        for target in all_targets():
            resolve(project, target)  # raises AttributeError if the path is a typo

    def test_a_control_can_actually_write_its_target(self):
        project = Project()
        for control in all_controls():
            if control.type is ControlType.COLLECTION:
                continue
            assign(project, control.target, control.default)
            assert resolve(project, control.target) == control.default


class TestEverySettingIsClassified:
    def test_no_setting_escapes_classification(self):
        # The rule that makes the contract hold over time: adding a field to the data
        # model fails here until someone decides, in public, whether the engine
        # honours it. That decision is what 0.2.0 never had to make.
        unclassified = sorted(model_paths() - HONOURED - NOT_IMPLEMENTED)
        assert not unclassified, (
            "these model settings are neither HONOURED nor NOT_IMPLEMENTED — classify "
            f"them in engine/capabilities.py: {unclassified}"
        )

    def test_classified_settings_all_exist(self):
        project = Project()
        for path in sorted(HONOURED | NOT_IMPLEMENTED):
            resolve(project, path)

    def test_the_two_sets_do_not_overlap(self):
        assert not (HONOURED & NOT_IMPLEMENTED)


class TestSchemaShape:
    def test_control_defaults_match_the_model_defaults(self):
        # The panel's reset must land on the same value a fresh Project has, or
        # "reset" quietly means "change".
        project = Project()
        for control in all_controls():
            if control.type is ControlType.COLLECTION:
                continue
            assert control.default == resolve(project, control.target), (
                f"{control.target}: schema default {control.default!r} != model default "
                f"{resolve(project, control.target)!r}"
            )

    def test_targets_are_unique(self):
        targets = all_targets()
        assert len(targets) == len(set(targets)), "a setting is edited by two controls"

    def test_sections_are_named_for_what_they_edit(self):
        # Toolcraft's rule: a section title names the thing, so the label beside a
        # control can stay short without becoming ambiguous.
        banned = {"settings", "options", "controls", "general", "misc", "other"}
        for section in all_sections():
            assert section.title.lower() not in banned, f"vague section title: {section.title}"
            assert section.entity, f"{section.title} does not say what it edits"

    def test_sections_stay_small_enough_to_read(self):
        for section in all_sections():
            assert 1 <= len(section.controls) <= 7, (
                f"{section.title} has {len(section.controls)} controls; split it"
            )

    def test_section_titles_are_unique(self):
        titles = [s.title for s in all_sections()]
        assert len(titles) == len(set(titles))

    def test_switch_labels_do_not_say_enable(self):
        # The switch already communicates on/off.
        for control in all_controls():
            if control.type is ControlType.SWITCH:
                assert not control.label.lower().startswith("enable"), control.label

    def test_segmented_controls_stay_compact(self):
        for control in all_controls():
            if control.type is not ControlType.SEGMENTED:
                continue
            assert len(control.options) <= 4, f"{control.target}: too many segments"
            for _, text in control.options:
                assert len(text) <= 14, f"{control.target}: segment label {text!r} too long"

    def test_choice_controls_offer_choices(self):
        for control in all_controls():
            if control.type in (ControlType.SEGMENTED, ControlType.SELECT):
                assert len(control.options) >= 2, f"{control.target} has nothing to choose"

    def test_descriptions_add_meaning_beyond_the_label(self):
        for control in all_controls():
            if not control.description:
                continue
            assert control.description.lower() != control.label.lower()
            assert len(control.description) > len(control.label), control.target

    def test_every_tab_has_sections(self):
        for tab in SCHEMA:
            assert tab.sections, f"{tab.name} is empty"


class TestVisibility:
    def test_visibility_conditions_point_at_real_controls(self):
        targets = set(all_targets())
        for item in list(all_controls()) + list(all_sections()):
            if item.visible_when is not None:
                assert item.visible_when.target in targets, (
                    f"visible_when refers to {item.visible_when.target}, which no control owns"
                )

    def test_booklet_controls_are_hidden_for_n_up(self):
        from laydown.engine.data_model import LayoutType
        values = defaults()
        values["layout.layout_type"] = LayoutType.NUP
        booklet = next(s for s in all_sections() if s.title == "Booklet")
        assert not is_visible(booklet, values)

    def test_booklet_controls_appear_for_a_booklet(self):
        from laydown.engine.data_model import LayoutType
        values = defaults()
        values["layout.layout_type"] = LayoutType.BOOKLET
        booklet = next(s for s in all_sections() if s.title == "Booklet")
        assert is_visible(booklet, values)

    def test_signature_size_only_shows_for_perfect_binding(self):
        from laydown.engine.data_model import BookletMode
        values = defaults()
        control = next(c for c in all_controls() if c.target == "layout.signature_sheets")

        values["layout.booklet_mode"] = BookletMode.SADDLE_STITCH
        assert not is_visible(control, values)
        values["layout.booklet_mode"] = BookletMode.PERFECT_BOUND
        assert is_visible(control, values)

    def test_custom_sheet_size_only_shows_for_the_custom_preset(self):
        values = defaults()
        control = next(c for c in all_controls() if c.target == "sheet.custom_width_mm")
        assert not is_visible(control, values)
        values["sheet.preset"] = "Custom"
        assert is_visible(control, values)

    def test_creep_amounts_only_show_when_compensating(self):
        values = defaults()
        control = next(c for c in all_controls() if c.target == "layout.creep_outer_mm")
        assert not is_visible(control, values)
        values["layout.creep_enabled"] = True
        assert is_visible(control, values)


# ── the per-item contract: marks and preprocessors ──
#
# "marks" and "preprocessors" are lists, so the dotted-path contract above
# can only see them as wholes. The tables in capabilities.py classify every
# field of every item, per item type, and these tests wire them to the two
# tabs that edit the lists — by reading the tabs' own source, so a control
# wired to a field the engine ignores for that type fails the build.

_UI_ROOT = Path(__file__).resolve().parent.parent / "laydown" / "ui"


def _field_names(cls) -> set:
    return {f.name for f in dataclass_fields(cls)}


def _enum_names(node) -> set:
    """Every MarkType.X / PreprocessorType.X name referenced in *node*."""
    names = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id in ("MarkType", "PreprocessorType")):
            names.add(sub.attr)
    return names


def _method(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"method {name!r} not found — the tab changed shape")


def _branch_writes(body):
    """{item field: widget} for every `m.X = self.Y(...)` in *body*."""
    writes = {}
    for sub in body:
        if not (isinstance(sub, ast.Assign) and isinstance(sub.targets[0], ast.Attribute)
                and isinstance(sub.targets[0].value, ast.Name)
                and sub.targets[0].value.id in ("m", "step")):
            continue
        widget = None
        value = sub.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Attribute)
                and isinstance(value.func.value.value, ast.Name)
                and value.func.value.value.id == "self"):
            widget = value.func.value.attr
        writes[sub.targets[0].attr] = widget
    return writes


def _type_branches(func):
    """[(type names, {field: widget})] for every if/elif keyed on the item type."""
    out = []

    def visit(ifnode):
        types = _enum_names(ifnode.test)
        if types:
            out.append((types, _branch_writes(ifnode.body)))
        for sub in ifnode.orelse:
            if isinstance(sub, ast.If):
                visit(sub)

    for node in func.body:
        if isinstance(node, ast.If):
            visit(node)
    return out


def _widgets_enabled_only_for(tree):
    """{widget: the one type it is enabled for} from setEnabled(type == X) calls."""
    only_for = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setEnabled" and len(node.args) == 1):
            types = _enum_names(node.args[0])
            if (len(types) == 1 and isinstance(node.func.value, ast.Attribute)
                    and isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "self"):
                only_for[node.func.value.attr] = next(iter(types))
    return only_for


def _edited_mark_fields():
    """{MarkType name: fields the Marks tab writes for that type}."""
    tree = ast.parse((_UI_ROOT / "marks_tab.py").read_text())
    only_for = _widgets_enabled_only_for(tree)
    edited = {}
    for types, writes in _type_branches(_method(tree, "_on_param")):
        for tname in types:
            fields_ = {f for f, w in writes.items()
                       if w is None or only_for.get(w, tname) == tname}
            edited.setdefault(tname, set()).update(fields_)
    return edited


def _edited_preprocessor_fields():
    """{PreprocessorType name: fields the Preprocessors tab writes for that type}."""
    tree = ast.parse((_UI_ROOT / "preprocessors_tab.py").read_text())
    edited = {}
    for types, writes in _type_branches(_method(tree, "_on_param_changed")):
        for tname in types:
            edited.setdefault(tname, set()).update(writes)
    return edited


def _offered_preprocessor_types():
    tree = ast.parse((_UI_ROOT / "preprocessors_tab.py").read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_AVAILABLE" for t in node.targets)):
            return _enum_names(node.value)
    raise AssertionError("_AVAILABLE list not found in preprocessors_tab.py")


class TestEveryItemFieldIsClassified:
    def test_every_mark_field_is_classified_for_every_mark_type(self):
        names = _field_names(MarkItem)
        for t in MarkType:  # a missing key raises KeyError — by design
            honoured, missing = MARK_HONOURED[t], MARK_NOT_IMPLEMENTED[t]
            assert honoured <= names and missing <= names, f"unknown field name under {t.name}"
            assert honoured | missing == names, f"{t.name}: unclassified mark fields"
            assert not honoured & missing, f"{t.name}: field in both tables"

    def test_every_preprocessor_field_is_classified_for_every_preprocessor_type(self):
        names = _field_names(PreprocessorStep)
        for t in PreprocessorType:
            honoured, missing = PREPROCESSOR_HONOURED[t], PREPROCESSOR_NOT_IMPLEMENTED[t]
            assert honoured <= names and missing <= names, f"unknown field name under {t.name}"
            assert honoured | missing == names, f"{t.name}: unclassified preprocessor fields"
            assert not honoured & missing, f"{t.name}: field in both tables"


class TestTheTabsCannotEditUnhonouredFields:
    def test_marks_tab_edits_only_honoured_fields(self):
        edited = _edited_mark_fields()
        assert edited, "the marks-tab parser went blind — it found no edits"
        for tname, fields_ in edited.items():
            offenders = fields_ - MARK_HONOURED[MarkType[tname]]
            assert not offenders, (
                f"the Marks tab edits {offenders} for {tname}, which the engine "
                "does not honour for that mark type"
            )

    def test_preprocessors_tab_edits_only_honoured_fields(self):
        edited = _edited_preprocessor_fields()
        assert edited, "the preprocessors-tab parser went blind — it found no edits"
        for tname, fields_ in edited.items():
            offenders = fields_ - PREPROCESSOR_HONOURED[PreprocessorType[tname]]
            assert not offenders, (
                f"the Preprocessors tab edits {offenders} for {tname}, which the "
                "engine does not honour for that preprocessor type"
            )

    def test_offered_preprocessor_types_are_implemented(self):
        offered = _offered_preprocessor_types()
        assert offered, "no preprocessors offered — the parser went blind"
        for tname in offered:
            assert "type" in PREPROCESSOR_HONOURED[PreprocessorType[tname]], (
                f"the Preprocessors tab offers {tname}, which the pipeline rejects"
            )

    def test_the_parser_sees_the_fields_that_matter(self):
        # Not vacuous in the other direction: the parser must find the very
        # edit that motivated these tables (label_text, 2026-10-12 review F4).
        assert "label_text" in _edited_mark_fields().get("TEXT_LABEL", set())
        assert "crop_offset_mm" in _edited_mark_fields().get("GAP_CROP_MARKS", set())
        assert "crop_length_mm" not in _edited_mark_fields().get("GAP_CROP_MARKS", set())
        assert "scale_factor" in _edited_preprocessor_fields().get("SCALE_PAGES", set())

    def test_the_guard_has_teeth(self):
        # Prove the check fails if a field stops being honoured: pretend the
        # engine dropped label_text, and the same computation must flag it —
        # and only it.
        edited = _edited_mark_fields()
        starved = {t: MARK_HONOURED[t] - {"label_text"} for t in MarkType}
        offenders = {t.name: edited.get(t.name, set()) - starved[t] for t in MarkType}
        assert offenders["TEXT_LABEL"] == {"label_text"}
        assert not any(v for k, v in offenders.items() if k != "TEXT_LABEL")
