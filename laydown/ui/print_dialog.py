"""
Print Preview and Print Dialog for Laydown.

Provides an integrated, dark-themed print dialog combining a real-time
print preview canvas (QPrintPreviewWidget) with a comprehensive prepress control panel:
- Printer selector and system driver properties
- Sheet range selection (All, Current, Custom range like '1-3, 5')
- Copies and collation
- Duplex mode (Simplex, Long edge, Short edge)
- Paper size selection and orientation matching
- Prepress scaling: 100% Actual Size (no distortion), Fit to page, Shrink oversized
- Printable area margin overlays and unprintable margin warning badges
- Asynchronous high-resolution spooling via QThread
"""

import os
import re
import tempfile
from typing import List, Optional, Tuple

import fitz
from PyQt6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QImage, QPageLayout, QPageSize, QPainter, QPen,
)
from PyQt6.QtPrintSupport import (
    QPrintDialog, QPrinter, QPrinterInfo, QPrintPreviewWidget,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGraphicsView,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QVBoxLayout,
    QWidget,
)

from laydown.engine.data_model import Project
from laydown.engine.impose import impose_to_temp
from laydown.ui import theme as t
from laydown.ui import win_print
from laydown.ui.icons import lucide


# ── Geometry & Range Helpers ──────────────────────────────

def parse_page_range(text: str, total_pages: int) -> List[int]:
    """
    Parse a 1-based page range string (e.g. '1-3, 5') into a list of 0-based page indices.

    Supports comma-separated numbers and ranges (e.g. '1, 3-5, 8').
    Ignores whitespace, discards duplicates while maintaining ascending order,
    and excludes numbers outside [1, total_pages].
    Raises ValueError on invalid syntax.
    """
    cleaned = text.strip()
    if not cleaned:
        return list(range(total_pages))

    result = set()
    parts = cleaned.split(",")
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            sub = token.split("-")
            if len(sub) != 2:
                raise ValueError(f"Intervallo non valido: '{token}'")
            try:
                start = int(sub[0].strip())
                end = int(sub[1].strip())
            except ValueError:
                raise ValueError(f"Intervallo non valido: '{token}'")
            if start > end:
                start, end = end, start
            for p in range(start, end + 1):
                if 1 <= p <= total_pages:
                    result.add(p - 1)
        else:
            try:
                val = int(token)
            except ValueError:
                raise ValueError(f"Valore numerico non valido: '{token}'")
            if 1 <= val <= total_pages:
                result.add(val - 1)

    sorted_pages = sorted(list(result))
    if not sorted_pages:
        raise ValueError("Nessun foglio valido specificato nell'intervallo")
    return sorted_pages


def compute_sheet_placement(
    paper_w_pt: float,
    paper_h_pt: float,
    sheet_w_pt: float,
    sheet_h_pt: float,
    scaling_mode: str = "actual",
    center: bool = True,
) -> Tuple[float, float, float, float]:
    """
    Calculate target dimensions and offsets (in points) for placing a sheet on paper.

    Returns:
        (target_w_pt, target_h_pt, offset_x_pt, offset_y_pt)
    """
    if sheet_w_pt <= 0 or sheet_h_pt <= 0:
        return (paper_w_pt, paper_h_pt, 0.0, 0.0)

    if scaling_mode == "fit":
        scale = min(paper_w_pt / sheet_w_pt, paper_h_pt / sheet_h_pt)
    elif scaling_mode == "shrink":
        if sheet_w_pt > paper_w_pt or sheet_h_pt > paper_h_pt:
            scale = min(paper_w_pt / sheet_w_pt, paper_h_pt / sheet_h_pt)
        else:
            scale = 1.0
    else:  # "actual" (100% 1:1)
        scale = 1.0

    target_w = sheet_w_pt * scale
    target_h = sheet_h_pt * scale

    if center:
        offset_x = (paper_w_pt - target_w) / 2.0
        offset_y = (paper_h_pt - target_h) / 2.0
    else:
        offset_x = 0.0
        offset_y = 0.0

    return (target_w, target_h, offset_x, offset_y)


def find_best_matching_paper_size(
    supported_sizes: List[QPageSize],
    sheet_w_mm: float,
    sheet_h_mm: float,
    preset: str = "",
) -> int:
    """
    Find the index of the best matching paper size from supported sizes.

    Prioritizes:
    1. Exact PageSizeId matching the imposition preset (e.g. A3 -> PageSizeId.A3).
    2. Name matching the preset (e.g. 'A3' in s.name()).
    3. Exact dimension match within 3 mm in either orientation.
    4. Proportional fit: if the sheet is large (A3, SRA3, etc.) but the printer only supports
       up to A4, selects standard A4 rather than Legal or Letter.
    """
    if not supported_sizes:
        return 0

    preset_upper = (preset or "").upper().strip()

    # 1. Exact preset ID / standard enum match
    if preset_upper == "A3":
        for idx, s in enumerate(supported_sizes):
            if s.id() == QPageSize.PageSizeId.A3 or s.name().strip().upper() == "A3":
                return idx
    elif preset_upper == "A4":
        for idx, s in enumerate(supported_sizes):
            if s.id() == QPageSize.PageSizeId.A4 or s.name().strip().upper() == "A4":
                return idx
    elif preset_upper == "SRA3":
        for idx, s in enumerate(supported_sizes):
            if "SRA3" in s.name().upper():
                return idx
        # Fallback for SRA3: if printer supports A3, pick A3
        for idx, s in enumerate(supported_sizes):
            if s.id() == QPageSize.PageSizeId.A3 or s.name().strip().upper() == "A3":
                return idx

    # 2. Exact dimension match within 3 mm (either orientation)
    for idx, s in enumerate(supported_sizes):
        sz = s.size(QPageSize.Unit.Millimeter)
        if (abs(sz.width() - sheet_w_mm) < 3.0 and abs(sz.height() - sheet_h_mm) < 3.0) or \
           (abs(sz.width() - sheet_h_mm) < 3.0 and abs(sz.height() - sheet_w_mm) < 3.0):
            return idx

    # 3. If the imposed sheet is large (A3, SRA3, etc.) but the printer only supports up to A4:
    # Prefer standard A4 rather than Legal/Letter!
    max_dim = max(sheet_w_mm, sheet_h_mm)
    if max_dim >= 400.0:
        for idx, s in enumerate(supported_sizes):
            if s.id() == QPageSize.PageSizeId.A3 or s.name().strip().upper() == "A3":
                return idx
        for idx, s in enumerate(supported_sizes):
            if s.id() == QPageSize.PageSizeId.A4 or s.name().strip().upper() == "A4":
                return idx

    # 4. Fallback minimum difference
    best_diff = float("inf")
    best_idx = 0
    for idx, s in enumerate(supported_sizes):
        sz = s.size(QPageSize.Unit.Millimeter)
        diff = min(
            abs(sz.width() - sheet_w_mm) + abs(sz.height() - sheet_h_mm),
            abs(sz.width() - sheet_h_mm) + abs(sz.height() - sheet_w_mm),
        )
        if diff < best_diff:
            best_diff = diff
            best_idx = idx
    return best_idx


# ── Background Print Spooler Worker ────────────────────────

class _PrintWorker(QThread):
    progress = pyqtSignal(int, int)  # current_sheet (1-based), total_sheets
    finished_printing = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        printer: QPrinter,
        pdf_path: str,
        sheet_indices: List[int],
        scaling_mode: str,
        center: bool,
        resolution: int,
        page_size: Optional[QPageSize] = None,
        orientation: Optional[QPageLayout.Orientation] = None,
        printer_name: Optional[str] = None,
        devmode_bytes: Optional[bytes] = None,
        project_name: str = "Progetto",
        parent=None,
    ):
        super().__init__(parent)
        self._printer = printer
        self._pdf_path = pdf_path
        self._sheet_indices = sheet_indices
        self._scaling_mode = scaling_mode
        self._center = center
        self._resolution = resolution
        self._page_size = page_size
        self._orientation = orientation
        self._printer_name = printer_name
        self._devmode_bytes = devmode_bytes
        self._project_name = project_name
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        # 1. Native Windows GDI spooler path if on Windows with valid DEVMODE
        if win_print.is_windows() and self._devmode_bytes and self._printer_name:
            try:
                win_print.print_pdf_direct_gdi(
                    printer_name=self._printer_name,
                    devmode_bytes=self._devmode_bytes,
                    doc_path=self._pdf_path,
                    sheet_indices=self._sheet_indices,
                    scaling_mode=self._scaling_mode,
                    center=self._center,
                    resolution=self._resolution,
                    progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
                    cancel_check=lambda: self._cancel,
                    doc_name=f"Laydown - {self._project_name}",
                )
                if not self._cancel:
                    self.finished_printing.emit()
                return
            except Exception as exc:
                self.error.emit(f"Errore durante l'invio diretto al driver Windows: {exc}")
                return

        # 2. Standard Qt QPainter fallback for non-Windows (macOS, Linux) or tests
        doc = None
        try:
            doc = fitz.open(self._pdf_path)
            self._printer.setResolution(self._resolution)
            if self._page_size is not None:
                self._printer.setPageSize(self._page_size)
            if self._orientation is not None:
                self._printer.setPageOrientation(self._orientation)
            self._printer.setFullPage(True)

            painter = QPainter(self._printer)
            if not painter.isActive():
                self.error.emit("Impossibile avviare il painter della stampante.")
                return

            total = len(self._sheet_indices)
            paper_rect_pt = self._printer.pageLayout().fullRectPoints()
            paper_w_pt = float(paper_rect_pt.width())
            paper_h_pt = float(paper_rect_pt.height())
            scale_px = self._printer.resolution() / 72.0

            for i, sheet_idx in enumerate(self._sheet_indices):
                if self._cancel:
                    break
                self.progress.emit(i + 1, total)

                if i > 0:
                    self._printer.newPage()

                if sheet_idx < 0 or sheet_idx >= len(doc):
                    continue

                page = doc[sheet_idx]
                sheet_w_pt = float(page.rect.width)
                sheet_h_pt = float(page.rect.height)

                target_w_pt, target_h_pt, offset_x_pt, offset_y_pt = compute_sheet_placement(
                    paper_w_pt, paper_h_pt, sheet_w_pt, sheet_h_pt,
                    scaling_mode=self._scaling_mode, center=self._center,
                )

                # Render page via PyMuPDF at exact device resolution
                matrix = fitz.Matrix(
                    scale_px * (target_w_pt / sheet_w_pt),
                    scale_px * (target_h_pt / sheet_h_pt),
                )
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                img = QImage(
                    pix.samples, pix.width, pix.height, pix.stride,
                    QImage.Format.Format_RGB888,
                )

                target_rect_px = QRectF(
                    offset_x_pt * scale_px,
                    offset_y_pt * scale_px,
                    float(pix.width),
                    float(pix.height),
                )
                painter.drawImage(target_rect_px, img)
                del pix

            painter.end()

            if not self._cancel:
                self.finished_printing.emit()
        except Exception as exc:
            self.error.emit(f"Errore durante l'invio allo spooler di stampa: {exc}")
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass


# ── The Professional Print Dialog ──────────────────────────

class PrintDialog(QDialog):
    """
    A complete and professional print preview and print dialog for Laydown.
    """

    def __init__(
        self,
        project: Project,
        pdf_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._project = project
        self._owns_temp_pdf = False
        self._pdf_path = pdf_path
        self._doc: Optional[fitz.Document] = None
        self._worker: Optional[_PrintWorker] = None

        # Prepare imposed PDF
        if not self._pdf_path:
            self._pdf_path, _ = impose_to_temp(self._project)
            self._owns_temp_pdf = True

        self._doc = fitz.open(self._pdf_path)
        self._total_sheets = len(self._doc)

        # Retrieve first sheet size in points & mm
        first_page = self._doc[0] if self._total_sheets > 0 else None
        self._sheet_w_pt = float(first_page.rect.width) if first_page else 595.28
        self._sheet_h_pt = float(first_page.rect.height) if first_page else 841.89
        self._sheet_w_mm = self._sheet_w_pt * 25.4 / 72.0
        self._sheet_h_mm = self._sheet_h_pt * 25.4 / 72.0

        # Initialize QPrinter
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        self._printer.setFullPage(True)

        # Settings persistence
        self._settings = QSettings("Laydown", "Laydown")
        saved_printer = self._settings.value("print/last_printer", "")
        self._show_printable_margins = self._settings.value("print/show_margins", True, type=bool)

        # Detect printers
        self._available_printers = QPrinterInfo.availablePrinters()
        default_name = QPrinterInfo.defaultPrinterName()
        target_name = saved_printer if any(p.printerName() == saved_printer for p in self._available_printers) else default_name
        if target_name:
            self._printer.setPrinterName(target_name)

        # Set default orientation based on imposed sheet aspect ratio
        if self._sheet_w_pt > self._sheet_h_pt:
            self._printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        else:
            self._printer.setPageOrientation(QPageLayout.Orientation.Portrait)

        # Set default duplex (from settings, or booklets/jobs with >= 2 sheets default to DuplexLongSide)
        saved_duplex = self._settings.value("print/duplex", None)
        if saved_duplex is not None:
            try:
                self._printer.setDuplex(QPrinter.DuplexMode(int(saved_duplex)))
            except Exception:
                if self._total_sheets > 1:
                    self._printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)
        elif self._total_sheets > 1:
            self._printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)

        self._current_sheet_indices: List[int] = list(range(self._total_sheets))
        self._preset = getattr(getattr(self._project, "sheet", None), "preset", "A3")
        self._last_was_draft = False
        self._active_devmode: Optional[bytes] = None

        self.setWindowTitle("Stampa — Laydown")
        self.resize(1120, 760)
        self.setMinimumSize(960, 640)

        self._build_ui()
        self._sync_printer_to_controls()
        self._update_active_devmode()
        self._update_overflow_warning()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SPACE_4, t.SPACE_3, t.SPACE_4, t.SPACE_3)
        root.setSpacing(t.SPACE_3)

        # ── Header bar with project badges ─────────────────
        header = QFrame()
        header.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 6px 12px;"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 6, 8, 6)
        h_layout.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(lucide("print", 22, t.ACCENT).pixmap(22, 22))
        h_layout.addWidget(icon_lbl)

        title_lbl = QLabel("Stampa di Produzione")
        title_lbl.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 700; color: {t.FG};")
        h_layout.addWidget(title_lbl)

        h_layout.addStretch(1)

        # Badges: Formato foglio imposto, Totale fogli
        sheet_dim_str = f"{self._sheet_w_mm:.1f} × {self._sheet_h_mm:.1f} mm"
        for label, val in [
            ("Formato foglio imposto", sheet_dim_str),
            ("Fogli totali", f"{self._total_sheets} fogli{'o' if self._total_sheets == 1 else ''}"),
        ]:
            b = QFrame()
            b.setStyleSheet(
                f"background: {t.INPUT_BG}; border: 1px solid {t.BORDER_SOFT};"
                f"border-radius: {t.RADIUS_SM}px; padding: 2px 8px;"
            )
            bl = QHBoxLayout(b)
            bl.setContentsMargins(6, 2, 6, 2)
            bl.setSpacing(6)
            lbl_w = QLabel(f"{label}:")
            lbl_w.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px;")
            val_w = QLabel(val)
            val_w.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_2XS}px; font-weight: 600;")
            bl.addWidget(lbl_w)
            bl.addWidget(val_w)
            h_layout.addWidget(b)

        root.addWidget(header)

        # ── Main content splitter (Left: Sidebar, Right: Canvas) ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {t.BORDER}; width: 2px; }}")

        # Left panel: Settings scroll area
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(330)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet(f"background: transparent;")

        sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 8, 0)
        sidebar_layout.setSpacing(t.SPACE_3)

        self._build_sidebar_sections(sidebar_layout)
        sidebar_layout.addStretch(1)

        left_scroll.setWidget(sidebar_widget)
        splitter.addWidget(left_scroll)

        # Right panel: Preview container
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(8, 0, 0, 0)
        preview_layout.setSpacing(t.SPACE_2)

        self._build_preview_area(preview_layout)
        splitter.addWidget(preview_container)

        splitter.setChildrenCollapsible(False)
        splitter.setSizes([380, 740])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # ── Bottom action & status bar ──────────────────────
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet(
            f"background: {t.RAISED}; border-top: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 6px 12px;"
        )
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(8, 6, 8, 6)
        bb_layout.setSpacing(t.SPACE_3)

        self._btn_driver_props = QPushButton("Proprietà driver…")
        self._btn_driver_props.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_driver_props.clicked.connect(self._on_open_driver_properties)
        bb_layout.addWidget(self._btn_driver_props)

        bb_layout.addSpacing(12)

        # Progress bar during background print
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, max(1, self._total_sheets))
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setVisible(False)
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER};"
            f"                border-radius: {t.RADIUS_SM}px; text-align: center;"
            f"                color: {t.FG}; font-size: {t.TEXT_2XS}px; }}"
            f"QProgressBar::chunk {{ background: {t.ACCENT}; border-radius: {t.RADIUS_SM}px; }}"
        )
        bb_layout.addWidget(self._progress_bar, 1)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        bb_layout.addWidget(self._status_lbl)

        bb_layout.addStretch(1)

        self._btn_cancel = QPushButton("Chiudi")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._on_cancel_or_close)
        bb_layout.addWidget(self._btn_cancel)

        self._btn_print_draft = QPushButton("  ⚡ Bozza Veloce (150 DPI)")
        self._btn_print_draft.setIcon(lucide("print", 16, t.FG))
        self._btn_print_draft.setToolTip(
            "Stampa rapida di prova a 150 DPI per verificare impostazioni, margini e piega in 1-2 secondi"
        )
        self._btn_print_draft.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_print_draft.clicked.connect(lambda: self._on_start_print(draft=True))
        bb_layout.addWidget(self._btn_print_draft)

        self._btn_print = QPushButton("  🖨️ Stampa Alta Definizione")
        self._btn_print.setIcon(lucide("print", 18, t.ACCENT_FG))
        self._btn_print.setProperty("accent", True)
        self._btn_print.setToolTip(
            "Stampa finale di produzione ad alta definizione (600 DPI / 1200 DPI)"
        )
        self._btn_print.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_print.clicked.connect(lambda: self._on_start_print(draft=False))
        bb_layout.addWidget(self._btn_print)

        root.addWidget(bottom_bar)

    # ── Sidebar Setup ──────────────────────────────────────────

    def _create_section_card(self, title: str) -> Tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 10px;"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet(
            f"font-size: {t.TEXT_2XS}px; font-weight: 700; color: {t.FG_MUTED};"
            f"letter-spacing: 0.5px; margin-bottom: 2px;"
        )
        layout.addWidget(title_lbl)
        return card, layout

    def _build_sidebar_sections(self, sidebar: QVBoxLayout):
        # 1. Stampante
        printer_card, p_layout = self._create_section_card("Stampante")
        self._combo_printers = QComboBox()
        self._combo_printers.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._available_printers:
            default_name = QPrinterInfo.defaultPrinterName()
            saved_printer = self._settings.value("print/last_printer", "")
            target_name = saved_printer if any(p.printerName() == saved_printer for p in self._available_printers) else default_name
            for p_info in self._available_printers:
                name = p_info.printerName()
                label = f"{name} (Predefinita)" if name == default_name else name
                self._combo_printers.addItem(label, name)
            # Select matching default/saved printer
            idx = self._combo_printers.findData(target_name)
            if idx >= 0:
                self._combo_printers.setCurrentIndex(idx)
        else:
            self._combo_printers.addItem("Nessuna stampante rilevata")
            self._combo_printers.setEnabled(False)

        self._combo_printers.currentIndexChanged.connect(self._on_printer_changed)
        p_layout.addWidget(self._combo_printers)
        sidebar.addWidget(printer_card)

        # 2. Fogli e Intervallo
        pages_card, pg_layout = self._create_section_card("Fogli da Stampare")
        self._range_group = QButtonGroup(self)

        self._radio_all = QRadioButton(f"Tutti i fogli ({self._total_sheets})")
        self._radio_all.setChecked(True)
        self._range_group.addButton(self._radio_all, 0)
        pg_layout.addWidget(self._radio_all)

        self._radio_current = QRadioButton("Foglio corrente")
        self._range_group.addButton(self._radio_current, 1)
        pg_layout.addWidget(self._radio_current)

        self._radio_custom = QRadioButton("Intervallo personalizzato:")
        self._range_group.addButton(self._radio_custom, 2)
        pg_layout.addWidget(self._radio_custom)

        self._range_edit = QLineEdit()
        self._range_edit.setPlaceholderText("es. 1-2, 4")
        self._range_edit.setEnabled(False)
        self._range_edit.textChanged.connect(self._on_range_text_changed)
        pg_layout.addWidget(self._range_edit)

        self._range_group.idToggled.connect(self._on_range_mode_changed)
        sidebar.addWidget(pages_card)

        # 3. Copie & Fascicolazione
        copies_card, c_layout = self._create_section_card("Copie")
        c_row = QHBoxLayout()
        c_row.setSpacing(8)

        c_lbl = QLabel("Numero copie:")
        c_lbl.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_SM}px;")
        c_row.addWidget(c_lbl)

        self._spin_copies = QSpinBox()
        self._spin_copies.setRange(1, 999)
        self._spin_copies.setValue(1)
        self._spin_copies.setFixedWidth(70)
        self._spin_copies.valueChanged.connect(self._on_copies_changed)
        c_row.addWidget(self._spin_copies)
        c_row.addStretch(1)
        c_layout.addLayout(c_row)

        self._chk_collate = QCheckBox("Fascicola copie")
        self._chk_collate.setChecked(True)
        self._chk_collate.setEnabled(False)
        self._chk_collate.toggled.connect(lambda v: self._printer.setCollateCopies(v))
        c_layout.addWidget(self._chk_collate)
        sidebar.addWidget(copies_card)

        # 4. Fronte / Retro (Duplex)
        duplex_card, d_layout = self._create_section_card("Fronte / Retro")
        self._combo_duplex = QComboBox()
        self._combo_duplex.addItem("Solo fronte (Simplex)", QPrinter.DuplexMode.DuplexNone)
        self._combo_duplex.addItem("Fronte/retro — Lato lungo", QPrinter.DuplexMode.DuplexLongSide)
        self._combo_duplex.addItem("Fronte/retro — Lato corto", QPrinter.DuplexMode.DuplexShortSide)
        saved_duplex = self._settings.value("print/duplex", None)
        if saved_duplex is not None:
            try:
                target_mode = QPrinter.DuplexMode(int(saved_duplex))
                d_idx = self._combo_duplex.findData(target_mode)
                if d_idx >= 0:
                    self._combo_duplex.setCurrentIndex(d_idx)
                elif self._total_sheets > 1:
                    self._combo_duplex.setCurrentIndex(1)
            except Exception:
                if self._total_sheets > 1:
                    self._combo_duplex.setCurrentIndex(1)
        elif self._total_sheets > 1:
            self._combo_duplex.setCurrentIndex(1)
        else:
            self._combo_duplex.setCurrentIndex(0)
        self._combo_duplex.currentIndexChanged.connect(self._on_duplex_changed)
        d_layout.addWidget(self._combo_duplex)
        sidebar.addWidget(duplex_card)

        # 5. Formato Carta Stampante & Orientamento
        paper_card, ppr_layout = self._create_section_card("Carta & Orientamento")
        paper_lbl = QLabel("Formato carta:")
        paper_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        ppr_layout.addWidget(paper_lbl)

        self._combo_paper = QComboBox()
        self._combo_paper.setCursor(Qt.CursorShape.PointingHandCursor)
        self._combo_paper.currentIndexChanged.connect(self._on_paper_size_changed)
        ppr_layout.addWidget(self._combo_paper)

        orient_lbl = QLabel("Orientamento:")
        orient_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px; margin-top: 4px;")
        ppr_layout.addWidget(orient_lbl)

        self._combo_orient = QComboBox()
        self._combo_orient.addItem("Automatico (in base al foglio)", "auto")
        self._combo_orient.addItem("Orizzontale (Landscape)", "landscape")
        self._combo_orient.addItem("Verticale (Portrait)", "portrait")
        self._combo_orient.currentIndexChanged.connect(self._on_orientation_changed)
        ppr_layout.addWidget(self._combo_orient)
        sidebar.addWidget(paper_card)

        # 6. Scala & Adattamento (Opzioni Prestampa)
        scale_card, s_layout = self._create_section_card("Scala e Geometria")
        self._combo_scale = QComboBox()
        self._combo_scale.addItem("100% — Dimensioni reali (Consigliato)", "actual")
        self._combo_scale.addItem("Adatta al foglio di stampa (Bozza)", "fit")
        self._combo_scale.addItem("Riduci se sovradimensionato", "shrink")
        saved_scale = self._settings.value("print/scaling_mode", "")
        if saved_scale:
            s_idx = self._combo_scale.findData(saved_scale)
            if s_idx >= 0:
                self._combo_scale.setCurrentIndex(s_idx)
        self._combo_scale.currentIndexChanged.connect(self._on_scale_mode_changed)
        s_layout.addWidget(self._combo_scale)

        self._chk_center = QCheckBox("Centra sul foglio")
        self._chk_center.setChecked(self._settings.value("print/center", True, type=bool))
        self._chk_center.toggled.connect(lambda _: (self._save_print_settings(), self._preview.updatePreview()))
        s_layout.addWidget(self._chk_center)

        # Warning card for unprintable margin overflow
        self._warning_frame = QFrame()
        self._warning_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._warning_frame.setStyleSheet(
            f"background: {t.mix(t.ACCENT, t.SURFACE, 0.2)};"
            f"border: 1px solid {t.ACCENT}; border-radius: {t.RADIUS_SM}px; padding: 6px;"
        )
        wf_layout = QVBoxLayout(self._warning_frame)
        wf_layout.setContentsMargins(8, 6, 8, 6)
        wf_layout.setSpacing(3)
        self._wf_title = QLabel("Avviso margini fisici")
        self._wf_title.setWordWrap(True)
        self._wf_title.setStyleSheet(f"color: {t.ACCENT}; font-size: {t.TEXT_2XS}px; font-weight: 700;")
        self._wf_text = QLabel("Il foglio imposto eccede l'area stampabile della carta selezionata.")
        self._wf_text.setWordWrap(True)
        self._wf_text.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_2XS}px;")
        wf_layout.addWidget(self._wf_title)
        wf_layout.addWidget(self._wf_text)
        s_layout.addWidget(self._warning_frame)
        self._warning_frame.setVisible(False)

        sidebar.addWidget(scale_card)

        # 7. Risoluzione di Stampa (DPI)
        dpi_card, dpi_layout = self._create_section_card("Qualità di Stampa")
        self._combo_dpi = QComboBox()
        self._combo_dpi.addItem("300 DPI — Bozza veloce / Standard", 300)
        self._combo_dpi.addItem("600 DPI — Alta qualità (Consigliato)", 600)
        self._combo_dpi.addItem("1200 DPI — Massima definizione", 1200)
        saved_dpi = self._settings.value("print/resolution", 600, type=int)
        dpi_idx = self._combo_dpi.findData(saved_dpi)
        self._combo_dpi.setCurrentIndex(dpi_idx if dpi_idx >= 0 else 1)
        self._combo_dpi.currentIndexChanged.connect(lambda _: self._save_print_settings())
        dpi_layout.addWidget(self._combo_dpi)
        sidebar.addWidget(dpi_card)

    # ── Preview Canvas Setup ───────────────────────────────────

    def _build_preview_area(self, preview_layout: QVBoxLayout):
        # Top toolbar
        toolbar = QFrame()
        toolbar.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 4px;"
        )
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)
        tb_layout.setSpacing(6)

        # Sheet navigation
        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedSize(28, 26)
        self._btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_prev.clicked.connect(self._on_prev_page)
        tb_layout.addWidget(self._btn_prev)

        self._sheet_nav_lbl = QLabel(f"Foglio 1 di {self._total_sheets}")
        self._sheet_nav_lbl.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_SM}px; font-weight: 600;")
        tb_layout.addWidget(self._sheet_nav_lbl)

        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedSize(28, 26)
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(self._on_next_page)
        tb_layout.addWidget(self._btn_next)

        tb_layout.addSpacing(16)

        # Zoom controls
        btn_zoom_in = QPushButton()
        btn_zoom_in.setIcon(lucide("zoom_in", 16, t.FG))
        btn_zoom_in.setToolTip("Ingrandisci (Ctrl++)")
        btn_zoom_in.setFixedSize(28, 26)
        btn_zoom_in.clicked.connect(lambda: self._preview.zoomIn(1.2))
        tb_layout.addWidget(btn_zoom_in)

        btn_zoom_out = QPushButton()
        btn_zoom_out.setIcon(lucide("zoom_out", 16, t.FG))
        btn_zoom_out.setToolTip("Riduci zoom (Ctrl+-)")
        btn_zoom_out.setFixedSize(28, 26)
        btn_zoom_out.clicked.connect(lambda: self._preview.zoomOut(1.2))
        tb_layout.addWidget(btn_zoom_out)

        btn_fit_page = QPushButton()
        btn_fit_page.setIcon(lucide("fit_page", 16, t.FG))
        btn_fit_page.setToolTip("Adatta pagina intera")
        btn_fit_page.setFixedSize(28, 26)
        btn_fit_page.clicked.connect(lambda: self._preview.fitInView())
        tb_layout.addWidget(btn_fit_page)

        btn_fit_width = QPushButton()
        btn_fit_width.setIcon(lucide("fit_width", 16, t.FG))
        btn_fit_width.setToolTip("Adatta larghezza")
        btn_fit_width.setFixedSize(28, 26)
        btn_fit_width.clicked.connect(lambda: self._preview.fitToWidth())
        tb_layout.addWidget(btn_fit_width)

        tb_layout.addSpacing(16)

        # View mode: Single vs Facing
        self._btn_single_view = QPushButton("Singola")
        self._btn_single_view.setCheckable(True)
        self._btn_single_view.setChecked(True)
        self._btn_single_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_single_view.setStyleSheet(
            f"QPushButton {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER};"
            f"                border-radius: {t.RADIUS_SM}px; padding: 3px 8px;"
            f"                color: {t.FG}; font-size: {t.TEXT_2XS}px; }}"
            f"QPushButton:checked {{ background: {t.RAISED}; font-weight: 600; }}"
        )
        self._btn_single_view.clicked.connect(self._set_single_page_view)
        tb_layout.addWidget(self._btn_single_view)

        self._btn_facing_view = QPushButton("Affiancata")
        self._btn_facing_view.setCheckable(True)
        self._btn_facing_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_facing_view.setStyleSheet(
            f"QPushButton {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER};"
            f"                border-radius: {t.RADIUS_SM}px; padding: 3px 8px;"
            f"                color: {t.FG}; font-size: {t.TEXT_2XS}px; }}"
            f"QPushButton:checked {{ background: {t.RAISED}; font-weight: 600; }}"
        )
        self._btn_facing_view.clicked.connect(self._set_facing_pages_view)
        tb_layout.addWidget(self._btn_facing_view)

        tb_layout.addStretch(1)

        self._chk_show_margins = QCheckBox("Mostra margini stampante")
        self._chk_show_margins.setChecked(self._show_printable_margins)
        self._chk_show_margins.toggled.connect(self._on_toggle_margins)
        tb_layout.addWidget(self._chk_show_margins)

        preview_layout.addWidget(toolbar)

        # QPrintPreviewWidget
        self._preview = QPrintPreviewWidget(self._printer, self)
        gv = self._preview.findChild(QGraphicsView)
        if gv:
            gv.setStyleSheet(f"background-color: {t.BG}; border: none;")
        self._preview.paintRequested.connect(self._on_paint_requested)
        self._preview.previewChanged.connect(self._on_preview_changed)
        preview_layout.addWidget(self._preview, 1)

    # ── Printer Synchronization ────────────────────────────────

    def _sync_printer_to_controls(self):
        """Populate paper sizes and sync controls with active QPrinter."""
        self._combo_paper.blockSignals(True)
        self._combo_paper.clear()

        info = QPrinterInfo(self._printer)
        supported_sizes = info.supportedPageSizes() if not info.isNull() else []

        selected_idx = 0
        if supported_sizes:
            selected_idx = find_best_matching_paper_size(
                supported_sizes,
                self._sheet_w_mm,
                self._sheet_h_mm,
                self._preset,
            )
            for idx, s in enumerate(supported_sizes):
                sz = s.size(QPageSize.Unit.Millimeter)
                label = f"{s.name()} ({sz.width():.0f} × {sz.height():.0f} mm)"
                self._combo_paper.addItem(label, s)
        else:
            # Fallback standard ISO / US sizes
            for s_id in [QPageSize.PageSizeId.A4, QPageSize.PageSizeId.A3, QPageSize.PageSizeId.Letter]:
                ps = QPageSize(s_id)
                sz = ps.size(QPageSize.Unit.Millimeter)
                self._combo_paper.addItem(f"{ps.name()} ({sz.width():.0f} × {sz.height():.0f} mm)", ps)
            if self._preset.upper() == "A3":
                selected_idx = 1

        if self._combo_paper.count() > 0:
            self._combo_paper.setCurrentIndex(selected_idx)
            chosen_size = self._combo_paper.itemData(selected_idx)
            if chosen_size:
                self._printer.setPageSize(chosen_size)
                # Smart scale selection: if sheet is larger than the paper,
                # default to 'fit' (bozza proporzionale), otherwise 'actual' (100% 1:1)
                ch_sz = chosen_size.size(QPageSize.Unit.Millimeter)
                if self._sheet_w_mm > 350.0 and (ch_sz.width() < 350.0 and ch_sz.height() < 350.0):
                    fit_idx = self._combo_scale.findData("fit")
                    if fit_idx >= 0:
                        self._combo_scale.setCurrentIndex(fit_idx)
                else:
                    act_idx = self._combo_scale.findData("actual")
                    if act_idx >= 0:
                        self._combo_scale.setCurrentIndex(act_idx)

        self._combo_paper.blockSignals(False)
        self._update_orientation()
        self._update_active_devmode()
        self._preview.updatePreview()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(80, self._preview.fitInView)

    def _save_print_settings(self):
        try:
            printer_name = self._combo_printers.currentData()
            if printer_name:
                self._settings.setValue("print/last_printer", printer_name)
            scale_mode = self._combo_scale.currentData()
            if scale_mode:
                self._settings.setValue("print/scaling_mode", scale_mode)
            self._settings.setValue("print/center", self._chk_center.isChecked())
            self._settings.setValue("print/show_margins", self._chk_show_margins.isChecked())
            dpi = self._combo_dpi.currentData()
            if dpi:
                self._settings.setValue("print/resolution", dpi)
            duplex = self._combo_duplex.currentData()
            if duplex is not None:
                val = duplex.value if hasattr(duplex, "value") else int(duplex)
                self._settings.setValue("print/duplex", val)
        except Exception:
            pass

    def _on_printer_changed(self, index: int):
        if index < 0:
            return
        printer_name = self._combo_printers.itemData(index)
        if printer_name:
            self._printer.setPrinterName(printer_name)
            self._active_devmode = None
            self._sync_printer_to_controls()
            self._update_overflow_warning()
            self._save_print_settings()
            QTimer.singleShot(50, self._preview.fitInView)

    def _on_paper_size_changed(self, index: int):
        if index < 0:
            return
        page_size = self._combo_paper.itemData(index)
        if page_size:
            self._printer.setPageSize(page_size)
            self._update_orientation()
            self._update_active_devmode()
            self._update_overflow_warning()
            self._preview.updatePreview()
            self._save_print_settings()
            QTimer.singleShot(50, self._preview.fitInView)

    def _on_orientation_changed(self):
        self._update_orientation()
        self._update_active_devmode()
        self._update_overflow_warning()
        self._preview.updatePreview()
        self._save_print_settings()
        QTimer.singleShot(50, self._preview.fitInView)

    def _update_orientation(self):
        mode = self._combo_orient.currentData()
        if mode == "landscape":
            self._printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        elif mode == "portrait":
            self._printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        else:  # "auto"
            if self._sheet_w_pt > self._sheet_h_pt:
                self._printer.setPageOrientation(QPageLayout.Orientation.Landscape)
            else:
                self._printer.setPageOrientation(QPageLayout.Orientation.Portrait)

    def _on_duplex_changed(self, index: int):
        mode = self._combo_duplex.itemData(index)
        if mode is not None:
            self._printer.setDuplex(mode)
            self._update_active_devmode()
            self._save_print_settings()

    def _on_copies_changed(self, val: int):
        self._printer.setCopyCount(val)
        self._chk_collate.setEnabled(val > 1)
        self._update_active_devmode()

    def _on_scale_mode_changed(self):
        self._update_overflow_warning()
        self._preview.updatePreview()
        self._save_print_settings()

    def _on_toggle_margins(self, checked: bool):
        self._show_printable_margins = checked
        self._preview.updatePreview()
        self._save_print_settings()

    def _set_single_page_view(self):
        self._btn_single_view.setChecked(True)
        self._btn_facing_view.setChecked(False)
        self._preview.setSinglePageViewMode()

    def _set_facing_pages_view(self):
        self._btn_single_view.setChecked(False)
        self._btn_facing_view.setChecked(True)
        self._preview.setFacingPagesViewMode()

    # ── Page Range Handling ───────────────────────────────────

    def _on_range_mode_changed(self, button_id: int):
        self._range_edit.setEnabled(button_id == 2)
        self._update_active_sheets()

    def _on_range_text_changed(self):
        if self._range_group.checkedId() == 2:
            self._update_active_sheets()

    def _update_active_sheets(self):
        mode_id = self._range_group.checkedId()
        self._range_edit.setStyleSheet("")
        try:
            if mode_id == 0:  # All
                self._current_sheet_indices = list(range(self._total_sheets))
            elif mode_id == 1:  # Current
                cur = max(0, min(self._preview.currentPage() - 1, self._total_sheets - 1))
                self._current_sheet_indices = [cur]
            elif mode_id == 2:  # Custom range
                text = self._range_edit.text()
                self._current_sheet_indices = parse_page_range(text, self._total_sheets)
            can_print = len(self._current_sheet_indices) > 0 and len(self._available_printers) > 0
            self._btn_print.setEnabled(can_print)
            self._btn_print_draft.setEnabled(can_print)
            self._preview.updatePreview()
        except ValueError:
            self._range_edit.setStyleSheet(f"border: 1px solid {t.DESTRUCTIVE};")
            self._btn_print.setEnabled(False)
            self._btn_print_draft.setEnabled(False)

    # ── Overflow Warning ──────────────────────────────────────

    def _update_overflow_warning(self):
        mode = self._combo_scale.currentData()
        paper_rect_pt = self._printer.pageLayout().fullRectPoints()
        paper_w_pt = float(paper_rect_pt.width())
        paper_h_pt = float(paper_rect_pt.height())

        printer_name = self._combo_printers.currentData() or ""
        m_l_pt, m_t_pt, m_r_pt, m_b_pt = win_print.get_printer_hardware_margins(
            printer_name, self._active_devmode
        )
        paint_w_pt = max(0.0, paper_w_pt - m_l_pt - m_r_pt)
        paint_h_pt = max(0.0, paper_h_pt - m_t_pt - m_b_pt)

        overflows_paper = (self._sheet_w_pt > paper_w_pt + 1.0) or (self._sheet_h_pt > paper_h_pt + 1.0)
        overflows_printable = (self._sheet_w_pt > paint_w_pt + 1.0) or (self._sheet_h_pt > paint_h_pt + 1.0)

        paper_name = self._combo_paper.currentText()
        paper_is_smaller = (self._sheet_w_mm > 350.0 and paper_w_pt * 25.4 / 72.0 < 350.0)

        if paper_is_smaller:
            if mode == "fit":
                self._wf_title.setText("Bozza ridotta su carta A4")
                self._wf_title.setStyleSheet(f"color: #0EA5E9; font-size: {t.TEXT_2XS}px; font-weight: 700;")
                self._warning_frame.setStyleSheet(
                    f"background: {t.mix('#0EA5E9', t.SURFACE, 0.15)};"
                    f"border: 1px solid #0EA5E9; border-radius: {t.RADIUS_SM}px; padding: 6px;"
                )
                self._wf_text.setText(
                    f"Il foglio imposto A3 ({self._sheet_w_mm:.0f}×{self._sheet_h_mm:.0f} mm) viene ridotto al 70.7% "
                    f"per entrare interamente nella carta {paper_name.split('(')[0].strip()} come bozza di verifica."
                )
                self._warning_frame.setVisible(True)
            else:
                self._wf_title.setText("Avviso formato carta A4")
                self._wf_title.setStyleSheet(f"color: {t.ACCENT}; font-size: {t.TEXT_2XS}px; font-weight: 700;")
                self._warning_frame.setStyleSheet(
                    f"background: {t.mix(t.ACCENT, t.SURFACE, 0.2)};"
                    f"border: 1px solid {t.ACCENT}; border-radius: {t.RADIUS_SM}px; padding: 6px;"
                )
                self._wf_text.setText(
                    f"Attenzione: il foglio imposto ({self._sheet_w_mm:.0f}×{self._sheet_h_mm:.0f} mm) è più grande "
                    f"della carta ({paper_name.split('(')[0].strip()}). A grandezza reale (100%) verrà stampata solo "
                    f"l'area centrale. Seleziona 'Adatta al foglio di stampa' per vedere l'intero foglio."
                )
                self._warning_frame.setVisible(True)
        elif mode == "actual" and overflows_paper:
            self._wf_title.setText("Avviso dimensioni foglio")
            self._wf_title.setStyleSheet(f"color: {t.ACCENT}; font-size: {t.TEXT_2XS}px; font-weight: 700;")
            self._warning_frame.setStyleSheet(
                f"background: {t.mix(t.ACCENT, t.SURFACE, 0.2)};"
                f"border: 1px solid {t.ACCENT}; border-radius: {t.RADIUS_SM}px; padding: 6px;"
            )
            self._wf_text.setText(
                f"Il foglio imposto ({self._sheet_w_mm:.0f}×{self._sheet_h_mm:.0f} mm) supera la dimensione "
                f"della carta ({paper_w_pt*25.4/72:.0f}×{paper_h_pt*25.4/72:.0f} mm). Parte dell'imposizione verrà tagliata."
            )
            self._warning_frame.setVisible(True)
        elif mode == "actual" and overflows_printable:
            self._wf_title.setText("Avviso margini fisici stampante")
            self._wf_title.setStyleSheet(f"color: {t.ACCENT}; font-size: {t.TEXT_2XS}px; font-weight: 700;")
            self._warning_frame.setStyleSheet(
                f"background: {t.mix(t.ACCENT, t.SURFACE, 0.2)};"
                f"border: 1px solid {t.ACCENT}; border-radius: {t.RADIUS_SM}px; padding: 6px;"
            )
            self._wf_text.setText(
                "Il foglio imposto si estende oltre i margini fisici stampabili della periferica. "
                "Crocini o elementi al vivo potrebbero essere troncati."
            )
            self._warning_frame.setVisible(True)
        else:
            paper_tag = "A3" if "A3" in paper_name.upper() else ("A4" if "A4" in paper_name.upper() else paper_name.split("(")[0].strip()[:10])
            self._wf_title.setText(f"Driver pronto: Carta {paper_tag} (1:1)")
            self._wf_title.setStyleSheet(f"color: #10B981; font-size: {t.TEXT_2XS}px; font-weight: 700;")
            self._warning_frame.setStyleSheet(
                f"background: {t.mix('#10B981', t.SURFACE, 0.15)};"
                f"border: 1px solid #10B981; border-radius: {t.RADIUS_SM}px; padding: 6px 8px;"
            )
            self._wf_text.setText(
                f"Stampa reale 1:1 a grandezza naturale (100% senza riduzioni) su carta {paper_tag}."
            )
            self._warning_frame.setVisible(True)

    # ── Preview Rendering (paintRequested) ────────────────────

    def _on_paint_requested(self, printer: QPrinter):
        """Render imposed sheets and printable boundary overlay onto QPrintPreviewWidget."""
        if not self._doc or self._total_sheets == 0:
            return

        painter = QPainter(printer)
        if not painter.isActive():
            return

        paper_rect_pt = printer.pageLayout().fullRectPoints()
        paper_w_pt = float(paper_rect_pt.width())
        paper_h_pt = float(paper_rect_pt.height())

        # Query true physical unprintable hardware margins (roller offsets)
        printer_name = self._combo_printers.currentData() or ""
        m_l_pt, m_t_pt, m_r_pt, m_b_pt = win_print.get_printer_hardware_margins(
            printer_name, self._active_devmode
        )
        paint_w_pt = max(0.0, paper_w_pt - m_l_pt - m_r_pt)
        paint_h_pt = max(0.0, paper_h_pt - m_t_pt - m_b_pt)

        scale_px = printer.resolution() / 72.0
        scaling_mode = self._combo_scale.currentData()
        center = self._chk_center.isChecked()

        sheets_to_draw = self._current_sheet_indices if self._current_sheet_indices else list(range(self._total_sheets))

        for idx, sheet_num in enumerate(sheets_to_draw):
            if idx > 0:
                printer.newPage()

            if sheet_num < 0 or sheet_num >= len(self._doc):
                continue

            page = self._doc[sheet_num]
            sheet_w_pt = float(page.rect.width)
            sheet_h_pt = float(page.rect.height)

            target_w_pt, target_h_pt, offset_x_pt, offset_y_pt = compute_sheet_placement(
                paper_w_pt, paper_h_pt, sheet_w_pt, sheet_h_pt,
                scaling_mode=scaling_mode, center=center,
            )

            # Render sheet via PyMuPDF at preview resolution
            matrix = fitz.Matrix(
                scale_px * (target_w_pt / sheet_w_pt),
                scale_px * (target_h_pt / sheet_h_pt),
            )
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)

            target_rect_px = QRectF(
                offset_x_pt * scale_px,
                offset_y_pt * scale_px,
                float(pix.width),
                float(pix.height),
            )
            painter.drawImage(target_rect_px, img)
            del pix

            # Draw printable margins dashed boundary and shaded strips if enabled
            if self._show_printable_margins:
                painter.save()
                margin_fill = QColor(239, 68, 68, 28)
                paper_w_px = paper_w_pt * scale_px
                paper_h_px = paper_h_pt * scale_px

                mx0 = m_l_pt * scale_px
                my0 = m_t_pt * scale_px
                mw = paint_w_pt * scale_px
                mh = paint_h_pt * scale_px
                mx1 = mx0 + mw
                my1 = my0 + mh

                # 1. Soft shaded strips on the 4 unprintable edges
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(margin_fill)
                if my0 > 0:
                    painter.drawRect(QRectF(0, 0, paper_w_px, my0))
                if my1 < paper_h_px:
                    painter.drawRect(QRectF(0, my1, paper_w_px, paper_h_px - my1))
                if mx0 > 0:
                    painter.drawRect(QRectF(0, my0, mx0, mh))
                if mx1 < paper_w_px:
                    painter.drawRect(QRectF(mx1, my0, paper_w_px - mx1, mh))

                # 2. Crisp, cosmetic dashed line around the printable boundary
                pen = QPen(QColor(239, 68, 68, 220), 2.0, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                printable_rect_px = QRectF(mx0, my0, mw, mh)
                painter.drawRect(printable_rect_px)
                painter.restore()

        painter.end()

    def _on_preview_changed(self):
        cur = self._preview.currentPage()
        total = self._preview.pageCount()
        self._sheet_nav_lbl.setText(f"Foglio {cur} di {total}")
        self._btn_prev.setEnabled(cur > 1)
        self._btn_next.setEnabled(cur < total)

    def _on_prev_page(self):
        cur = self._preview.currentPage()
        if cur > 1:
            self._preview.setCurrentPage(cur - 1)

    def _on_next_page(self):
        cur = self._preview.currentPage()
        if cur < self._preview.pageCount():
            self._preview.setCurrentPage(cur + 1)

    # ── Windows DEVMODE Synchronization ───────────────────────

    def _update_active_devmode(self):
        """Synchronize UI settings into active Windows DEVMODE."""
        if not win_print.is_windows():
            return

        printer_name = self._combo_printers.currentData()
        if not printer_name:
            return

        page_size = self._combo_paper.currentData()
        paper_id, custom_w, custom_h = win_print.map_qpagesize_to_dmpaper(page_size)

        orient_data = self._combo_orient.currentData()
        if orient_data == "landscape":
            orient_id = win_print.DMORIENT_LANDSCAPE
        elif orient_data == "portrait":
            orient_id = win_print.DMORIENT_PORTRAIT
        else:  # "auto"
            orient_id = win_print.DMORIENT_LANDSCAPE if self._sheet_w_pt > self._sheet_h_pt else win_print.DMORIENT_PORTRAIT

        duplex_data = self._combo_duplex.currentData()
        if duplex_data == QPrinter.DuplexMode.DuplexLongSide:
            duplex_id = win_print.DMDUP_VERTICAL
        elif duplex_data == QPrinter.DuplexMode.DuplexShortSide:
            duplex_id = win_print.DMDUP_HORIZONTAL
        else:
            duplex_id = win_print.DMDUP_SIMPLEX

        copies = self._spin_copies.value()
        collate = self._chk_collate.isChecked()

        merged = win_print.merge_devmode(
            printer_name=printer_name,
            in_devmode=self._active_devmode,
            paper_size_id=paper_id,
            orientation=orient_id,
            duplex=duplex_id,
            copies=copies,
            collate=collate,
            custom_width_mm=custom_w,
            custom_height_mm=custom_h,
        )
        if merged:
            self._active_devmode = merged

    def _sync_controls_from_devmode(self, devmode_bytes: bytes):
        """Update dialog controls when settings were modified inside native driver dialog."""
        settings = win_print.parse_devmode_settings(devmode_bytes)
        if not settings:
            return

        # 1. Paper size
        paper_id = settings.get("paper_size")
        target_name = None
        if paper_id == win_print.DMPAPER_A3:
            target_name = "A3"
        elif paper_id == win_print.DMPAPER_A4:
            target_name = "A4"
        elif paper_id == win_print.DMPAPER_A5:
            target_name = "A5"
        elif paper_id in (win_print.DMPAPER_TABLOID, win_print.DMPAPER_LEDGER):
            target_name = "Tabloid"

        if target_name:
            for idx in range(self._combo_paper.count()):
                ps = self._combo_paper.itemData(idx)
                if ps and (target_name in ps.name().upper() or target_name == getattr(ps.id(), "name", "")):
                    self._combo_paper.blockSignals(True)
                    self._combo_paper.setCurrentIndex(idx)
                    self._printer.setPageSize(ps)
                    self._combo_paper.blockSignals(False)
                    break

        # 2. Orientation
        orient_id = settings.get("orientation")
        self._combo_orient.blockSignals(True)
        if orient_id == win_print.DMORIENT_LANDSCAPE:
            idx = self._combo_orient.findData("landscape")
            if idx >= 0:
                self._combo_orient.setCurrentIndex(idx)
            self._printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        elif orient_id == win_print.DMORIENT_PORTRAIT:
            idx = self._combo_orient.findData("portrait")
            if idx >= 0:
                self._combo_orient.setCurrentIndex(idx)
            self._printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        self._combo_orient.blockSignals(False)

        # 3. Duplex
        duplex_id = settings.get("duplex")
        self._combo_duplex.blockSignals(True)
        if duplex_id == win_print.DMDUP_VERTICAL:
            self._combo_duplex.setCurrentIndex(1)
            self._printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)
        elif duplex_id == win_print.DMDUP_HORIZONTAL:
            self._combo_duplex.setCurrentIndex(2)
            self._printer.setDuplex(QPrinter.DuplexMode.DuplexShortSide)
        elif duplex_id == win_print.DMDUP_SIMPLEX:
            self._combo_duplex.setCurrentIndex(0)
            self._printer.setDuplex(QPrinter.DuplexMode.DuplexNone)
        self._combo_duplex.blockSignals(False)

        # 4. Copies
        copies = settings.get("copies", 1)
        if copies > 0:
            self._spin_copies.blockSignals(True)
            self._spin_copies.setValue(copies)
            self._printer.setCopyCount(copies)
            self._chk_collate.setEnabled(copies > 1)
            self._spin_copies.blockSignals(False)

    # ── Driver Properties Dialog ──────────────────────────────

    def _on_open_driver_properties(self):
        if win_print.is_windows():
            self._update_active_devmode()
            printer_name = self._combo_printers.currentData()
            if not printer_name:
                return
            hwnd = int(self.winId())
            new_devmode = win_print.open_native_driver_dialog(hwnd, printer_name, self._active_devmode)
            if new_devmode is not None:
                self._active_devmode = new_devmode
                self._sync_controls_from_devmode(new_devmode)
                self._update_overflow_warning()
                self._preview.updatePreview()
        else:
            dialog = QPrintDialog(self._printer, self)
            dialog.setWindowTitle("Proprietà stampante")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._sync_printer_to_controls()
                self._update_overflow_warning()
                self._preview.updatePreview()

    # ── Print Execution ───────────────────────────────────────

    def _on_start_print(self, draft: bool = False):
        if not self._current_sheet_indices:
            QMessageBox.warning(self, "Attenzione", "Nessun foglio valido selezionato per la stampa.")
            return

        if not self._available_printers:
            QMessageBox.critical(self, "Errore", "Nessuna stampante rilevata sul sistema.")
            return

        self._last_was_draft = draft

        # Prepare UI for background printing
        self._btn_print.setEnabled(False)
        self._btn_print_draft.setEnabled(False)
        self._btn_cancel.setText("Annulla invio")
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(len(self._current_sheet_indices))

        resolution = 150 if draft else (self._combo_dpi.currentData() or 600)
        label_mode = "bozza veloce (150 DPI)" if draft else f"alta definizione ({resolution} DPI)"
        self._status_lbl.setText(f"Invio {label_mode} in corso…")

        scaling_mode = self._combo_scale.currentData()
        center = self._chk_center.isChecked()

        selected_page_size = self._combo_paper.currentData()
        if selected_page_size:
            self._printer.setPageSize(selected_page_size)
        self._update_orientation()
        self._update_active_devmode()
        self._save_print_settings()

        printer_name = self._combo_printers.currentData()
        project_name = getattr(self._project, "name", "") or "Progetto"

        self._worker = _PrintWorker(
            printer=self._printer,
            pdf_path=self._pdf_path,
            sheet_indices=self._current_sheet_indices,
            scaling_mode=scaling_mode,
            center=center,
            resolution=resolution,
            page_size=selected_page_size,
            orientation=self._printer.pageLayout().orientation(),
            printer_name=printer_name,
            devmode_bytes=self._active_devmode,
            project_name=project_name,
            parent=self,
        )
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished_printing.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_worker_progress(self, current: int, total: int):
        self._progress_bar.setValue(current)
        mode_str = "Bozza" if self._last_was_draft else "Produzione"
        self._status_lbl.setText(f"Spooling {mode_str}: foglio {current} di {total}…")

    def _on_worker_finished(self):
        self._progress_bar.setVisible(False)
        self._btn_print.setEnabled(True)
        self._btn_print_draft.setEnabled(True)
        self._btn_cancel.setText("Chiudi")
        total = len(self._current_sheet_indices)
        mode_str = "Bozza veloce" if self._last_was_draft else "Alta definizione"
        self._status_lbl.setText(
            f"✅ Stampa completata ({mode_str}): {total} fogli{'o' if total == 1 else ''} inviati allo spooler."
        )
        # Il dialog rimane aperto come richiesto: l'utente chiuderà manualmente la finestra

    def _on_worker_error(self, err_msg: str):
        self._btn_print.setEnabled(True)
        self._btn_print_draft.setEnabled(True)
        self._btn_cancel.setText("Chiudi")
        self._progress_bar.setVisible(False)
        self._status_lbl.setText("Errore durante la stampa.")
        QMessageBox.critical(self, "Errore di stampa", err_msg)

    def close_document(self):
        """Cancel worker, close fitz document, save settings, and remove temp file if owned."""
        self._save_print_settings()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
            self._worker = None
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None
        if self._owns_temp_pdf and self._pdf_path and os.path.exists(self._pdf_path):
            try:
                os.unlink(self._pdf_path)
            except Exception:
                pass
            self._pdf_path = None

    def _on_cancel_or_close(self):
        self.close_document()
        self.reject()

    def reject(self):
        self.close_document()
        super().reject()

    def accept(self):
        self.close_document()
        super().accept()

    def closeEvent(self, event):
        self.close_document()
        if event is not None:
            super().closeEvent(event)
