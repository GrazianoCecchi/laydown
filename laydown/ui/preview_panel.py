"""
Multi-sheet scrollable preview canvas.

Renders ALL imposed sheets and displays them in a scrollable grid.
Supports 1/2/4-column layout, zoom, fit-to-width/page, and overlays.
"""

import math
import os
from typing import Optional, List
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea,
    QGridLayout, QSizePolicy, QApplication,
)
from PyQt6.QtCore import Qt, QThread, QEvent, pyqtSignal, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPixmap, QImage, QFont, QPainter, QPen, QColor

import fitz

from laydown.engine.data_model import Project
from laydown.ui import theme as t
from laydown.engine.geometry import sheet_size_pt
from laydown.engine.impose import ImposeResult, impose_to_temp

MAGENTA = QColor(t.OVERLAY)

# ── preview memory budget ───────────────────────────

#: Total raster memory the preview may hold across ALL sheets. A 600-page 2-up
#: job (300 A3 sheets) is ~1.6 GB of QPixmaps at 96 DPI and ~14 GB at 288 DPI —
#: the budget caps the render DPI so that can never happen.
PREVIEW_MEMORY_BUDGET_BYTES = 512 * 1024 * 1024

#: Never render below this: an unreadable preview is worse than an oversized one.
PREVIEW_DPI_FLOOR = 36

#: RGB888 (alpha=False), 3 bytes per pixel.
PREVIEW_BYTES_PER_PIXEL = 3


def budgeted_dpi(
    sheet_count: int,
    sheet_w_pt: float,
    sheet_h_pt: float,
    target_dpi: int,
    budget: int = PREVIEW_MEMORY_BUDGET_BYTES,
    floor: int = PREVIEW_DPI_FLOOR,
) -> int:
    """
    The largest DPI <= *target_dpi* that keeps the whole job inside the budget.

    A sheet of sheet_w_pt x sheet_h_pt points rendered at DPI *d* is
    (sheet_w_pt * d/72) x (sheet_h_pt * d/72) pixels at 3 bytes each, so the
    job costs::

        bytes(d) = sheet_count * sheet_w_pt * sheet_h_pt * 3 * (d / 72)^2

    Solving bytes(d) = budget for d and clamping into [floor, target_dpi]
    gives the render DPI. Small jobs never feel it: 4 A4 sheets at 300 DPI are
    ~104 MB, well under the budget, so they render at the full target. A
    300-A3-sheet job lands near 55 DPI instead of the gigabytes an unbounded
    render would allocate. The floor wins over the budget — if even the floor
    DPI exceeds the budget the preview stays readable and oversize.
    """
    if sheet_count <= 0 or sheet_w_pt <= 0 or sheet_h_pt <= 0:
        return max(floor, int(target_dpi))
    bytes_per_dpi_squared = (
        sheet_count * sheet_w_pt * sheet_h_pt * PREVIEW_BYTES_PER_PIXEL / (72.0 ** 2)
    )
    affordable = math.sqrt(budget / bytes_per_dpi_squared)
    return int(max(floor, min(float(target_dpi), affordable)))


# ── cell geometry for overlays ───────────────────────


@dataclass
class CellInfo:
    x0: float
    y0: float
    x1: float
    y1: float
    page_num: int  # 1-based, 0 = blank


def cells_from_result(result: ImposeResult, sheet_idx: int) -> List[CellInfo]:
    """
    Overlay geometry for one sheet, taken from the imposition that drew it.

    The preview used to re-derive cell maths from the project, which meant the
    magenta overlay was a second opinion that could disagree with the printed
    result. It now describes the run whose output is on screen: the rects are the
    trim rects the engine actually placed, so what's outlined is what gets cut.
    """
    if sheet_idx >= len(result.placed):
        return []
    return [
        CellInfo(p.trim.x0, p.trim.y0, p.trim.x1, p.trim.y1, p.page_number)
        for p in result.placed[sheet_idx]
    ]


def draw_overlays(
    image: QImage, cells: List[CellInfo], scale: float,
    show_tops: bool, show_numbers: bool, show_frames: bool, show_previews: bool,
) -> QImage:
    """Paint overlay marks onto a QImage.

    QImage (not QPixmap) on purpose: this runs in the render thread, and Qt
    only allows QPixmap operations on the GUI thread.
    """
    if not cells:
        return image
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    for c in cells:
        px0, py0 = c.x0 * scale, c.y0 * scale
        pw, ph = (c.x1 - c.x0) * scale, (c.y1 - c.y0) * scale
        rect = QRectF(px0, py0, pw, ph)

        if not show_previews:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(245, 245, 245))
            painter.drawRect(rect)

        if show_frames and c.page_num > 0:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(MAGENTA, max(1.0, scale * 0.5)))
            painter.drawRect(rect)

        if show_tops and c.page_num > 0:
            painter.setPen(QPen(MAGENTA, max(1.5, scale * 0.7)))
            painter.drawLine(QPointF(px0, py0), QPointF(px0 + pw, py0))
            cx = px0 + pw / 2
            painter.drawLine(QPointF(cx, py0), QPointF(cx, py0 + min(20 * scale / 1.5, ph * 0.06)))

        if show_numbers and c.page_num > 0:
            # Bigger and regular weight (not bold) — the numbers should read clearly
            # without shouting.
            fs = max(22, int(min(pw, ph) * 0.28))
            f = QFont(t.FONT_FAMILY, fs)
            painter.setFont(f)
            painter.setPen(MAGENTA)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(c.page_num))

    painter.end()
    return image


# ── worker: render ALL sheets ────────────────────────


class _RenderWorker(QThread):
    """Renders every sheet of one imposition as a list of QImages.

    One worker per render: a settings change cancels the old worker and starts
    a new one instead of waiting, so the GUI thread never blocks on a render.
    The worker's results carry the generation they belong to; the canvas drops
    any that arrive after a newer render was requested.

    QImage (not QPixmap) because this runs on the worker thread — Qt only
    permits QPixmap on the GUI thread. The canvas converts to QPixmap when
    the results arrive.
    """

    done = pyqtSignal(list, int, int)  # List[QImage], render DPI, generation
    error = pyqtSignal(str, int)      # message, generation

    def __init__(self, project, target_dpi, overlays, generation, parent=None):
        super().__init__(parent)
        self.project: Project = project
        # The DPI worth rendering at (zoom-driven). The real DPI used is
        # derived per job inside run(): the memory budget depends on the
        # actual sheet count and sheet size, which only the imposed document
        # knows for sure.
        self.target_dpi: int = target_dpi
        self.show_tops, self.show_numbers, self.show_frames, self.show_previews = overlays
        self.generation = generation
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        if self._cancel:
            return  # cancelled before the thread even started
        tmp_path = None
        try:
            tmp_path, result = impose_to_temp(self.project)
            doc = fitz.open(tmp_path)
            total = len(doc)
            dpi = self.target_dpi
            if total:
                rect = doc[0].rect
                dpi = budgeted_dpi(total, rect.width, rect.height, self.target_dpi)
            mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            scale = dpi / 72.0
            images = []

            for i in range(total):
                if self._cancel:
                    doc.close()
                    return
                page = doc[i]
                pix = page.get_pixmap(matrix=mat, alpha=False)
                # .copy() detaches: pix.samples is freed when pix goes out of
                # scope, and a QImage built on it without copying would point
                # into freed memory.
                img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                             QImage.Format.Format_RGB888).copy()
                cells = cells_from_result(result, i)
                img = draw_overlays(img, cells, scale,
                                    self.show_tops, self.show_numbers,
                                    self.show_frames, self.show_previews)
                images.append(img)

            doc.close()
            if not self._cancel:
                self.done.emit(images, dpi, self.generation)
        except Exception as e:
            if not self._cancel:
                self.error.emit(str(e), self.generation)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


# ── main canvas widget ───────────────────────────────


class SheetCanvas(QScrollArea):
    """Scrollable multi-sheet preview with zoom and column controls."""

    #: emitted when the user double-clicks the empty canvas (the "open a PDF" area)
    open_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._pixmaps: List[QPixmap] = []
        self._columns = 2
        self._zoom = 0.0  # 0 = auto-fit on first render
        self._base_dpi = 72        # the PDF's own unit scale — pixmaps are rendered above this
        self._render_dpi = 96
        self._sheet_widgets: list = []

        self._show_tops = False
        self._show_numbers = True
        self._show_frames = True
        self._show_previews = True

        # One worker per render. _worker is the newest; _workers holds every
        # in-flight one so clear_all/cleanup can cancel and join them.
        self._worker: Optional[_RenderWorker] = None
        self._workers: List[_RenderWorker] = []
        # Bumped on every render request and on clear_all. Results carry the
        # generation they were started with, so a stale result that lands
        # after a newer request is dropped instead of overwriting it.
        self._generation = 0

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._do_render)

        self._setup_ui()
        # Double-click the empty preview to open a PDF, like the drop zone in most
        # apps. Filtered on the viewport because that is where the click lands.
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if (obj is self.viewport()
                and event.type() == QEvent.Type.MouseButtonDblClick
                and not self._pixmaps):          # only over the "open a PDF" placeholder
            self.open_requested.emit()
            return True
        return super().eventFilter(obj, event)

    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { border: none; }")
        self._content = QWidget()
        self._content.setStyleSheet(f"background: {t.BG};")
        self.setWidget(self._content)
        self._grid = QGridLayout(self._content)
        self._grid.setSpacing(16)
        self._grid.setContentsMargins(16, 16, 16, 16)
        self._show_placeholder()

    def _show_placeholder(self):
        self._clear_grid()
        lbl = QLabel("Open a PDF to get started\n\nDouble-click here, press Ctrl+O,\nor drag and drop a PDF")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_LG}px; padding: 60px;")
        self._grid.addWidget(lbl, 0, 0)

    def _clear_grid(self):
        # NEVER setParent(None) here: on a visible child that reparents it to the
        # desktop, i.e. it becomes a *top-level window*. Every zoom rebuilds this grid,
        # so setParent(None) made each zoom click spawn flashing windows of the sheets.
        # hide() + deleteLater() removes them from view and frees them while they stay
        # parented to the content widget until the event loop deletes them.
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
        self._sheet_widgets.clear()

    # ── public API ───────────────────────────────

    def update_project(self, project: Project):
        self._project = project
        self._debounce.start()

    def set_columns(self, n: int):
        self._columns = n
        self._rebuild_display()

    def set_overlays(self, show_tops, show_numbers, show_frames, show_previews):
        self._show_tops = show_tops
        self._show_numbers = show_numbers
        self._show_frames = show_frames
        self._show_previews = show_previews
        if self._project:
            self._debounce.start()

    def clear_all(self):
        # Used on close/reset, so waiting here is fine (it is NOT fine in
        # _do_render — see there).
        self._stop_workers()
        self._project = None
        self._pixmaps.clear()
        self._show_placeholder()

    def _sheet_size_pt(self):
        """The sheet in points, recovered from a pixmap rendered at _render_dpi."""
        if not self._pixmaps:
            return 0.0, 0.0
        factor = self._base_dpi / self._render_dpi
        return self._pixmaps[0].width() * factor, self._pixmaps[0].height() * factor

    def _apply_zoom(self, zoom: float):
        self._zoom = zoom
        self._rebuild_display()
        # Re-rasterise if the sheets are now drawn much larger or smaller than the
        # pixels we have, so zooming in sharpens instead of magnifying blur.
        # Compare against the budgeted DPI a new render would actually use —
        # on a large job the budget caps it below the zoom target, and without
        # that every zoom click would queue a re-render that changes nothing.
        if self._project and abs(self._planned_dpi() - self._render_dpi) > 24:
            self._debounce.start()

    def zoom_in(self):
        self._apply_zoom(min(4.0, (self._zoom if self._zoom > 0 else 1.0) * 1.25))

    def zoom_out(self):
        self._apply_zoom(max(0.15, (self._zoom if self._zoom > 0 else 1.0) / 1.25))

    def fit_width(self):
        if not self._pixmaps:
            return
        avail = self.viewport().width() - self._grid.spacing() * (self._columns + 1) - 40
        per_col = avail / self._columns
        sheet_w, _ = self._sheet_size_pt()
        self._apply_zoom(per_col / sheet_w if sheet_w > 0 else 1.0)

    def fit_page(self):
        if not self._pixmaps:
            return
        vp = self.viewport()
        avail_w = (vp.width() - 60) / self._columns
        avail_h = vp.height() - 70
        sheet_w, sheet_h = self._sheet_size_pt()
        self._apply_zoom(min(avail_w / sheet_w if sheet_w else 1,
                             avail_h / sheet_h if sheet_h else 1))

    def actual_size(self):
        try:
            screen_dpi = QApplication.primaryScreen().logicalDotsPerInch()
        except Exception:
            screen_dpi = 96
        self._apply_zoom(screen_dpi / self._base_dpi)

    def get_zoom_percent(self) -> int:
        z = self._zoom if self._zoom > 0 else 1.0
        return int(z * 100)

    # ── rendering ────────────────────────────────

    def _target_dpi(self) -> int:
        """
        The DPI worth rasterising at for the current zoom.

        Rendering at 72 and letting Qt scale up is what made the preview soft; asking
        MuPDF for the pixels instead keeps text and rules crisp. Capped so that zooming
        a large job in doesn't turn every sheet into a huge bitmap.
        """
        try:
            screen = QApplication.primaryScreen()
            ratio = screen.devicePixelRatio() if screen else 1.0
        except Exception:
            ratio = 1.0
        zoom = self._zoom if self._zoom > 0 else 1.0
        return int(max(96, min(300, self._base_dpi * zoom * ratio)))

    def _planned_dpi(self) -> int:
        """The DPI a render started now would use: the zoom target capped by
        the memory budget. The worker recomputes this with the real sheet
        count once the job is imposed; this estimate (last render's count)
        is what the zoom heuristic compares against."""
        target = self._target_dpi()
        if not self._project:
            return target
        w_pt, h_pt = sheet_size_pt(self._project)
        count = len(self._pixmaps) or 1
        return budgeted_dpi(count, w_pt, h_pt, target)

    def _do_render(self):
        if not self._project or not self._project.source_pdf_path:
            return
        self._generation += 1
        # Cancel any in-flight render but NEVER wait() for it here: that used
        # to freeze the GUI thread until the old render finished (PyMuPDF
        # holds the GIL through each call, so the wait was long). The old
        # worker's late done/error is dropped by the generation check.
        if self._worker is not None:
            self._worker.cancel()
        worker = _RenderWorker(
            self._project, self._target_dpi(),
            (self._show_tops, self._show_numbers, self._show_frames,
             self._show_previews),
            self._generation,
        )
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._retire_worker)
        self._workers.append(worker)
        self._worker = worker
        worker.start()

    def _retire_worker(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _stop_workers(self):
        """Cancel every in-flight render and join it. Only for teardown paths
        (clear_all/cleanup) — calling this on a settings change would block
        the GUI thread, which is exactly what the per-render workers avoid."""
        self._generation += 1  # invalidate results still in flight
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            worker.wait()

    def _on_done(self, images: list, render_dpi: int, generation: int):
        if generation != self._generation:
            return  # a newer settings change superseded this render
        self._render_dpi = render_dpi
        # QPixmap belongs on the GUI thread; the worker handed us QImages.
        self._pixmaps = [QPixmap.fromImage(img) for img in images]
        if self._zoom <= 0:
            self._rebuild_display()
            QTimer.singleShot(50, self.fit_width)
        else:
            self._rebuild_display()

    def _on_error(self, msg: str, generation: int):
        if generation != self._generation:
            return
        self._clear_grid()
        lbl = QLabel(f"Render error:\n{msg}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {t.DESTRUCTIVE}; font-size: {t.TEXT_SM}px; padding: 40px;")
        self._grid.addWidget(lbl, 0, 0)

    # ── display ──────────────────────────────────

    def _rebuild_display(self):
        self._clear_grid()
        if not self._pixmaps:
            self._show_placeholder()
            return

        zoom = max(0.1, self._zoom) if self._zoom > 0 else 1.0
        cols = self._columns

        for i, pm in enumerate(self._pixmaps):
            row = i // cols
            col = i % cols

            container = QWidget()
            container.setStyleSheet("background: transparent;")
            vbox = QVBoxLayout(container)
            vbox.setContentsMargins(4, 4, 4, 6)
            vbox.setSpacing(4)

            lbl = QLabel()
            # zoom is against the sheet's own points, so convert out of render DPI.
            display_scale = zoom * self._base_dpi / self._render_dpi
            target_w = max(60, int(pm.width() * display_scale))
            scaled = pm.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(scaled)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"border: 1px solid {t.BORDER};")
            vbox.addWidget(lbl)

            num = QLabel(str(i + 1))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px; background: transparent;")
            vbox.addWidget(num)

            self._grid.addWidget(container, row, col,
                                 Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            self._sheet_widgets.append(container)

    # ── cleanup ──────────────────────────────────

    def cleanup(self):
        self._stop_workers()
