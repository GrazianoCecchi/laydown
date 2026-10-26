"""
PDF Inspector Dialog — inspect document metadata, page boundaries, and box overlays.

Displays MediaBox, CropBox, BleedBox, TrimBox, and ArtBox dimensions with
real-time multi-color vector overlays on top of the rendered page.
"""

from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import fitz

from laydown.engine.inspector import (
    BoxInfo, DocumentInspection, PageInspection, inspect_document,
)
from laydown.ui import theme as t
from laydown.ui.icons import lucide


# Industry-standard distinctive palette for PDF boxes
BOX_COLORS: Dict[str, QColor] = {
    "MediaBox": QColor("#94A3B8"),   # Slate grey
    "CropBox": QColor("#10B981"),    # Emerald green
    "BleedBox": QColor("#0EA5E9"),   # Cyan / Sky blue
    "TrimBox": QColor("#EC4899"),    # Vivid magenta
    "ArtBox": QColor("#F59E0B"),     # Amber / Gold
}

BOX_PEN_STYLES: Dict[str, Qt.PenStyle] = {
    "MediaBox": Qt.PenStyle.DashLine,
    "CropBox": Qt.PenStyle.DashDotLine,
    "BleedBox": Qt.PenStyle.DashLine,
    "TrimBox": Qt.PenStyle.SolidLine,
    "ArtBox": Qt.PenStyle.DotLine,
}


class _PageBoxCanvas(QWidget):
    """
    Renders the PDF page and overlays active boxes in contrasting colors.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc: Optional[fitz.Document] = None
        self._page_idx: int = 0
        self._page_info: Optional[PageInspection] = None
        self._active_boxes: Dict[str, bool] = {
            "MediaBox": True,
            "CropBox": True,
            "BleedBox": True,
            "TrimBox": True,
            "ArtBox": False,
        }
        self._show_labels = True
        self._zoom = 1.0
        self._pixmap: Optional[QPixmap] = None
        self.setStyleSheet(f"background: {t.BG};")

    def set_data(self, doc: fitz.Document, page_idx: int, page_info: PageInspection):
        self._doc = doc
        self._page_idx = page_idx
        self._page_info = page_info
        self._render_pixmap()
        self.update()

    def set_box_visible(self, box_name: str, visible: bool):
        self._active_boxes[box_name] = visible
        self.update()

    def set_show_labels(self, show: bool):
        self._show_labels = show
        self.update()

    def set_zoom(self, zoom: float):
        self._zoom = max(0.2, min(5.0, zoom))
        self._render_pixmap()
        self.update()

    def zoom_in(self):
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self._zoom / 1.25)

    def reset_zoom(self):
        self.set_zoom(1.0)

    def _render_pixmap(self):
        if not self._doc or self._page_idx >= len(self._doc):
            self._pixmap = None
            return

        page = self._doc[self._page_idx]
        # Render with base 1.5x scale * zoom for crisp text & graphics
        dpi_scale = 1.5 * self._zoom
        mat = fitz.Matrix(dpi_scale, dpi_scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(img)
        self.setMinimumSize(int(self._pixmap.width()), int(self._pixmap.height()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw dark canvas background
        painter.fillRect(self.rect(), QColor(t.BG))

        if not self._pixmap or not self._doc or not self._page_info:
            painter.setPen(QColor(t.FG_FAINT))
            painter.setFont(QFont(t.FONT_FAMILY, 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nessuna pagina caricata")
            painter.end()
            return

        # Position page centered in available viewport or top-left
        ox = max(20, (self.width() - self._pixmap.width()) // 2)
        oy = max(20, (self.height() - self._pixmap.height()) // 2)

        # Paint page pixmap
        painter.drawPixmap(ox, oy, self._pixmap)

        # Paint thin page boundary shadow/border
        painter.setPen(QPen(QColor(t.BORDER), 1))
        painter.drawRect(ox - 1, oy - 1, self._pixmap.width() + 2, self._pixmap.height() + 2)

        # Calculate scale factor from visual PDF points to pixmap pixels
        page = self._doc[self._page_idx]
        pw_pt = page.rect.width
        scale = self._pixmap.width() / pw_pt if pw_pt > 0 else 1.0

        # Draw overlays for each active box
        font = QFont(t.FONT_FAMILY, 9)
        painter.setFont(font)

        # Order to draw: MediaBox -> CropBox -> BleedBox -> ArtBox -> TrimBox (TrimBox on top)
        draw_order = ["MediaBox", "CropBox", "BleedBox", "ArtBox", "TrimBox"]

        for name in draw_order:
            if not self._active_boxes.get(name, False):
                continue
            box: Optional[BoxInfo] = self._page_info.boxes.get(name)
            if not box:
                continue

            # Visual coordinates in page.rect (pixmap) space
            vx0, vy0, vx1, vy1 = box.visual_rect
            px0 = ox + vx0 * scale
            py0 = oy + vy0 * scale
            pw = (vx1 - vx0) * scale
            ph = (vy1 - vy0) * scale

            rect = QRectF(px0, py0, pw, ph)
            color = BOX_COLORS.get(name, QColor(t.ACCENT))
            pen_style = BOX_PEN_STYLES.get(name, Qt.PenStyle.SolidLine)
            pen_width = 2.0 if name == "TrimBox" else 1.5

            # Semi-transparent wash for TrimBox
            if name == "TrimBox":
                fill_color = QColor(color)
                fill_color.setAlpha(15)
                painter.fillRect(rect, fill_color)

            # Draw boundary rect
            pen = QPen(color, pen_width, pen_style)
            painter.setPen(pen)
            painter.drawRect(rect)

            # Draw tag label in corner
            if self._show_labels:
                tag_text = f" {name}: {box.dimensions_mm_str} "
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(tag_text) + 4
                th = fm.height() + 2

                # Position tag inside or adjacent to top-left of box
                tag_x = px0 + 2
                tag_y = py0 + 2 if name != "MediaBox" else py0 + th + 4
                tag_rect = QRectF(tag_x, tag_y, tw, th)

                bg_col = QColor(t.RAISED)
                bg_col.setAlpha(220)
                painter.fillRect(tag_rect, bg_col)
                painter.setPen(QPen(color, 1))
                painter.drawRect(tag_rect)
                painter.setPen(color)
                painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag_text)

        painter.end()


class PdfInspectorDialog(QDialog):
    """
    Complete PDF inspection dialog with document metadata, boundary boxes,
    and visual overlay inspector.
    """

    def __init__(self, doc: fitz.Document, file_path: str = "", parent=None):
        super().__init__(parent)
        self._doc = doc
        self._path = file_path
        self._inspection = inspect_document(doc, file_path)
        self._current_page_idx = 0

        self.setWindowTitle(f"Ispezione PDF — {self._inspection.file_name}")
        self.resize(1150, 780)
        self.setMinimumSize(950, 640)

        self._setup_ui()
        self._load_page(0)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SPACE_3, t.SPACE_3, t.SPACE_3, t.SPACE_3)
        root.setSpacing(t.SPACE_2)

        # ── Top header strip with document highlights ────────
        header = QFrame()
        header.setStyleSheet(
            f"background: {t.RAISED}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 6px 12px;"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(4, 4, 4, 4)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(lucide("inspect", 24, t.ACCENT).pixmap(24, 24))
        h_layout.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        name_lbl = QLabel(self._inspection.file_name)
        name_lbl.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 600; color: {t.FG};")
        path_lbl = QLabel(self._inspection.file_path or "In memoria")
        path_lbl.setStyleSheet(f"font-size: {t.TEXT_2XS}px; color: {t.FG_MUTED};")
        title_col.addWidget(name_lbl)
        title_col.addWidget(path_lbl)
        h_layout.addLayout(title_col, 1)

        # Quick badges
        badges_layout = QHBoxLayout()
        badges_layout.setSpacing(8)
        for label, val in [
            ("Pagine", str(self._inspection.page_count)),
            ("Versione", self._inspection.pdf_version),
            ("Dimensione", self._inspection.file_size_formatted),
        ]:
            b = QFrame()
            b.setStyleSheet(
                f"background: {t.INPUT_BG}; border: 1px solid {t.BORDER_SOFT};"
                f"border-radius: {t.RADIUS_SM}px; padding: 2px 8px;"
            )
            bl = QHBoxLayout(b)
            bl.setContentsMargins(4, 2, 4, 2)
            bl.setSpacing(4)
            lbl_w = QLabel(f"{label}:")
            lbl_w.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px;")
            val_w = QLabel(val)
            val_w.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_2XS}px; font-weight: 600;")
            bl.addWidget(lbl_w)
            bl.addWidget(val_w)
            badges_layout.addWidget(b)

        h_layout.addLayout(badges_layout)
        root.addWidget(header)

        # ── Main content splitter (Left: info & boxes, Right: visual overlay canvas) ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {t.BORDER}; width: 2px; }}")

        # Left panel: tabs or sections
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(t.SPACE_2)

        # 1. Page navigation bar
        nav_box = QFrame()
        nav_box.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 6px;"
        )
        nav_row = QHBoxLayout(nav_box)
        nav_row.setContentsMargins(6, 4, 6, 4)
        nav_row.setSpacing(8)

        nav_lbl = QLabel("Pagina:")
        nav_lbl.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_SM}px; font-weight: 600;")
        nav_row.addWidget(nav_lbl)

        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedSize(28, 26)
        self._btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_prev.clicked.connect(self._on_prev_page)
        nav_row.addWidget(self._btn_prev)

        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, max(1, self._inspection.page_count))
        self._page_spin.setValue(1)
        self._page_spin.setFixedWidth(64)
        self._page_spin.valueChanged.connect(lambda v: self._load_page(v - 1))
        nav_row.addWidget(self._page_spin)

        self._total_pages_lbl = QLabel(f"di {self._inspection.page_count}")
        self._total_pages_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_SM}px;")
        nav_row.addWidget(self._total_pages_lbl)

        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedSize(28, 26)
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(self._on_next_page)
        nav_row.addWidget(self._btn_next)

        nav_row.addStretch(1)

        self._rot_badge = QLabel("Rotazione: 0°")
        self._rot_badge.setStyleSheet(
            f"background: {t.INPUT_BG}; color: {t.FG_MUTED}; border: 1px solid {t.BORDER_SOFT};"
            f"border-radius: {t.RADIUS_SM}px; padding: 2px 6px; font-size: {t.TEXT_2XS}px;"
        )
        nav_row.addWidget(self._rot_badge)
        left_layout.addWidget(nav_box)

        # 2. Box details table & toggles
        boxes_group = QGroupBox("Riquadri Pagina (Page Boxes)")
        boxes_group.setStyleSheet(
            f"QGroupBox {{ font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};"
            f"              border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px;"
            f"              margin-top: 10px; padding-top: 14px; background: {t.SURFACE}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
        )
        bg_layout = QVBoxLayout(boxes_group)
        bg_layout.setContentsMargins(8, 8, 8, 8)
        bg_layout.setSpacing(6)

        # Table showing each box
        self._boxes_table = QTableWidget()
        self._boxes_table.setColumnCount(4)
        self._boxes_table.setHorizontalHeaderLabels(["Riquadro", "Dimensioni (mm)", "Dimensioni (pt)", "Stato"])
        self._boxes_table.horizontalHeader().setStretchLastSection(True)
        self._boxes_table.verticalHeader().setVisible(False)
        self._boxes_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._boxes_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._boxes_table.setStyleSheet(
            f"QTableWidget {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER_SOFT};"
            f"                gridline-color: {t.BORDER_SOFT}; font-size: {t.TEXT_XS}px; }}"
            f"QHeaderView::section {{ background: {t.RAISED}; color: {t.FG_MUTED}; border: 1px solid {t.BORDER_SOFT}; padding: 4px; font-size: {t.TEXT_2XS}px; }}"
        )
        self._boxes_table.setFixedHeight(180)
        bg_layout.addWidget(self._boxes_table)

        # Checkboxes row for toggling overlays
        toggles_frame = QFrame()
        t_row = QHBoxLayout(toggles_frame)
        t_row.setContentsMargins(0, 4, 0, 0)
        t_row.setSpacing(10)

        self._box_checks: Dict[str, QCheckBox] = {}
        for box_name in ["MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox"]:
            cb = QCheckBox(box_name)
            col = BOX_COLORS[box_name].name()
            cb.setChecked(self._active_boxes_default(box_name))
            cb.setStyleSheet(
                f"QCheckBox {{ color: {col}; font-weight: 600; font-size: {t.TEXT_2XS}px; }}"
                f"QCheckBox::indicator:checked {{ background: {col}; border: 1px solid {col}; }}"
            )
            cb.toggled.connect(lambda state, n=box_name: self._canvas.set_box_visible(n, state))
            self._box_checks[box_name] = cb
            t_row.addWidget(cb)

        t_row.addStretch(1)
        bg_layout.addWidget(toggles_frame)
        left_layout.addWidget(boxes_group)

        # 3. Document Metadata Accordion / Group
        meta_group = QGroupBox("Proprietà e Metadati Documento")
        meta_group.setStyleSheet(
            f"QGroupBox {{ font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};"
            f"              border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px;"
            f"              margin-top: 10px; padding-top: 14px; background: {t.SURFACE}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
        )
        meta_layout = QVBoxLayout(meta_group)
        meta_layout.setContentsMargins(8, 8, 8, 8)

        meta_scroll = QScrollArea()
        meta_scroll.setWidgetResizable(True)
        meta_scroll.setFrameShape(QFrame.Shape.NoFrame)
        meta_content = QWidget()
        meta_grid = QGridLayout(meta_content)
        meta_grid.setContentsMargins(4, 4, 4, 4)
        meta_grid.setSpacing(6)

        meta_fields = [
            ("Titolo", self._inspection.title or "—"),
            ("Autore", self._inspection.author or "—"),
            ("Oggetto", self._inspection.subject or "—"),
            ("Creatore", self._inspection.creator or "—"),
            ("Produttore", self._inspection.producer or "—"),
            ("Data creazione", self._inspection.creation_date or "—"),
            ("Data modifica", self._inspection.mod_date or "—"),
            ("Crittografia", "Sì (Protetto)" if self._inspection.is_encrypted else "Nessuna"),
        ]

        for row, (label, val) in enumerate(meta_fields):
            lbl_w = QLabel(f"{label}:")
            lbl_w.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px; font-weight: 500;")
            lbl_w.setFixedWidth(100)
            val_w = QLabel(val)
            val_w.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px;")
            val_w.setWordWrap(True)
            meta_grid.addWidget(lbl_w, row, 0, Qt.AlignmentFlag.AlignTop)
            meta_grid.addWidget(val_w, row, 1, Qt.AlignmentFlag.AlignTop)

        meta_scroll.setWidget(meta_content)
        meta_layout.addWidget(meta_scroll)
        left_layout.addWidget(meta_group, 1)

        splitter.addWidget(left_widget)

        # Right panel: visual overlay canvas with its toolbar
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(4)

        # Overlay Toolbar
        tb = QFrame()
        tb.setFixedHeight(36)
        tb.setStyleSheet(
            f"background: {t.RAISED}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 2px 8px;"
        )
        tb_row = QHBoxLayout(tb)
        tb_row.setContentsMargins(4, 2, 4, 2)
        tb_row.setSpacing(6)

        lbl_ov = QLabel("Anteprima Sovrapposizione Riquadri:")
        lbl_ov.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px; font-weight: 600;")
        tb_row.addWidget(lbl_ov)

        tb_row.addStretch(1)

        self._labels_check = QCheckBox("Mostra etichette")
        self._labels_check.setChecked(True)
        self._labels_check.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_2XS}px;")
        self._labels_check.toggled.connect(lambda s: self._canvas.set_show_labels(s))
        tb_row.addWidget(self._labels_check)

        btn_zin = QPushButton("＋")
        btn_zin.setToolTip("Ingrandisci")
        btn_zin.setFixedSize(26, 24)
        btn_zin.clicked.connect(lambda: self._canvas.zoom_in())
        tb_row.addWidget(btn_zin)

        btn_zout = QPushButton("－")
        btn_zout.setToolTip("Riduci zoom")
        btn_zout.setFixedSize(26, 24)
        btn_zout.clicked.connect(lambda: self._canvas.zoom_out())
        tb_row.addWidget(btn_zout)

        btn_zreset = QPushButton("1:1")
        btn_zreset.setToolTip("Ripristina zoom 100%")
        btn_zreset.setFixedSize(32, 24)
        btn_zreset.clicked.connect(lambda: self._canvas.reset_zoom())
        tb_row.addWidget(btn_zreset)

        right_layout.addWidget(tb)

        # Scroll area for canvas
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px; }}")

        self._canvas = _PageBoxCanvas()
        scroll.setWidget(self._canvas)
        right_layout.addWidget(scroll, 1)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)

        root.addWidget(splitter, 1)

        # ── Dialog footer buttons ────────────────────────────
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.close)
        root.addWidget(btn_box)

    @staticmethod
    def _active_boxes_default(box_name: str) -> bool:
        return box_name in ("MediaBox", "CropBox", "BleedBox", "TrimBox")

    def _load_page(self, page_idx: int):
        if page_idx < 0 or page_idx >= self._inspection.page_count:
            return
        self._current_page_idx = page_idx
        page_info = self._inspection.pages[page_idx]

        # Update page spin & navigation
        self._page_spin.blockSignals(True)
        self._page_spin.setValue(page_idx + 1)
        self._page_spin.blockSignals(False)
        self._btn_prev.setEnabled(page_idx > 0)
        self._btn_next.setEnabled(page_idx < self._inspection.page_count - 1)
        self._rot_badge.setText(f"Rotazione: {page_info.rotation}°")

        # Update boxes table
        self._boxes_table.setRowCount(0)
        boxes_list = [
            ("TrimBox", page_info.trimbox),
            ("BleedBox", page_info.bleedbox),
            ("CropBox", page_info.cropbox),
            ("MediaBox", page_info.mediabox),
            ("ArtBox", page_info.artbox),
        ]

        self._boxes_table.setRowCount(len(boxes_list))
        for row, (name, box) in enumerate(boxes_list):
            color = BOX_COLORS.get(name, QColor(t.FG))

            # Column 0: Name with colored badge
            item_name = QTableWidgetItem(f"● {name}")
            item_name.setForeground(color)
            item_name.setFont(QFont(t.FONT_FAMILY, 9, QFont.Weight.Bold))
            self._boxes_table.setItem(row, 0, item_name)

            # Column 1: Dimensions in mm
            item_mm = QTableWidgetItem(box.dimensions_mm_str)
            item_mm.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._boxes_table.setItem(row, 1, item_mm)

            # Column 2: Dimensions in pt
            item_pt = QTableWidgetItem(box.dimensions_pt_str)
            item_pt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_pt.setForeground(QColor(t.FG_MUTED))
            self._boxes_table.setItem(row, 2, item_pt)

            # Column 3: Status (Dichiarato vs Derivato)
            status_text = "Dichiarato" if box.is_explicit else "Derivato"
            item_status = QTableWidgetItem(status_text)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if box.is_explicit:
                item_status.setForeground(QColor("#10B981"))  # Green
            else:
                item_status.setForeground(QColor(t.FG_FAINT))
            self._boxes_table.setItem(row, 3, item_status)

        # Update canvas
        self._canvas.set_data(self._doc, page_idx, page_info)

    def _on_prev_page(self):
        if self._current_page_idx > 0:
            self._load_page(self._current_page_idx - 1)

    def _on_next_page(self):
        if self._current_page_idx < self._inspection.page_count - 1:
            self._load_page(self._current_page_idx + 1)
