"""
Dialogs for managing and saving custom sheet formats in Laydown.
"""

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from laydown.engine.data_model import SHEET_PRESETS_MM
from laydown.ui import theme as t
from laydown.ui.icons import lucide
from laydown.ui.sheet_formats import SheetFormatManager, _dim_str


class FormatSaveDialog(QDialog):
    """Dialog to quickly save custom sheet dimensions as a named format."""

    def __init__(
        self,
        default_width_mm: float = 297.0,
        default_height_mm: float = 420.0,
        default_name: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._saved_name = ""

        self.setWindowTitle("Salva Formato Carta — Laydown")
        self.setMinimumWidth(440)
        self.resize(480, 310)

        self._build_ui(default_name, default_width_mm, default_height_mm)

    def _build_ui(self, default_name: str, width_mm: float, height_mm: float):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.SPACE_4, t.SPACE_4, t.SPACE_4, t.SPACE_4)
        layout.setSpacing(t.SPACE_3)

        # Header
        h_lbl = QLabel("Salva dimensioni come formato personalizzato")
        h_lbl.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 700; color: {t.FG};")
        layout.addWidget(h_lbl)

        sub_lbl = QLabel(
            "Il nuovo formato verrà memorizzato nel catalogo locale (sheet_formats.json) "
            "e sarà immediatamente selezionabile dal menu Formato del foglio."
        )
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        layout.addWidget(sub_lbl)

        # Name input
        name_lbl = QLabel("Nome formato:")
        name_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; font-weight: 600; color: {t.FG};")
        layout.addWidget(name_lbl)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Es. Copertina 3 ante (320x650)")
        self._name_edit.setText(default_name)
        self._name_edit.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name_edit)

        # Dimension inputs (Width & Height)
        dims_row = QHBoxLayout()
        dims_row.setSpacing(t.SPACE_3)

        w_col = QVBoxLayout()
        w_lbl = QLabel("Larghezza (mm):")
        w_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        self._w_spin = QDoubleSpinBox()
        self._w_spin.setRange(10.0, 5000.0)
        self._w_spin.setDecimals(1)
        self._w_spin.setValue(width_mm)
        self._w_spin.setSuffix(" mm")
        w_col.addWidget(w_lbl)
        w_col.addWidget(self._w_spin)
        dims_row.addLayout(w_col)

        h_col = QVBoxLayout()
        h_lbl = QLabel("Altezza (mm):")
        h_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        self._h_spin = QDoubleSpinBox()
        self._h_spin.setRange(10.0, 5000.0)
        self._h_spin.setDecimals(1)
        self._h_spin.setValue(height_mm)
        self._h_spin.setSuffix(" mm")
        h_col.addWidget(h_lbl)
        h_col.addWidget(self._h_spin)
        dims_row.addLayout(h_col)

        layout.addLayout(dims_row)

        # Description input
        desc_lbl = QLabel("Descrizione (opzionale):")
        desc_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        layout.addWidget(desc_lbl)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Es. Per depliant orizzontale a 3 ante con alette")
        layout.addWidget(self._desc_edit)

        # Overwrite warning banner
        self._warn_frame = QFrame()
        self._warn_frame.setStyleSheet(
            f"background: {t.mix(t.ACCENT, t.SURFACE, 0.2)};"
            f"border: 1px solid {t.ACCENT}; border-radius: {t.RADIUS_SM}px; padding: 4px 8px;"
        )
        w_layout = QHBoxLayout(self._warn_frame)
        w_layout.setContentsMargins(6, 4, 6, 4)
        self._warn_lbl = QLabel("⚠️ Esiste già un formato con questo nome. Verrà aggiornato.")
        self._warn_lbl.setStyleSheet(f"color: {t.ACCENT}; font-size: {t.TEXT_2XS}px;")
        w_layout.addWidget(self._warn_lbl)
        layout.addWidget(self._warn_frame)
        self._warn_frame.setVisible(False)

        layout.addStretch(1)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch(1)

        btn_cancel = QPushButton("Annulla")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        self._btn_save = QPushButton("  Salva formato")
        self._btn_save.setIcon(lucide("save_preset", 16, t.ACCENT_FG))
        self._btn_save.setProperty("accent", True)
        self._btn_save.clicked.connect(self._on_save)
        btns.addWidget(self._btn_save)

        layout.addLayout(btns)
        self._on_name_changed(self._name_edit.text())

    def _on_name_changed(self, text: str):
        cleaned = text.strip()
        self._btn_save.setEnabled(bool(cleaned))
        existing_custom = [f["name"] for f in SheetFormatManager.load_custom_formats()]
        is_overwrite = cleaned in existing_custom
        self._warn_frame.setVisible(is_overwrite)

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            return
        if name in SHEET_PRESETS_MM or name == "Custom":
            QMessageBox.warning(
                self,
                "Nome riservato",
                f"'{name}' è un nome riservato per i formati standard di sistema. Scegli un altro nome.",
            )
            return

        w = self._w_spin.value()
        h = self._h_spin.value()
        desc = self._desc_edit.text().strip()
        try:
            SheetFormatManager.save_custom_format(name, w, h, description=desc)
            self._saved_name = name
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Errore salvataggio", f"Impossibile salvare il formato: {exc}")

    @property
    def saved_name(self) -> str:
        return self._saved_name


class FormatManagerDialog(QDialog):
    """Dialog to list, add, delete, import, and export paper formats."""

    formats_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Gestione Formati Carta — Laydown")
        self.setMinimumSize(680, 480)
        self.resize(750, 520)

        self._build_ui()
        self._populate_table()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.SPACE_4, t.SPACE_4, t.SPACE_4, t.SPACE_4)
        layout.setSpacing(t.SPACE_3)

        # Header
        h_lbl = QLabel("Catalogo Formati Carta")
        h_lbl.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 700; color: {t.FG};")
        layout.addWidget(h_lbl)

        sub_lbl = QLabel(
            "Visualizza i formati standard europei e tipografici oppure crea e gestisci i tuoi formati "
            "personalizzati salvati in JSON."
        )
        sub_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        layout.addWidget(sub_lbl)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Formato", "Dimensioni (L × A)", "Tipo", "Descrizione"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, 1)

        # Action buttons row
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(t.SPACE_2)

        self._btn_add = QPushButton("  Nuovo formato…")
        self._btn_add.setIcon(lucide("plus", 15))
        self._btn_add.clicked.connect(self._on_add)
        btn_bar.addWidget(self._btn_add)

        self._btn_delete = QPushButton("  Elimina")
        self._btn_delete.setIcon(lucide("trash", 15))
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._on_delete)
        btn_bar.addWidget(self._btn_delete)

        btn_bar.addStretch(1)

        self._btn_import = QPushButton("  Importa JSON…")
        self._btn_import.setIcon(lucide("load_preset", 15))
        self._btn_import.clicked.connect(self._on_import)
        btn_bar.addWidget(self._btn_import)

        self._btn_export = QPushButton("  Esporta JSON…")
        self._btn_export.setIcon(lucide("save_preset", 15))
        self._btn_export.clicked.connect(self._on_export)
        btn_bar.addWidget(self._btn_export)

        layout.addLayout(btn_bar)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch(1)
        btn_close = QPushButton("Chiudi")
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        layout.addLayout(footer)

    def _populate_table(self):
        self._table.setRowCount(0)
        row = 0

        # 1. Custom formats first (so user sees their own formats on top)
        custom_formats = SheetFormatManager.load_custom_formats(reload=True)
        for fmt in custom_formats:
            self._table.insertRow(row)
            name = fmt["name"]
            w = float(fmt.get("width_mm", 0.0))
            h = float(fmt.get("height_mm", 0.0))
            desc = fmt.get("description", "")

            name_item = QTableWidgetItem(f"★ {name}")
            name_item.setData(Qt.ItemDataRole.UserRole, (name, True))  # (name, is_custom)
            dim_item = QTableWidgetItem(_dim_str(w, h))
            type_item = QTableWidgetItem("Personalizzato")
            type_item.setForeground(Qt.GlobalColor.darkYellow)
            desc_item = QTableWidgetItem(desc)

            for col, item in enumerate([name_item, dim_item, type_item, desc_item]):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)
            row += 1

        # 2. Built-in presets
        for key, (w, h) in SHEET_PRESETS_MM.items():
            self._table.insertRow(row)
            name_item = QTableWidgetItem(key)
            name_item.setData(Qt.ItemDataRole.UserRole, (key, False))
            dim_item = QTableWidgetItem(_dim_str(w, h))
            type_item = QTableWidgetItem("Standard di sistema")
            type_item.setForeground(Qt.GlobalColor.gray)

            desc = ""
            if "SRA" in key:
                desc = "Formato tipografico per rifilo con abbondanze"
            elif key in ("A3+", "12x18"):
                desc = "Formato standard per macchine digital press"
            elif key in ("70x100", "64x88"):
                desc = "Formato classico offset da cartiera"
            elif key.startswith("B"):
                desc = "Formato serie ISO B intermedia"
            elif key in ("Letter", "Legal", "Tabloid"):
                desc = "Formato standard nordamericano ANSI"
            else:
                desc = "Formato serie ISO A europea"

            desc_item = QTableWidgetItem(desc)
            for col, item in enumerate([name_item, dim_item, type_item, desc_item]):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)
            row += 1

        self._on_selection_changed()

    def _on_selection_changed(self):
        selected = self._table.selectedItems()
        if not selected:
            self._btn_delete.setEnabled(False)
            return

        row = selected[0].row()
        item = self._table.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            name, is_custom = data
            self._btn_delete.setEnabled(bool(is_custom))
        else:
            self._btn_delete.setEnabled(False)

    def _on_add(self):
        dialog = FormatSaveDialog(parent=self)
        if dialog.exec():
            self._populate_table()
            self.formats_changed.emit()

    def _on_delete(self):
        selected = self._table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = self._table.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or not data[1]:
            return

        name = data[0]
        reply = QMessageBox.question(
            self,
            "Conferma eliminazione",
            f"Sei sicuro di voler eliminare il formato personalizzato '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            SheetFormatManager.delete_custom_format(name)
            self._populate_table()
            self.formats_changed.emit()

    def _on_export(self):
        custom = SheetFormatManager.load_custom_formats()
        if not custom:
            QMessageBox.information(
                self,
                "Nessun formato personalizzato",
                "Non ci sono formati personalizzati da esportare. I formati standard sono già integrati nell'app.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Esporta formati personalizzati",
            "formati_carta.json",
            "File JSON (*.json)",
        )
        if path:
            try:
                SheetFormatManager.export_formats(path)
                QMessageBox.information(
                    self,
                    "Esportazione completata",
                    f"Esportati {len(custom)} formati personalizzati in:\n{path}",
                )
            except Exception as exc:
                QMessageBox.critical(self, "Errore esportazione", f"Impossibile esportare: {exc}")

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importa formati personalizzati",
            "",
            "File JSON (*.json)",
        )
        if path:
            try:
                count = SheetFormatManager.import_formats(path)
                self._populate_table()
                self.formats_changed.emit()
                QMessageBox.information(
                    self,
                    "Importazione completata",
                    f"Importati o aggiornati {count} formati con successo.",
                )
            except Exception as exc:
                QMessageBox.critical(self, "Errore importazione", f"Impossibile importare formati: {exc}")
