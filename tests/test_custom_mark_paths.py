"""
Custom-mark PDFs on network paths.

A preset can point a custom mark at \\\\server\\share\\mark.pdf. On Windows, merely
asking whether that file exists makes the machine sign in to the server with the
user's credentials, so a shared preset could harvest them. Export and preflight
must refuse such a path before any filesystem call.
"""

import os

import fitz
import pytest

from laydown.engine.data_model import MarkItem, MarkType, Project, SheetSettings
from laydown.engine.impose import impose
from laydown.engine.preflight import Severity, preflight
from laydown.engine.utils import is_network_path

NETWORK_PATHS = [
    r"\\server\share\mark.pdf",
    "//server/share/mark.pdf",
    r"\\?\UNC\server\share\mark.pdf",
    r"\\.\pipe\mark.pdf",
    r"\/server/share/mark.pdf",
    r"  \\server\share\mark.pdf",
]

LOCAL_PATHS = [
    r"C:\marks\mark.pdf",
    "/home/user/marks/mark.pdf",
    "marks/mark.pdf",
    r"\marks\mark.pdf",
    "",
]


@pytest.fixture
def no_network_touch(monkeypatch):
    """Record any filesystem call that reaches a network path."""
    touched = []
    real_isfile, real_open = os.path.isfile, fitz.open

    def isfile(path, *a, **kw):
        if isinstance(path, str) and is_network_path(path):
            touched.append(("isfile", path))
        return real_isfile(path, *a, **kw)

    def fitz_open(*a, **kw):
        path = a[0] if a else kw.get("filename")
        if isinstance(path, str) and is_network_path(path):
            touched.append(("fitz.open", path))
        return real_open(*a, **kw)

    monkeypatch.setattr(os.path, "isfile", isfile)
    monkeypatch.setattr(fitz, "open", fitz_open)
    return touched


def a_project(src, mark_path):
    p = Project(source_pdf_path=src)
    p.sheet = SheetSettings(preset="A3")
    p.marks = [MarkItem(mark_type=MarkType.CUSTOM_MARK, mark_pdf_path=mark_path)]
    return p


class TestDetection:
    @pytest.mark.parametrize("path", NETWORK_PATHS)
    def test_network_paths_are_recognised(self, path):
        assert is_network_path(path)

    @pytest.mark.parametrize("path", LOCAL_PATHS)
    def test_local_paths_are_not(self, path):
        assert not is_network_path(path)


class TestExport:
    @pytest.mark.parametrize("path", NETWORK_PATHS)
    def test_export_skips_the_mark_without_touching_it(
            self, source_pdf, out_path, no_network_touch, path):
        project = a_project(source_pdf(n_pages=2), path)
        assert impose(project, out_path()) == 1  # the sheet is still produced
        assert no_network_touch == []

    def test_a_local_mark_still_reaches_the_sheet(self, source_pdf, out_path, tmp_path,
                                                  no_network_touch):
        art = str(tmp_path / "mark.pdf")
        doc = fitz.open()
        doc.new_page(width=60, height=60).draw_rect(
            fitz.Rect(0, 0, 60, 60), fill=(0, 0, 1), color=None)
        doc.save(art)
        doc.close()

        out = out_path()
        impose(a_project(source_pdf(n_pages=2), art), out)
        with fitz.open(out) as result:
            blue = [d for d in result[0].get_drawings() if d.get("fill") == (0.0, 0.0, 1.0)]
        assert blue, "a local custom mark must still be drawn"


class TestPreflight:
    @pytest.mark.parametrize("path", NETWORK_PATHS)
    def test_preflight_warns_without_touching_it(self, no_network_touch, path):
        findings = preflight(a_project("", path))
        network = [f for f in findings if "network location" in f.message]
        assert len(network) == 1
        assert network[0].severity == Severity.WARNING
        assert "copy it to this computer" in network[0].message
        assert no_network_touch == []

    def test_a_missing_local_mark_is_still_reported_as_missing(self, tmp_path):
        findings = preflight(a_project("", str(tmp_path / "gone.pdf")))
        messages = [f.message for f in findings]
        assert any("does not exist" in m for m in messages)
        assert not any("network location" in m for m in messages)
