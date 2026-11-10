"""
Dialog for composing and merging multiple PDF documents with fine-grained page sequence selection.
"""

from __future__ import annotations

import logging
import os
import string
import tempfile
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from laydown.engine.composer import (
    PageSpec,
    ParseResult,
    SourceDocInfo,
    compose_pdf,
    inspect_source_pdf,
    parse_page_sequence,
)
from laydown.ui import theme as t
from laydown.ui.icons import lucide

_LOG = logging.getLogger(__name__)


def _letter_id(index: int) -> str:
    """Generate spreadsheet-style letter identifiers: 0->A, 25->Z, 26->AA, etc."""
    result = ""
    while True:
        result = chr(ord('A') + (index % 26)) + result
        index = (index // 26) - 1
        if index < 0:
            break
    return result


class ComposerDialog(QDialog):
    """
    Dialog allowing users to load multiple PDF files, map them to letters (A, B, C...),
    and define an exact composite page sequence (e.g. 'A1-10, B2, A11-12, C1-4').
    """

    def __init__(
        self,
        initial_pdf_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Componi e unisci PDF — Laydown")
        self.setMinimumSize(780, 620)
        self.resize(850, 680)
        self.setAcceptDrops(True)

        self._sources: List[SourceDocInfo] = []
        self._current_parse_result: Optional[ParseResult] = None
        self.composed_pdf_path: Optional[str] = None
        self.open_in_laydown_requested: bool = False

        self._build_ui()

        # Pre-load initial PDF if provided and valid
        if initial_pdf_path and os.path.isfile(initial_pdf_path):
            self._add_source_files([initial_pdf_path])

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SPACE_5, t.SPACE_5, t.SPACE_5, t.SPACE_5)
        root.setSpacing(t.SPACE_4)

        # ── Header ──
        header_layout = QVBoxLayout()
        header_layout.setSpacing(t.SPACE_1)

        title = QLabel("Composizione e unione di file PDF")
        title.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 700; color: {t.FG};")
        header_layout.addWidget(title)

        desc = QLabel(
            "Unisci più file PDF ed estrai pagine specifiche componendo la sequenza desiderata "
            "(es. A1-10, B2, A11-12, C1-4). Trascina i file PDF nella tabella sottostante o usa 'Aggiungi PDF'."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: {t.TEXT_SM}px; color: {t.FG_MUTED};")
        header_layout.addWidget(desc)
        root.addLayout(header_layout)

        # ── Sources Section ──
        sources_box = QVBoxLayout()
        sources_box.setSpacing(t.SPACE_2)

        src_lbl = QLabel("Documenti sorgente")
        src_lbl.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};")
        sources_box.addWidget(src_lbl)

        # Table & action buttons side by side
        tbl_row = QHBoxLayout()
        tbl_row.setSpacing(t.SPACE_3)

        self._src_table = QTableWidget(0, 5)
        self._src_table.setHorizontalHeaderLabels(["ID", "File", "Pagine", "Formato", "Percorso"])
        self._src_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._src_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._src_table.verticalHeader().setVisible(False)
        self._src_table.setShowGrid(False)
        self._src_table.setStyleSheet(
            f"QTableWidget {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER}; "
            f"               border-radius: {t.RADIUS_MD}px; gridline-color: transparent; }}"
            f"QTableWidget::item {{ padding: 6px 8px; color: {t.FG}; }}"
            f"QTableWidget::item:selected {{ background: {t.SELECTED_WASH}; color: {t.FG}; }}"
            f"QHeaderView::section {{ background: {t.RAISED}; color: {t.FG_MUTED}; "
            f"                        font-size: {t.TEXT_XS}px; font-weight: 600; "
            f"                        border: none; border-bottom: 1px solid {t.BORDER}; padding: 6px 8px; }}"
        )

        h = self._src_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._src_table.setColumnWidth(1, 200)

        self._src_table.itemSelectionChanged.connect(self._update_button_states)
        tbl_row.addWidget(self._src_table, 1)

        # Buttons on the right of the sources table
        btn_col = QVBoxLayout()
        btn_col.setSpacing(t.SPACE_2)

        self._btn_add = QPushButton("Aggiungi PDF…")
        self._btn_add.setIcon(lucide("plus", 14))
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.clicked.connect(self._on_add_pdf)
        btn_col.addWidget(self._btn_add)

        self._btn_remove = QPushButton("Rimuovi")
        self._btn_remove.setIcon(lucide("trash", 14))
        self._btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._on_remove_selected)
        btn_col.addWidget(self._btn_remove)

        self._btn_up = QPushButton("Sposta su")
        self._btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_up.setEnabled(False)
        self._btn_up.clicked.connect(self._on_move_up)
        btn_col.addWidget(self._btn_up)

        self._btn_down = QPushButton("Sposta giù")
        self._btn_down.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_down.setEnabled(False)
        self._btn_down.clicked.connect(self._on_move_down)
        btn_col.addWidget(self._btn_down)

        self._btn_clear = QPushButton("Svuota")
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._on_clear_all)
        btn_col.addWidget(self._btn_clear)

        btn_col.addStretch(1)
        tbl_row.addLayout(btn_col)
        sources_box.addLayout(tbl_row)
        root.addLayout(sources_box)

        # ── Sequence Input Section ──
        seq_box = QVBoxLayout()
        seq_box.setSpacing(t.SPACE_2)

        seq_lbl_row = QHBoxLayout()
        seq_lbl = QLabel("Sequenza delle pagine di output:")
        seq_lbl.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};")
        seq_lbl_row.addWidget(seq_lbl)
        seq_lbl_row.addStretch(1)

        # Quick action links/buttons
        self._btn_all_order = QPushButton("Tutti i file in ordine")
        self._btn_all_order.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_all_order.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {t.BORDER}; "
            f"               border-radius: {t.RADIUS_SM}px; padding: 2px 8px; "
            f"               font-size: {t.TEXT_XS}px; color: {t.FG_MUTED}; }}"
            f"QPushButton:hover {{ background: {t.HOVER_WASH}; color: {t.FG}; }}"
        )
        self._btn_all_order.clicked.connect(self._on_set_all_in_order)
        seq_lbl_row.addWidget(self._btn_all_order)

        self._btn_add_blank = QPushButton("+ Pagina vuota")
        self._btn_add_blank.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add_blank.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {t.BORDER}; "
            f"               border-radius: {t.RADIUS_SM}px; padding: 2px 8px; "
            f"               font-size: {t.TEXT_XS}px; color: {t.FG_MUTED}; }}"
            f"QPushButton:hover {{ background: {t.HOVER_WASH}; color: {t.FG}; }}"
        )
        self._btn_add_blank.clicked.connect(self._on_add_blank_token)
        seq_lbl_row.addWidget(self._btn_add_blank)

        seq_box.addLayout(seq_lbl_row)

        self._seq_input = QLineEdit()
        self._seq_input.setPlaceholderText("Es. A1-10, B2, A11-12, C1-4   (A = tutto il file A; A5- = da pag 5 alla fine; blank = vuota)")
        self._seq_input.setStyleSheet(
            f"QLineEdit {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER}; "
            f"             border-radius: {t.RADIUS_MD}px; padding: 8px 10px; "
            f"             font-size: {t.TEXT_SM}px; font-family: monospace; color: {t.FG}; }}"
            f"QLineEdit:focus {{ border-color: {t.ACCENT}; }}"
        )
        self._seq_input.textChanged.connect(self._on_sequence_changed)
        seq_box.addWidget(self._seq_input)

        # ── Status & Validation Bar ──
        self._status_frame = QFrame()
        self._status_frame.setStyleSheet(
            f"QFrame {{ background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT}; "
            f"          border-radius: {t.RADIUS_SM}px; padding: 6px 10px; }}"
        )
        status_row = QHBoxLayout(self._status_frame)
        status_row.setContentsMargins(8, 6, 8, 6)
        status_row.setSpacing(t.SPACE_2)

        self._status_label = QLabel("Aggiungi i file PDF sorgente per iniziare.")
        self._status_label.setStyleSheet(f"font-size: {t.TEXT_SM}px; color: {t.FG_MUTED};")
        status_row.addWidget(self._status_label, 1)

        self._btn_toggle_preview = QPushButton("Mostra anteprima sequenza")
        self._btn_toggle_preview.setCheckable(True)
        self._btn_toggle_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle_preview.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; font-size: {t.TEXT_XS}px; "
            f"               color: {t.ACCENT}; text-decoration: underline; }}"
            f"QPushButton:hover {{ color: {t.ACCENT_HOVER}; }}"
        )
        self._btn_toggle_preview.toggled.connect(self._on_toggle_preview)
        status_row.addWidget(self._btn_toggle_preview)

        seq_box.addWidget(self._status_frame)
        root.addLayout(seq_box)

        # ── Sequence Preview Table (Collapsible) ──
        self._preview_table = QTableWidget(0, 3)
        self._preview_table.setHorizontalHeaderLabels(["N° Output", "Documento Sorgente", "Pagina Originale"])
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.setShowGrid(False)
        self._preview_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._preview_table.setMaximumHeight(140)
        self._preview_table.setVisible(False)
        self._preview_table.setStyleSheet(
            f"QTableWidget {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER}; "
            f"               border-radius: {t.RADIUS_SM}px; font-size: {t.TEXT_XS}px; }}"
            f"QTableWidget::item {{ padding: 4px 6px; color: {t.FG}; }}"
            f"QHeaderView::section {{ background: {t.RAISED}; color: {t.FG_MUTED}; "
            f"                        font-size: {t.TEXT_XS}px; font-weight: 600; "
            f"                        border: none; border-bottom: 1px solid {t.BORDER}; padding: 4px 6px; }}"
        )
        prev_h = self._preview_table.horizontalHeader()
        prev_h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        prev_h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        prev_h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._preview_table)

        # ── Bottom Action Buttons ──
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(t.SPACE_3)

        self._btn_cancel = QPushButton("Annulla")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.setStyleSheet(
            f"QPushButton {{ background: {t.SURFACE}; border: 1px solid {t.BORDER}; "
            f"               border-radius: {t.RADIUS_MD}px; padding: 8px 16px; "
            f"               font-size: {t.TEXT_SM}px; color: {t.FG}; }}"
            f"QPushButton:hover {{ background: {t.HOVER_WASH}; }}"
        )
        self._btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(self._btn_cancel)

        bottom_row.addStretch(1)

        self._btn_save_as = QPushButton("Salva PDF con nome…")
        self._btn_save_as.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save_as.setEnabled(False)
        self._btn_save_as.setStyleSheet(
            f"QPushButton {{ background: {t.SURFACE}; border: 1px solid {t.BORDER}; "
            f"               border-radius: {t.RADIUS_MD}px; padding: 8px 18px; "
            f"               font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG}; }}"
            f"QPushButton:hover:enabled {{ background: {t.HOVER_WASH}; }}"
            f"QPushButton:disabled {{ color: {t.FG_FAINT}; border-color: {t.BORDER_SOFT}; }}"
        )
        self._btn_save_as.clicked.connect(self._on_save_as)
        bottom_row.addWidget(self._btn_save_as)

        self._btn_open_in_laydown = QPushButton("Apri in Laydown")
        self._btn_open_in_laydown.setIcon(lucide("layers", 16))
        self._btn_open_in_laydown.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_in_laydown.setEnabled(False)
        self._btn_open_in_laydown.setStyleSheet(
            f"QPushButton {{ background: {t.ACCENT}; border: 1px solid {t.ACCENT}; "
            f"               border-radius: {t.RADIUS_MD}px; padding: 8px 20px; "
            f"               font-size: {t.TEXT_SM}px; font-weight: 700; color: #FFFFFF; }}"
            f"QPushButton:hover:enabled {{ background: {t.ACCENT_HOVER}; border-color: {t.ACCENT_HOVER}; }}"
            f"QPushButton:pressed:enabled {{ background: {t.ACCENT_PRESS}; }}"
            f"QPushButton:disabled {{ background: {t.RAISED}; border-color: {t.BORDER}; color: {t.FG_FAINT}; }}"
        )
        self._btn_open_in_laydown.clicked.connect(self._on_open_in_laydown)
        bottom_row.addWidget(self._btn_open_in_laydown)

        root.addLayout(bottom_row)

    # ── Drag and Drop ──

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(u.toLocalFile().lower().endswith(".pdf") for u in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path.lower().endswith(".pdf") and os.path.isfile(local_path):
                paths.append(local_path)
        if paths:
            self._add_source_files(paths)
            event.acceptProposedAction()

    # ── Source Management ──

    def _on_add_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleziona file PDF da aggiungere",
            "",
            "File PDF (*.pdf);;Tutti i file (*.*)",
        )
        if paths:
            self._add_source_files(paths)

    def _add_source_files(self, paths: List[str]):
        added_any = False
        existing_paths = {s.path for s in self._sources}

        for path in paths:
            abs_p = os.path.abspath(path)
            if abs_p in existing_paths:
                continue
            try:
                # Temporary ID, will be normalized by _reassign_source_ids()
                info = inspect_source_pdf(abs_p, doc_id="TEMP")
                self._sources.append(info)
                existing_paths.add(abs_p)
                added_any = True
            except Exception as e:
                _LOG.warning("Could not read PDF '%s': %s", path, e)
                QMessageBox.warning(
                    self,
                    "Errore caricamento PDF",
                    f"Impossibile leggere il file:\n{os.path.basename(path)}\n\nErrore: {e}",
                )

        if added_any:
            self._reassign_source_ids()
            self._rebuild_sources_table()
            # If sequence input was empty, provide a convenient default: all documents
            if not self._seq_input.text().strip():
                default_expr = ", ".join(s.id for s in self._sources)
                self._seq_input.setText(default_expr)
            else:
                self._validate_sequence()

    def _reassign_source_ids(self):
        """Reassign consecutive letters A, B, C... to match the current order."""
        updated = []
        for i, src in enumerate(self._sources):
            letter = _letter_id(i)
            updated.append(
                SourceDocInfo(
                    id=letter,
                    path=src.path,
                    page_count=src.page_count,
                    first_page_width_mm=src.first_page_width_mm,
                    first_page_height_mm=src.first_page_height_mm,
                    title=src.title,
                )
            )
        self._sources = updated

    def _rebuild_sources_table(self):
        self._src_table.setRowCount(len(self._sources))
        for row, src in enumerate(self._sources):
            # Column 0: Badge ID
            id_item = QTableWidgetItem(f"[{src.id}]")
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            id_item.setForeground(QColor(t.ACCENT))
            self._src_table.setItem(row, 0, id_item)

            # Column 1: Filename
            fn_item = QTableWidgetItem(src.filename)
            fn_item.setToolTip(src.path)
            self._src_table.setItem(row, 1, fn_item)

            # Column 2: Page count
            pages_str = f"{src.page_count} pag." if src.page_count != 1 else "1 pag."
            pg_item = QTableWidgetItem(pages_str)
            pg_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._src_table.setItem(row, 2, pg_item)

            # Column 3: Format / dimensions
            dim_item = QTableWidgetItem(src.dimensions_str)
            dim_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dim_item.setForeground(QColor(t.FG_MUTED))
            self._src_table.setItem(row, 3, dim_item)

            # Column 4: Full path
            p_item = QTableWidgetItem(src.path)
            p_item.setForeground(QColor(t.FG_FAINT))
            self._src_table.setItem(row, 4, p_item)

        self._update_button_states()

    def _update_button_states(self):
        has_sources = len(self._sources) > 0
        selected_row = self._src_table.currentRow()
        has_sel = selected_row >= 0

        self._btn_clear.setEnabled(has_sources)
        self._btn_remove.setEnabled(has_sel)
        self._btn_up.setEnabled(has_sel and selected_row > 0)
        self._btn_down.setEnabled(has_sel and selected_row < len(self._sources) - 1)
        self._btn_all_order.setEnabled(has_sources)

    def _on_remove_selected(self):
        row = self._src_table.currentRow()
        if 0 <= row < len(self._sources):
            del self._sources[row]
            self._reassign_source_ids()
            self._rebuild_sources_table()
            self._validate_sequence()

    def _on_move_up(self):
        row = self._src_table.currentRow()
        if row > 0:
            self._sources[row - 1], self._sources[row] = self._sources[row], self._sources[row - 1]
            self._reassign_source_ids()
            self._rebuild_sources_table()
            self._src_table.selectRow(row - 1)
            self._validate_sequence()

    def _on_move_down(self):
        row = self._src_table.currentRow()
        if 0 <= row < len(self._sources) - 1:
            self._sources[row + 1], self._sources[row] = self._sources[row], self._sources[row + 1]
            self._reassign_source_ids()
            self._rebuild_sources_table()
            self._src_table.selectRow(row + 1)
            self._validate_sequence()

    def _on_clear_all(self):
        if not self._sources:
            return
        self._sources.clear()
        self._rebuild_sources_table()
        self._validate_sequence()

    # ── Sequence & Validation ──

    def _on_set_all_in_order(self):
        if not self._sources:
            return
        self._seq_input.setText(", ".join(s.id for s in self._sources))

    def _on_add_blank_token(self):
        cur = self._seq_input.text().strip()
        if cur and not cur.endswith((",", ";")):
            self._seq_input.setText(cur + ", blank")
        elif cur:
            self._seq_input.setText(cur + " blank")
        else:
            self._seq_input.setText("blank")

    def _on_sequence_changed(self, _):
        self._validate_sequence()

    def _validate_sequence(self):
        counts = {s.id: s.page_count for s in self._sources}
        text = self._seq_input.text().strip()

        if not self._sources:
            self._current_parse_result = None
            self._status_frame.setStyleSheet(
                f"QFrame {{ background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT}; "
                f"          border-radius: {t.RADIUS_SM}px; padding: 6px 10px; }}"
            )
            self._status_label.setText("Aggiungi i file PDF sorgente per iniziare.")
            self._status_label.setStyleSheet(f"font-size: {t.TEXT_SM}px; color: {t.FG_MUTED};")
            self._btn_save_as.setEnabled(False)
            self._btn_open_in_laydown.setEnabled(False)
            self._update_preview_table([])
            return

        if not text:
            self._current_parse_result = None
            self._status_frame.setStyleSheet(
                f"QFrame {{ background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT}; "
                f"          border-radius: {t.RADIUS_SM}px; padding: 6px 10px; }}"
            )
            self._status_label.setText("Inserisci la sequenza desiderata delle pagine (es. A1-10, B2, A11-12, C1-4).")
            self._status_label.setStyleSheet(f"font-size: {t.TEXT_SM}px; color: {t.FG_MUTED};")
            self._btn_save_as.setEnabled(False)
            self._btn_open_in_laydown.setEnabled(False)
            self._update_preview_table([])
            return

        result = parse_page_sequence(text, counts)
        self._current_parse_result = result

        if result.is_valid:
            if result.total_pages == 0:
                self._status_label.setText("Nessuna pagina selezionata.")
                self._status_label.setStyleSheet(f"font-size: {t.TEXT_SM}px; color: {t.FG_MUTED};")
                self._btn_save_as.setEnabled(False)
                self._btn_open_in_laydown.setEnabled(False)
                self._update_preview_table([])
                return

            # Summary breakdown
            parts = []
            for doc_id, count in result.summary_by_source.items():
                if doc_id == "BLANK":
                    parts.append(f"{count} vuot{'a' if count == 1 else 'e'}")
                else:
                    parts.append(f"{doc_id}: {count} pag.")
            summary_str = ", ".join(parts)

            pag_word = "pagina" if result.total_pages == 1 else "pagine"
            msg = f"✓ Sequenza valida: {result.total_pages} {pag_word} generate ({summary_str})"
            self._status_frame.setStyleSheet(
                f"QFrame {{ background: {t.mix(t.ACCENT, t.SURFACE, 0.12)}; "
                f"          border: 1px solid {t.mix(t.ACCENT, t.BORDER, 0.4)}; "
                f"          border-radius: {t.RADIUS_SM}px; padding: 6px 10px; }}"
            )
            self._status_label.setText(msg)
            self._status_label.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.ACCENT};")
            self._btn_save_as.setEnabled(True)
            self._btn_open_in_laydown.setEnabled(True)
            self._update_preview_table(result.page_specs)
        else:
            self._status_frame.setStyleSheet(
                f"QFrame {{ background: {t.mix(t.DESTRUCTIVE, t.SURFACE, 0.15)}; "
                f"          border: 1px solid {t.mix(t.DESTRUCTIVE, t.BORDER, 0.5)}; "
                f"          border-radius: {t.RADIUS_SM}px; padding: 6px 10px; }}"
            )
            self._status_label.setText(f"⚠️ {result.error_message}")
            self._status_label.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.DESTRUCTIVE};")
            self._btn_save_as.setEnabled(False)
            self._btn_open_in_laydown.setEnabled(False)
            self._update_preview_table([])

    def _on_toggle_preview(self, checked: bool):
        self._preview_table.setVisible(checked)
        self._btn_toggle_preview.setText("Nascondi anteprima sequenza" if checked else "Mostra anteprima sequenza")

    def _update_preview_table(self, specs: List[PageSpec]):
        self._preview_table.setRowCount(len(specs))
        doc_names = {s.id: s.filename for s in self._sources}

        for row, spec in enumerate(specs):
            # Col 0: Output page number (1-based)
            p_out = QTableWidgetItem(f"Pagina {row + 1}")
            p_out.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._preview_table.setItem(row, 0, p_out)

            # Col 1: Source document
            if spec.is_blank:
                doc_item = QTableWidgetItem("[Pagina vuota]")
                doc_item.setForeground(QColor(t.FG_MUTED))
            else:
                fn = doc_names.get(spec.source_id, "")
                doc_item = QTableWidgetItem(f"[{spec.source_id}] {fn}")
            self._preview_table.setItem(row, 1, doc_item)

            # Col 2: Original page number
            if spec.is_blank:
                orig_item = QTableWidgetItem("—")
                orig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                orig_item.setForeground(QColor(t.FG_FAINT))
            else:
                orig_item = QTableWidgetItem(f"pag. {spec.display_source_page}")
                orig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._preview_table.setItem(row, 2, orig_item)

    # ── PDF Generation Actions ──

    def _compose_to_file(self, target_path: str) -> bool:
        if not self._current_parse_result or not self._current_parse_result.is_valid:
            return False

        sources_map = {s.id: s.path for s in self._sources}
        try:
            doc = compose_pdf(
                sources=sources_map,
                page_specs=self._current_parse_result.page_specs,
                output_path=target_path,
            )
            doc.close()
            return True
        except Exception as e:
            _LOG.exception("Error composing PDF to '%s'", target_path)
            QMessageBox.critical(
                self,
                "Errore durante la composizione",
                f"Si è verificato un errore durante la generazione del PDF:\n\n{e}",
            )
            return False

    def _on_save_as(self):
        default_dir = ""
        if self._sources:
            default_dir = os.path.dirname(self._sources[0].path)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Salva PDF composto con nome",
            os.path.join(default_dir, "documento_composto.pdf"),
            "File PDF (*.pdf);;Tutti i file (*.*)",
        )
        if not path:
            return

        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        if self._compose_to_file(path):
            self.composed_pdf_path = path
            self.open_in_laydown_requested = False
            QMessageBox.information(
                self,
                "PDF generato con successo",
                f"Il documento composto è stato salvato con successo in:\n{path}",
            )
            self.accept()

    def _on_open_in_laydown(self):
        # Create a temporary file to load into Laydown
        fd, temp_path = tempfile.mkstemp(prefix="laydown_composed_", suffix=".pdf")
        os.close(fd)

        if self._compose_to_file(temp_path):
            self.composed_pdf_path = temp_path
            self.open_in_laydown_requested = True
            self.accept()
