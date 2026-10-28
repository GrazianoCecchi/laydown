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
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QToolTip, QVBoxLayout, QWidget,
)

import fitz

from laydown.engine.inspector import (
    BoxInfo, DocumentColorInspection, DocumentInspection, ICCProfileInfo,
    OutputIntentInfo, PageColorInspection, PageInspection, SpotColorInfo,
    inspect_document,
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

BOX_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "MediaBox": {
        "title": "MediaBox (Supporto fisico / Carta)",
        "summary": "Limiti fisici massimi del foglio o del supporto di stampa.",
        "desc": (
            "Rappresenta l'intero formato della carta grezza. Contiene tutto ciò che compone la pagina: "
            "il contenuto visibile, l'abbondanza (bleed), i crocini di registro e di taglio, le barre di calibrazione colore "
            "e le informazioni informative di commessa esterne al rifilo."
        ),
    },
    "CropBox": {
        "title": "CropBox (Riquadro di ritaglio a video)",
        "summary": "Area visualizzata a schermo dai lettori PDF e stampata per impostazione predefinita.",
        "desc": (
            "Definisce l'area della pagina mostrata a video in Adobe Acrobat o altri visualizzatori PDF. "
            "In assenza di ritaglio esplicito, coincide esattamente con il MediaBox."
        ),
    },
    "BleedBox": {
        "title": "BleedBox (Riquadro di abbondanza / Al vivo)",
        "summary": "Area comprensiva dell'abbondanza necessaria per il taglio professionale.",
        "desc": (
            "Rappresenta l'estensione del documento fino al vivo della pagina (solitamente +2 mm o +3 mm per lato rispetto al TrimBox). "
            "La grafica e i fondini colorati devono estendersi fino al BleedBox per evitare sottili filetti bianchi sul bordo dopo il taglio con taglierina."
        ),
    },
    "TrimBox": {
        "title": "TrimBox (Riquadro di rifilo / Formato finito)",
        "summary": "Dimensioni e confini del prodotto finito dopo il taglio tipografico.",
        "desc": (
            "È il riquadro fondamentale per l'imposizione e la stampa: definisce il formato netto del foglio ritagliato (es. A4, o 85×55 mm per biglietti da visita). "
            "È lungo i bordi del TrimBox che la taglierina/ghigliottina effettua il taglio e dove cadono i crocini di taglio."
        ),
    },
    "ArtBox": {
        "title": "ArtBox (Riquadro grafica / Area utile)",
        "summary": "Area del contenuto significativo o dell'area di sicurezza per il testo.",
        "desc": (
            "Definisce l'area che racchiude il contenuto significativo della pagina (es. la gabbia del testo o una grafica pubblicitaria all'interno di una pagina editoriale). "
            "Se non specificato, eredita le dimensioni del CropBox."
        ),
    },
}


def box_tooltip_html(name: str, box: Optional[BoxInfo] = None) -> str:
    """Generate rich, informative HTML tooltip explaining a PDF box."""
    info = BOX_DESCRIPTIONS.get(name, {})
    title = info.get("title", name)
    summary = info.get("summary", "")
    desc = info.get("desc", "")
    color = BOX_COLORS.get(name, QColor("#38BDF8")).name()

    dims_section = ""
    if box:
        status_str = "Dichiarato nel PDF" if box.is_explicit else "Derivato (fallback)"
        status_col = "#10B981" if box.is_explicit else "#94A3B8"
        dims_section = (
            f"<div style='margin-top:8px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.15); font-size:11px;'>"
            f"<b>Dimensioni:</b> {box.dimensions_mm_str} ({box.dimensions_pt_str})<br>"
            f"<b>Stato:</b> <span style='color:{status_col}; font-weight:600;'>{status_str}</span>"
            f"</div>"
        )

    return (
        f"<div style='max-width:320px; font-family:sans-serif; color:#F8FAFC; padding:2px;'>"
        f"<div style='font-size:12px; font-weight:bold; color:{color}; margin-bottom:3px;'>● {title}</div>"
        f"<div style='font-size:11px; font-weight:600; color:#E2E8F0; margin-bottom:5px;'>{summary}</div>"
        f"<div style='font-size:11px; color:#CBD5E1; line-height:1.4;'>{desc}</div>"
        f"{dims_section}"
        f"</div>"
    )


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
        self._highlighted_box: Optional[str] = None
        self._rendered_boxes: Dict[str, Tuple[QRectF, BoxInfo]] = {}
        self.setMouseTracking(True)
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

    def set_highlighted_box(self, name: Optional[str]):
        if self._highlighted_box != name:
            self._highlighted_box = name
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

        # Clear previously recorded box geometries for hit testing
        self._rendered_boxes.clear()

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
            self._rendered_boxes[name] = (rect, box)

            color = BOX_COLORS.get(name, QColor(t.ACCENT))
            pen_style = BOX_PEN_STYLES.get(name, Qt.PenStyle.SolidLine)
            pen_width = 2.0 if name == "TrimBox" else 1.5

            is_highlighted = (name == self._highlighted_box)
            if is_highlighted:
                pen_width += 1.5
                pen_style = Qt.PenStyle.SolidLine

            # Semi-transparent wash for TrimBox or hovered/highlighted box
            if name == "TrimBox" or is_highlighted:
                fill_color = QColor(color)
                fill_color.setAlpha(26 if is_highlighted else 15)
                painter.fillRect(rect, fill_color)

            # Draw boundary rect
            pen = QPen(color, pen_width, pen_style)
            painter.setPen(pen)
            painter.drawRect(rect)

            # Draw tag label in corner
            if self._show_labels or is_highlighted:
                tag_text = f" {name}: {box.dimensions_mm_str} "
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(tag_text) + 4
                th = fm.height() + 2

                # Position tag inside or adjacent to top-left of box
                tag_x = px0 + 2
                tag_y = py0 + 2 if name != "MediaBox" else py0 + th + 4
                tag_rect = QRectF(tag_x, tag_y, tw, th)

                bg_col = QColor(t.RAISED)
                bg_col.setAlpha(240 if is_highlighted else 220)
                painter.fillRect(tag_rect, bg_col)
                painter.setPen(QPen(color, 1.5 if is_highlighted else 1))
                painter.drawRect(tag_rect)
                painter.setPen(color)
                painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag_text)

        painter.end()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._highlighted_box is not None:
            self._highlighted_box = None
            self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        QToolTip.hideText()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = event.position()

        # Prioritize innermost box: TrimBox -> BleedBox -> ArtBox -> CropBox -> MediaBox
        check_order = ["TrimBox", "BleedBox", "ArtBox", "CropBox", "MediaBox"]
        found_box = None

        # 1. Edge proximity test (near perimeter lines within 6px)
        tol = 6.0
        for name in check_order:
            if not self._active_boxes.get(name, False) or name not in self._rendered_boxes:
                continue
            rect, box_info = self._rendered_boxes[name]
            on_left_right = ((abs(pos.x() - rect.left()) <= tol or abs(pos.x() - rect.right()) <= tol)
                             and (rect.top() - tol <= pos.y() <= rect.bottom() + tol))
            on_top_bottom = ((abs(pos.y() - rect.top()) <= tol or abs(pos.y() - rect.bottom()) <= tol)
                             and (rect.left() - tol <= pos.x() <= rect.right() + tol))
            if on_left_right or on_top_bottom:
                found_box = (name, box_info)
                break

        # 2. Interior area test if not on edge
        if not found_box:
            for name in check_order:
                if not self._active_boxes.get(name, False) or name not in self._rendered_boxes:
                    continue
                rect, box_info = self._rendered_boxes[name]
                if rect.contains(pos):
                    found_box = (name, box_info)
                    break

        if found_box:
            name, box_info = found_box
            if self._highlighted_box != name:
                self._highlighted_box = name
                self.update()
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            tip = box_tooltip_html(name, box_info)
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            if self._highlighted_box is not None:
                self._highlighted_box = None
                self.update()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            QToolTip.hideText()


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
        header_badges = [
            ("Pagine", str(self._inspection.page_count)),
            ("Versione", self._inspection.pdf_version),
            ("Dimensione", self._inspection.file_size_formatted),
        ]
        color_insp = self._inspection.color
        if color_insp:
            cs_short = "CMYK" if (color_insp.has_cmyk and not color_insp.has_rgb) else ("Misto" if (color_insp.has_rgb and color_insp.has_cmyk) else ("RGB" if color_insp.has_rgb else "Grigi"))
            if color_insp.has_spot:
                cs_short += f" + {len(color_insp.spot_colors)} Spot"
            header_badges.append(("Colore", cs_short))
            if color_insp.output_intents:
                oi_lbl = color_insp.output_intents[0].output_condition_identifier
                header_badges.append(("Intento", oi_lbl))
            elif color_insp.pdfx_conformance and "Nessuna" not in color_insp.pdfx_conformance:
                header_badges.append(("Conformità", color_insp.pdfx_conformance))

        for label, val in header_badges:
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
        self._boxes_table.itemSelectionChanged.connect(self._on_table_selection_changed)
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
            cb.setToolTip(box_tooltip_html(box_name))
            cb.setStyleSheet(
                f"QCheckBox {{ color: {col}; font-weight: 600; font-size: {t.TEXT_2XS}px; }}"
                f"QCheckBox::indicator:checked {{ background: {col}; border: 1px solid {col}; }}"
            )
            cb.toggled.connect(lambda state, n=box_name: self._canvas.set_box_visible(n, state))
            self._box_checks[box_name] = cb
            t_row.addWidget(cb)

        t_row.addStretch(1)
        bg_layout.addWidget(toggles_frame)

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

        # Tabs for left panel
        self._tabs_widget = QTabWidget()
        tabs_style = f"""
        QTabWidget::pane {{
            border: 1px solid {t.BORDER};
            border-radius: {t.RADIUS_MD}px;
            background: {t.SURFACE};
            top: -1px;
        }}
        QTabBar::tab {{
            background: {t.RAISED};
            color: {t.FG_MUTED};
            border: 1px solid {t.BORDER};
            border-bottom: none;
            border-top-left-radius: {t.RADIUS_SM}px;
            border-top-right-radius: {t.RADIUS_SM}px;
            padding: 6px 12px;
            margin-right: 3px;
            font-size: {t.TEXT_XS}px;
            font-weight: 500;
        }}
        QTabBar::tab:selected {{
            background: {t.SURFACE};
            color: {t.FG};
            border-bottom: 2px solid {t.ACCENT};
            font-weight: 600;
        }}
        QTabBar::tab:hover:!selected {{
            background: {t.INPUT_BG};
            color: {t.FG};
        }}
        """
        self._tabs_widget.setStyleSheet(tabs_style)

        # Tab 1: Riquadri
        boxes_tab = QWidget()
        boxes_tab_layout = QVBoxLayout(boxes_tab)
        boxes_tab_layout.setContentsMargins(4, 6, 4, 4)
        boxes_tab_layout.addWidget(boxes_group)
        boxes_tab_layout.addStretch(1)

        # Tab 2: Colore & Prestampa
        color_tab = self._build_color_tab()

        # Tab 3: Proprietà & Metadati
        meta_tab = QWidget()
        meta_tab_layout = QVBoxLayout(meta_tab)
        meta_tab_layout.setContentsMargins(4, 6, 4, 4)
        meta_tab_layout.addWidget(meta_group)
        meta_tab_layout.addStretch(1)

        self._tabs_widget.addTab(boxes_tab, lucide("inspect", 14), "Riquadri")
        self._tabs_widget.addTab(color_tab, lucide("palette", 14), "Colore")
        self._tabs_widget.addTab(meta_tab, lucide("sysdir", 14), "Metadati")

        left_layout.addWidget(self._tabs_widget, 1)

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

    def _build_color_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(10)

        color_insp: Optional[DocumentColorInspection] = self._inspection.color

        # 1. Preflight status banner
        banner = QFrame()
        if color_insp and color_insp.has_rgb_warning:
            banner_bg = "rgba(245, 158, 11, 0.12)"
            banner_border = "#F59E0B"
            banner_fg = "#FBBF24"
            banner_txt = "⚠️ <b>Attenzione colore per stampa:</b> rilevati elementi o immagini in spazio colore <b>RGB</b>. Potrebbero verificarsi conversioni colore in fase di stampa."
        elif color_insp and (color_insp.has_cmyk or color_insp.has_spot):
            banner_bg = "rgba(16, 185, 129, 0.12)"
            banner_border = "#10B981"
            banner_fg = "#34D399"
            banner_txt = "✓ <b>Conforme per prestampa:</b> spazio colore <b>CMYK</b> e separazioni senza elementi RGB."
        else:
            banner_bg = "rgba(148, 163, 184, 0.12)"
            banner_border = "#64748B"
            banner_fg = "#94A3B8"
            banner_txt = "ℹ️ Documento monocromatico o spazio colore generico."

        banner.setStyleSheet(
            f"background: {banner_bg}; border: 1px solid {banner_border};"
            f"border-radius: {t.RADIUS_SM}px; padding: 6px 10px;"
        )
        b_row = QHBoxLayout(banner)
        b_row.setContentsMargins(4, 4, 4, 4)
        lbl_banner = QLabel(banner_txt)
        lbl_banner.setWordWrap(True)
        lbl_banner.setStyleSheet(f"color: {banner_fg}; font-size: {t.TEXT_2XS}px;")
        b_row.addWidget(lbl_banner)
        c_layout.addWidget(banner)

        # 2. Document Overview Group
        ov_group = QGroupBox("Panoramica Colore Documento")
        ov_group.setStyleSheet(
            f"QGroupBox {{ font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};"
            f"              border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px;"
            f"              margin-top: 10px; padding-top: 14px; background: {t.SURFACE}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
        )
        ov_layout = QGridLayout(ov_group)
        ov_layout.setContentsMargins(8, 8, 8, 8)
        ov_layout.setSpacing(6)

        primary_cs = color_insp.primary_color_space if color_insp else "—"
        pdfx_str = color_insp.pdfx_conformance if color_insp else "—"
        pdfa_str = color_insp.pdfa_conformance or "—" if color_insp else "—"
        transp_str = "Presenti (Trasparenze / Blend Mode)" if (color_insp and color_insp.has_transparency) else "Nessuna trasparenza"
        overp_str = "Attiva (OP / Sovrastampa)" if (color_insp and color_insp.has_overprint) else "Non impostata"
        spaces_str = ", ".join(color_insp.color_spaces_detected) if (color_insp and color_insp.color_spaces_detected) else "—"

        ov_fields = [
            ("Spazio primario", primary_cs),
            ("Conformità PDF/X", pdfx_str),
            ("Conformità PDF/A", pdfa_str),
            ("Trasparenze", transp_str),
            ("Sovrastampa", overp_str),
            ("Spazi rilevati", spaces_str),
        ]

        for r, (label, val) in enumerate(ov_fields):
            lbl_w = QLabel(f"{label}:")
            lbl_w.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px; font-weight: 500;")
            lbl_w.setFixedWidth(115)
            val_w = QLabel(val)
            val_w.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px;")
            val_w.setWordWrap(True)
            ov_layout.addWidget(lbl_w, r, 0, Qt.AlignmentFlag.AlignTop)
            ov_layout.addWidget(val_w, r, 1, Qt.AlignmentFlag.AlignTop)

        c_layout.addWidget(ov_group)

        # 3. OutputIntents & ICC Profiles Group
        oi_group = QGroupBox("Intenti di Uscita (OutputIntents & Profili ICC)")
        oi_group.setStyleSheet(
            f"QGroupBox {{ font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};"
            f"              border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px;"
            f"              margin-top: 10px; padding-top: 14px; background: {t.SURFACE}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
        )
        oi_layout = QVBoxLayout(oi_group)
        oi_layout.setContentsMargins(8, 8, 8, 8)
        oi_layout.setSpacing(6)

        if color_insp and color_insp.output_intents:
            for oi in color_insp.output_intents:
                card = QFrame()
                card.setStyleSheet(
                    f"background: {t.INPUT_BG}; border: 1px solid {t.BORDER_SOFT};"
                    f"border-radius: {t.RADIUS_SM}px; padding: 6px;"
                )
                cd_layout = QGridLayout(card)
                cd_layout.setContentsMargins(6, 4, 6, 4)
                cd_layout.setSpacing(4)

                header_lbl = QLabel(f"● {oi.output_condition_identifier} ({oi.subtype})")
                header_lbl.setStyleSheet(f"color: {t.ACCENT}; font-size: {t.TEXT_XS}px; font-weight: 600;")
                cd_layout.addWidget(header_lbl, 0, 0, 1, 2)

                row_idx = 1
                if oi.output_condition and oi.output_condition != oi.output_condition_identifier:
                    lbl_c = QLabel("Condizione:")
                    lbl_c.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_2XS}px;")
                    val_c = QLabel(oi.output_condition)
                    val_c.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_2XS}px;")
                    cd_layout.addWidget(lbl_c, row_idx, 0)
                    cd_layout.addWidget(val_c, row_idx, 1)
                    row_idx += 1
                if oi.info:
                    lbl_i = QLabel("Info:")
                    lbl_i.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_2XS}px;")
                    val_i = QLabel(oi.info)
                    val_i.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_2XS}px;")
                    val_i.setWordWrap(True)
                    cd_layout.addWidget(lbl_i, row_idx, 0)
                    cd_layout.addWidget(val_i, row_idx, 1)
                    row_idx += 1
                if oi.registry_name:
                    lbl_r = QLabel("Registro:")
                    lbl_r.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_2XS}px;")
                    val_r = QLabel(oi.registry_name)
                    val_r.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px;")
                    cd_layout.addWidget(lbl_r, row_idx, 0)
                    cd_layout.addWidget(val_r, row_idx, 1)
                    row_idx += 1

                if oi.profile:
                    p = oi.profile
                    prof_lbl = QLabel(f"Profilo ICC incorporato: {p.name} (v{p.version}, {p.color_space}, {p.rendering_intent}, {p.size_formatted})")
                    prof_lbl.setStyleSheet(f"color: #10B981; font-size: {t.TEXT_2XS}px;")
                    prof_lbl.setWordWrap(True)
                    cd_layout.addWidget(prof_lbl, row_idx, 0, 1, 2)

                oi_layout.addWidget(card)
        else:
            no_oi = QLabel("Nessun OutputIntent dichiarato nel documento.\nLa conversione colore farà riferimento al profilo standard del RIP / stampante.")
            no_oi.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px; font-style: italic;")
            no_oi.setWordWrap(True)
            oi_layout.addWidget(no_oi)

        c_layout.addWidget(oi_group)

        # 4. Spot Colors / Separations Group
        spot_group = QGroupBox("Tinte Piatte e Separazioni (Spot Colors)")
        spot_group.setStyleSheet(
            f"QGroupBox {{ font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};"
            f"              border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px;"
            f"              margin-top: 10px; padding-top: 14px; background: {t.SURFACE}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
        )
        spot_layout = QVBoxLayout(spot_group)
        spot_layout.setContentsMargins(8, 8, 8, 8)
        spot_layout.setSpacing(6)

        if color_insp and color_insp.spot_colors:
            spot_table = QTableWidget()
            spot_table.setColumnCount(3)
            spot_table.setHorizontalHeaderLabels(["Nome Tinta", "Spazio Alternativo", "Pagine"])
            spot_table.horizontalHeader().setStretchLastSection(True)
            spot_table.verticalHeader().setVisible(False)
            spot_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            spot_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            spot_table.setStyleSheet(
                f"QTableWidget {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER_SOFT};"
                f"                gridline-color: {t.BORDER_SOFT}; font-size: {t.TEXT_XS}px; }}"
                f"QHeaderView::section {{ background: {t.RAISED}; color: {t.FG_MUTED}; border: 1px solid {t.BORDER_SOFT}; padding: 4px; font-size: {t.TEXT_2XS}px; }}"
            )
            spot_table.setRowCount(len(color_insp.spot_colors))
            for row, sp in enumerate(color_insp.spot_colors):
                it_name = QTableWidgetItem(f"● {sp.name}")
                it_name.setForeground(QColor(t.ACCENT))
                it_name.setFont(QFont(t.FONT_FAMILY, 9, QFont.Weight.Bold))
                spot_table.setItem(row, 0, it_name)

                it_alt = QTableWidgetItem(sp.alternate_space)
                it_alt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                spot_table.setItem(row, 1, it_alt)

                pages_str = ", ".join(f"p.{p}" for p in sp.pages) if sp.pages else "—"
                it_pg = QTableWidgetItem(pages_str)
                it_pg.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                spot_table.setItem(row, 2, it_pg)

            spot_table.setFixedHeight(min(140, 28 * len(color_insp.spot_colors) + 32))
            spot_layout.addWidget(spot_table)
        else:
            no_spots = QLabel("Nessuna tinta piatta / separazione trovata nel documento.")
            no_spots.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px; font-style: italic;")
            spot_layout.addWidget(no_spots)

        c_layout.addWidget(spot_group)

        # 5. Dynamic Page Color Group (updates per page)
        self._page_color_group = QGroupBox("Dettaglio Colore Pagina Selezionata")
        self._page_color_group.setStyleSheet(
            f"QGroupBox {{ font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};"
            f"              border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px;"
            f"              margin-top: 10px; padding-top: 14px; background: {t.SURFACE}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
        )
        pg_layout = QGridLayout(self._page_color_group)
        pg_layout.setContentsMargins(8, 8, 8, 8)
        pg_layout.setSpacing(6)

        self._lbl_pg_summary = QLabel("—")
        self._lbl_pg_summary.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px; font-weight: 600;")
        self._lbl_pg_images = QLabel("—")
        self._lbl_pg_images.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px;")
        self._lbl_pg_intent = QLabel("—")
        self._lbl_pg_intent.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px;")
        self._lbl_pg_spots = QLabel("—")
        self._lbl_pg_spots.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_XS}px;")

        pg_fields = [
            ("Elementi pagina:", self._lbl_pg_summary),
            ("Immagini raster:", self._lbl_pg_images),
            ("Intento resa:", self._lbl_pg_intent),
            ("Tinte piatte pagina:", self._lbl_pg_spots),
        ]
        for r, (lbl_txt, w) in enumerate(pg_fields):
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px; font-weight: 500;")
            lbl.setFixedWidth(120)
            pg_layout.addWidget(lbl, r, 0, Qt.AlignmentFlag.AlignTop)
            pg_layout.addWidget(w, r, 1, Qt.AlignmentFlag.AlignTop)

        c_layout.addWidget(self._page_color_group)
        c_layout.addStretch(1)

        scroll.setWidget(content)
        return scroll

    def _update_page_color_tab(self, page_color: PageColorInspection):
        self._page_color_group.setTitle(f"Dettaglio Colore Pagina {page_color.page_number}")
        self._lbl_pg_summary.setText(page_color.summary_str)
        if page_color.has_rgb:
            self._lbl_pg_summary.setStyleSheet("color: #FBBF24; font-size: 12px; font-weight: 600;")
        else:
            self._lbl_pg_summary.setStyleSheet(f"color: {t.FG}; font-size: 12px; font-weight: 600;")

        # Images
        if page_color.images_count > 0:
            cs_breakdown = ", ".join(f"{count} {cs}" for cs, count in page_color.images_color_spaces.items())
            img_txt = f"{page_color.images_count} ({cs_breakdown})"
            if page_color.images_profiles:
                img_txt += f" — Profili: {', '.join(page_color.images_profiles)}"
        else:
            img_txt = "Nessuna immagine raster"
        self._lbl_pg_images.setText(img_txt)

        # Rendering intent
        if page_color.rendering_intents:
            self._lbl_pg_intent.setText(", ".join(page_color.rendering_intents))
        else:
            self._lbl_pg_intent.setText("Standard (da OutputIntent o default)")

        # Spot colors on page
        if page_color.spot_colors:
            self._lbl_pg_spots.setText(", ".join(page_color.spot_colors))
            self._lbl_pg_spots.setStyleSheet(f"color: {t.ACCENT}; font-size: 12px; font-weight: 600;")
        else:
            self._lbl_pg_spots.setText("Nessuna")
            self._lbl_pg_spots.setStyleSheet(f"color: {t.FG_MUTED}; font-size: 12px;")

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
            tip = box_tooltip_html(name, box)

            # Column 0: Name with colored badge
            item_name = QTableWidgetItem(f"● {name}")
            item_name.setForeground(color)
            item_name.setFont(QFont(t.FONT_FAMILY, 9, QFont.Weight.Bold))
            item_name.setToolTip(tip)
            self._boxes_table.setItem(row, 0, item_name)

            # Column 1: Dimensions in mm
            item_mm = QTableWidgetItem(box.dimensions_mm_str)
            item_mm.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_mm.setToolTip(tip)
            self._boxes_table.setItem(row, 1, item_mm)

            # Column 2: Dimensions in pt
            item_pt = QTableWidgetItem(box.dimensions_pt_str)
            item_pt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_pt.setForeground(QColor(t.FG_MUTED))
            item_pt.setToolTip(tip)
            self._boxes_table.setItem(row, 2, item_pt)

            # Column 3: Status (Dichiarato vs Derivato)
            status_text = "Dichiarato" if box.is_explicit else "Derivato"
            item_status = QTableWidgetItem(status_text)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setToolTip(tip)
            if box.is_explicit:
                item_status.setForeground(QColor("#10B981"))  # Green
            else:
                item_status.setForeground(QColor(t.FG_FAINT))
            self._boxes_table.setItem(row, 3, item_status)

        # Update canvas
        self._canvas.set_data(self._doc, page_idx, page_info)

        # Update page color details
        if page_info.color and hasattr(self, "_update_page_color_tab"):
            self._update_page_color_tab(page_info.color)

    def _on_table_selection_changed(self):
        row = self._boxes_table.currentRow()
        if 0 <= row < self._boxes_table.rowCount():
            item = self._boxes_table.item(row, 0)
            if item:
                box_name = item.text().replace("●", "").strip()
                self._canvas.set_highlighted_box(box_name)
        else:
            self._canvas.set_highlighted_box(None)

    def _on_prev_page(self):
        if self._current_page_idx > 0:
            self._load_page(self._current_page_idx - 1)

    def _on_next_page(self):
        if self._current_page_idx < self._inspection.page_count - 1:
            self._load_page(self._current_page_idx + 1)
