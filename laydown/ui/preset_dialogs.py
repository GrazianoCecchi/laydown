"""
Preset Management Dialogs for Laydown.

Provides user-friendly dialogs to:
1. Save the current workspace configuration as a named preset with optional description (PresetSaveDialog).
2. Manage, inspect, rename, delete, import, and export presets (PresetManagerDialog).
"""

from typing import Callable, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from laydown.ui import theme as t
from laydown.ui.icons import lucide
from laydown.ui.presets import PresetManager


class PresetSaveDialog(QDialog):
    """Dialog to save current settings as a new or updated preset."""

    def __init__(
        self,
        current_values: dict,
        default_name: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._values = current_values
        self._saved_name = ""

        self.setWindowTitle("Salva Preimpostazione — Laydown")
        self.setMinimumWidth(440)
        self.resize(480, 240)

        self._build_ui(default_name)

    def _build_ui(self, default_name: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.SPACE_4, t.SPACE_4, t.SPACE_4, t.SPACE_4)
        layout.setSpacing(t.SPACE_3)

        # Title / header
        h_lbl = QLabel("Salva impostazioni correnti come preimpostazione")
        h_lbl.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 700; color: {t.FG};")
        layout.addWidget(h_lbl)

        sub_lbl = QLabel(
            "La preimpostazione verrà archiviata nel catalogo centrale dell'applicazione "
            "e sarà immediatamente selezionabile dal menu rapido."
        )
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        layout.addWidget(sub_lbl)

        # Name input
        name_lbl = QLabel("Nome preimpostazione:")
        name_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; font-weight: 600; color: {t.FG};")
        layout.addWidget(name_lbl)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Es. Biglietti da visita 10-up (A3)")
        self._name_edit.setText(default_name)
        self._name_edit.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name_edit)

        # Description input
        desc_lbl = QLabel("Descrizione (opzionale):")
        desc_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        layout.addWidget(desc_lbl)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Es. Formato finito 85x55 mm con 2 mm di abbondanza")
        layout.addWidget(self._desc_edit)

        # Overwrite warning banner
        self._warn_frame = QFrame()
        self._warn_frame.setStyleSheet(
            f"background: {t.mix(t.ACCENT, t.SURFACE, 0.2)};"
            f"border: 1px solid {t.ACCENT}; border-radius: {t.RADIUS_SM}px; padding: 4px 8px;"
        )
        w_layout = QHBoxLayout(self._warn_frame)
        w_layout.setContentsMargins(6, 4, 6, 4)
        self._warn_lbl = QLabel("⚠️ Esiste già una preimpostazione con questo nome. Verrà sovrascritta.")
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

        self._btn_save = QPushButton("  Salva preimpostazione")
        self._btn_save.setIcon(lucide("save_preset", 16, t.ACCENT_FG))
        self._btn_save.setProperty("accent", True)
        self._btn_save.clicked.connect(self._on_save)
        btns.addWidget(self._btn_save)

        layout.addLayout(btns)
        self._on_name_changed(self._name_edit.text())

    def _on_name_changed(self, text: str):
        cleaned = text.strip()
        self._btn_save.setEnabled(bool(cleaned))
        existing = PresetManager.list_names()
        is_overwrite = cleaned in existing
        self._warn_frame.setVisible(is_overwrite)

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            return
        desc = self._desc_edit.text().strip()
        try:
            PresetManager.save_preset(name, self._values, description=desc)
            self._saved_name = name
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Errore salvataggio", f"Impossibile salvare la preimpostazione: {exc}")

    @property
    def saved_name(self) -> str:
        return self._saved_name


class PresetManagerDialog(QDialog):
    """Dialog to manage, rename, apply, delete, import, and export presets."""

    preset_applied = pyqtSignal(str, dict)

    def __init__(
        self,
        current_values: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._current_values = current_values
        self.setWindowTitle("Gestione Preimpostazioni — Laydown")
        self.resize(760, 480)
        self.setMinimumSize(640, 380)

        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SPACE_4, t.SPACE_3, t.SPACE_4, t.SPACE_3)
        root.setSpacing(t.SPACE_3)

        # Header
        top_row = QHBoxLayout()
        h_lbl = QLabel("Catalogo Preimpostazioni di Imposizione")
        h_lbl.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 700; color: {t.FG};")
        top_row.addWidget(h_lbl)
        top_row.addStretch(1)

        btn_import = QPushButton("  Importa da file…")
        btn_import.setIcon(lucide("load_preset", 14, t.FG))
        btn_import.clicked.connect(self._on_import)
        top_row.addWidget(btn_import)

        btn_export = QPushButton("  Esporta su file…")
        btn_export.setIcon(lucide("save_preset", 14, t.FG))
        btn_export.clicked.connect(self._on_export)
        top_row.addWidget(btn_export)

        root.addLayout(top_row)

        # Table of presets
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Nome Preimpostazione", "Descrizione", "Tipo"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, 240)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(lambda _: self._on_apply())
        self._table.setStyleSheet(
            f"QTableWidget {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER};"
            f"                border-radius: {t.RADIUS_SM}px; color: {t.FG}; gridline-color: {t.BORDER}; }}"
            f"QHeaderView::section {{ background: {t.SURFACE}; color: {t.FG_MUTED};"
            f"                         border: 1px solid {t.BORDER}; padding: 4px 6px;"
            f"                         font-size: {t.TEXT_2XS}px; font-weight: 700; }}"
            f"QTableWidget::item:selected {{ background: {t.RAISED}; color: {t.FG}; font-weight: 600; }}"
        )
        root.addWidget(self._table, 1)

        # Details box
        self._details_frame = QFrame()
        self._details_frame.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_SM}px; padding: 6px 10px;"
        )
        det_layout = QVBoxLayout(self._details_frame)
        det_layout.setContentsMargins(8, 6, 8, 6)
        det_layout.setSpacing(2)

        self._det_title = QLabel("Seleziona una preimpostazione")
        self._det_title.setStyleSheet(f"font-size: {t.TEXT_XS}px; font-weight: 700; color: {t.FG};")
        det_layout.addWidget(self._det_title)

        self._det_summary = QLabel("")
        self._det_summary.setWordWrap(True)
        self._det_summary.setStyleSheet(f"font-size: {t.TEXT_2XS}px; color: {t.FG_MUTED};")
        det_layout.addWidget(self._det_summary)
        root.addWidget(self._details_frame)

        # Bottom actions bar
        bottom_bar = QHBoxLayout()

        self._btn_reset_factory = QPushButton("Ripristina predefiniti di fabbrica")
        self._btn_reset_factory.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_XS}px;")
        self._btn_reset_factory.clicked.connect(self._on_reset_factory)
        bottom_bar.addWidget(self._btn_reset_factory)

        bottom_bar.addStretch(1)

        self._btn_rename = QPushButton("Rinomina / Modifica…")
        self._btn_rename.setEnabled(False)
        self._btn_rename.clicked.connect(self._on_rename)
        bottom_bar.addWidget(self._btn_rename)

        self._btn_update_current = QPushButton("Aggiorna con impostazioni attuali")
        self._btn_update_current.setEnabled(False)
        self._btn_update_current.setToolTip("Sovrascrive la preimpostazione selezionata con i valori correnti del progetto")
        self._btn_update_current.clicked.connect(self._on_update_current)
        bottom_bar.addWidget(self._btn_update_current)

        self._btn_delete = QPushButton("Elimina")
        self._btn_delete.setEnabled(False)
        self._btn_delete.setStyleSheet(f"color: {t.DESTRUCTIVE};")
        self._btn_delete.clicked.connect(self._on_delete)
        bottom_bar.addWidget(self._btn_delete)

        self._btn_apply = QPushButton("  Applica al progetto")
        self._btn_apply.setIcon(lucide("generate", 16, t.ACCENT_FG))
        self._btn_apply.setProperty("accent", True)
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._on_apply)
        bottom_bar.addWidget(self._btn_apply)

        btn_close = QPushButton("Chiudi")
        btn_close.clicked.connect(self.close)
        bottom_bar.addWidget(btn_close)

        root.addLayout(bottom_bar)

    def _refresh_list(self, select_name: str = ""):
        catalog = PresetManager.load_catalog(reload=True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(catalog))

        selected_row = -1
        for row, (name, item) in enumerate(catalog.items()):
            # Name
            it_name = QTableWidgetItem(name)
            it_name.setData(Qt.ItemDataRole.UserRole, name)
            self._table.setItem(row, 0, it_name)

            # Description
            desc = item.get("description", "")
            it_desc = QTableWidgetItem(desc)
            self._table.setItem(row, 1, it_desc)

            # Type badge
            is_fac = item.get("is_factory", False)
            type_str = "Standard" if is_fac else "Utente"
            it_type = QTableWidgetItem(type_str)
            it_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, it_type)

            if name == select_name:
                selected_row = row

        if selected_row >= 0:
            self._table.selectRow(selected_row)
        elif self._table.rowCount() > 0:
            self._table.selectRow(0)

    def _selected_name(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _on_selection_changed(self):
        name = self._selected_name()
        has_sel = bool(name)
        self._btn_apply.setEnabled(has_sel)
        self._btn_rename.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)
        self._btn_update_current.setEnabled(has_sel and self._current_values is not None)

        if not has_sel:
            self._det_title.setText("Nessuna preimpostazione selezionata")
            self._det_summary.setText("")
            return

        meta = PresetManager.get_preset_meta(name)
        if not meta:
            return

        self._det_title.setText(name)
        vals = meta.get("values", {})
        preset_sheet = vals.get("sheet.preset", "A3")
        orient = getattr(vals.get("sheet.orientation"), "name", str(vals.get("sheet.orientation", "Landscape")))
        l_type = getattr(vals.get("layout.layout_type"), "name", str(vals.get("layout.layout_type", "NUP")))
        nup = vals.get("layout.nup", 2)
        bleed = vals.get("source.bleed_mm", 0.0)

        desc = meta.get("description", "")
        summary_lines = []
        if desc:
            summary_lines.append(f"<b>Descrizione:</b> {desc}")
        summary_lines.append(
            f"<b>Foglio:</b> {preset_sheet} ({orient}) &nbsp;|&nbsp; "
            f"<b>Tipo layout:</b> {l_type} ({nup}-up) &nbsp;|&nbsp; "
            f"<b>Abbondanza (bleed):</b> {bleed} mm"
        )
        self._det_summary.setText("<br>".join(summary_lines))

    def _on_apply(self):
        name = self._selected_name()
        if not name:
            return
        vals = PresetManager.get_preset(name)
        if vals is not None:
            self.preset_applied.emit(name, vals)
            self.accept()

    def _on_rename(self):
        name = self._selected_name()
        if not name:
            return
        meta = PresetManager.get_preset_meta(name)
        old_desc = meta.get("description", "") if meta else ""

        dlg = QDialog(self)
        dlg.setWindowTitle("Modifica Preimpostazione")
        dlg.resize(420, 180)
        ly = QVBoxLayout(dlg)

        ly.addWidget(QLabel("Nuovo nome:"))
        name_edit = QLineEdit(name)
        ly.addWidget(name_edit)

        ly.addWidget(QLabel("Nuova descrizione:"))
        desc_edit = QLineEdit(old_desc)
        ly.addWidget(desc_edit)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_c = QPushButton("Annulla")
        btn_c.clicked.connect(dlg.reject)
        btn_ok = QPushButton("Salva modifiche")
        btn_ok.setProperty("accent", True)
        btn_ok.clicked.connect(dlg.accept)
        btns.addWidget(btn_c)
        btns.addWidget(btn_ok)
        ly.addLayout(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_name = name_edit.text().strip()
            new_desc = desc_edit.text().strip()
            if new_name:
                PresetManager.rename_preset(name, new_name, new_description=new_desc)
                self._refresh_list(select_name=new_name)

    def _on_update_current(self):
        name = self._selected_name()
        if not name or not self._current_values:
            return
        reply = QMessageBox.question(
            self,
            "Conferma aggiornamento",
            f"Vuoi sovrascrivere i valori della preimpostazione '{name}' con le impostazioni correnti del progetto?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            meta = PresetManager.get_preset_meta(name)
            desc = meta.get("description", "") if meta else ""
            PresetManager.save_preset(name, self._current_values, description=desc)
            self._refresh_list(select_name=name)
            QMessageBox.information(self, "Aggiornato", f"Preimpostazione '{name}' aggiornata con successo.")

    def _on_delete(self):
        name = self._selected_name()
        if not name:
            return
        reply = QMessageBox.question(
            self,
            "Conferma eliminazione",
            f"Sei sicuro di voler eliminare la preimpostazione '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            PresetManager.delete_preset(name)
            self._refresh_list()

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importa preimpostazione da file", "", "Preimpostazioni Laydown (*.json *.laydown.json);;Tutti i file (*)"
        )
        if path:
            try:
                imported_name = PresetManager.import_from_file(path)
                self._refresh_list(select_name=imported_name)
                QMessageBox.information(
                    self,
                    "Importazione completata",
                    f"Preimpostazione importata con successo nel catalogo con il nome:\n'{imported_name}'",
                )
            except Exception as exc:
                QMessageBox.critical(self, "Errore importazione", f"Impossibile importare il file: {exc}")

    def _on_export(self):
        name = self._selected_name()
        if not name:
            return
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Esporta preimpostazione su file", f"{safe_name}.laydown.json", "Preimpostazioni Laydown (*.laydown.json *.json)"
        )
        if path:
            try:
                PresetManager.export_to_file(name, path)
                QMessageBox.information(
                    self, "Esportazione completata", f"Preimpostazione esportata con successo in:\n{path}"
                )
            except Exception as exc:
                QMessageBox.critical(self, "Errore esportazione", f"Impossibile esportare il file: {exc}")

    def _on_reset_factory(self):
        reply = QMessageBox.question(
            self,
            "Ripristina predefiniti",
            "Vuoi ripristinare il catalogo alle preimpostazioni di fabbrica standard? "
            "Le preimpostazioni personalizzate verranno sovrascritte.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            PresetManager.reset_to_factory()
            self._refresh_list()
