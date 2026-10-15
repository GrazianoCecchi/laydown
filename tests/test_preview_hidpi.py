"""
The preview must stay sharp on HiDPI displays (0.4.6 bug).

Sheets are rasterised at zoom x device-pixel-ratio, but the display step scaled
them down to their *logical* width, so on a 150-200% display Qt stretched the
pixmap back up and the preview came out soft. The pixmap handed to the sheet
label must carry the physical pixels and the device pixel ratio.

(Qt 6's QLabel.pixmap() hands back a copy re-sampled to the label's own device
pixel ratio, which is 1 offscreen, so the test records what setPixmap received.)
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel

from laydown.ui import preview_panel
from laydown.ui.preview_panel import SheetCanvas


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_preview_keeps_physical_pixels(app, monkeypatch, dpr):
    given = []

    class RecordingLabel(QLabel):
        def setPixmap(self, pm):
            given.append(QPixmap(pm))
            super().setPixmap(pm)

    monkeypatch.setattr(preview_panel, "QLabel", RecordingLabel)
    monkeypatch.setattr(SheetCanvas, "devicePixelRatioF", lambda self: dpr)
    canvas = SheetCanvas()
    img = QImage(1200, 850, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFFFF)
    canvas._render_dpi = 144
    canvas._zoom = 1.0
    canvas._pixmaps = [QPixmap.fromImage(img)]
    given.clear()
    canvas._rebuild_display()

    (pm,) = given
    logical_w = max(60, int(1200 * 1.0 * canvas._base_dpi / canvas._render_dpi))
    assert pm.devicePixelRatio() == pytest.approx(dpr)
    assert pm.width() == round(logical_w * dpr), "pixmap was scaled to logical, not physical, pixels"
    assert pm.deviceIndependentSize().width() == pytest.approx(logical_w, abs=1), "on-screen size changed"
    canvas.deleteLater()
