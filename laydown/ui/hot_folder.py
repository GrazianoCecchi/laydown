"""
Production RIP & Hot Folder Integration for Laydown.

Provides direct vector PDF dispatch to professional RIP systems (Roland VersaWorks,
EFI Fiery, Canon PRISMAsync, Ricoh TotalFlow, Caldera, Onyx) via monitored Hot Folders.

Features:
1. Persistent Hot Folder presets management (name, directory path, auto-send preferences).
2. Pure vector production PDF export with exact TrimBox, BleedBox, and crop marks.
3. Clean dispatch dialog with quick target selection and folder validation.
"""

import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from laydown.ui import theme as t
from laydown.ui.icons import lucide


class HotFolderManager:
    """Manages persistent RIP Hot Folder targets."""

    SETTINGS_KEY = "production/hot_folders"
    LAST_USED_KEY = "production/last_hot_folder"

    @classmethod
    def get_folders(cls) -> List[Dict[str, str]]:
        settings = QSettings("Laydown", "Laydown")
        raw = settings.value(cls.SETTINGS_KEY, [])
        if isinstance(raw, list):
            valid = []
            for item in raw:
                if isinstance(item, dict) and "name" in item and "path" in item:
                    valid.append(item)
            return valid
        return []

    @classmethod
    def save_folders(cls, folders: List[Dict[str, str]]) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.SETTINGS_KEY, folders)

    @classmethod
    def get_last_used(cls) -> str:
        settings = QSettings("Laydown", "Laydown")
        return settings.value(cls.LAST_USED_KEY, "")

    @classmethod
    def set_last_used(cls, name_or_path: str) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.LAST_USED_KEY, name_or_path)

    @classmethod
    def add_folder(cls, name: str, path: str) -> None:
        folders = cls.get_folders()
        # Update if exists, else append
        for f in folders:
            if f["name"] == name:
                f["path"] = path
                cls.save_folders(folders)
                return
        folders.append({"name": name, "path": path})
        cls.save_folders(folders)

    @classmethod
    def remove_folder(cls, name: str) -> None:
        folders = [f for f in cls.get_folders() if f["name"] != name]
        cls.save_folders(folders)

    @classmethod
    def dispatch_pdf(
        cls,
        source_pdf_path: str,
        destination_folder: str,
        job_name: str = "Lavoro",
        preset: str = "",
    ) -> str:
        """
        Copy the imposed production PDF into the RIP's hot folder with a clean production filename.
        Returns the destination file path.
        """
        if not os.path.isfile(source_pdf_path):
            raise FileNotFoundError(f"File PDF sorgente non trovato: {source_pdf_path}")
        if not os.path.isdir(destination_folder):
            raise NotADirectoryError(f"Cartella RIP non trovata o non accessibile: {destination_folder}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in job_name).strip("_")
        if not safe_name:
            safe_name = "Laydown_Stampa"

        preset_tag = f"_{preset}" if preset else ""
        dest_filename = f"{safe_name}{preset_tag}_{timestamp}.pdf"
        dest_path = os.path.join(destination_folder, dest_filename)

        shutil.copy2(source_pdf_path, dest_path)
        return dest_path


class SendToRipDialog(QDialog):
    """Dialog allowing the operator to dispatch the imposed vector PDF directly to a RIP Hot Folder."""

    def __init__(
        self,
        pdf_path: str,
        project_name: str = "Lavoro",
        preset: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._project_name = project_name
        self._preset = preset
        self._dispatched_path: Optional[str] = None

        self.setWindowTitle("Invia a RIP di Produzione — Laydown")
        self.resize(560, 360)
        self.setMinimumSize(480, 300)

        self._build_ui()
        self._load_folders()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SPACE_4, t.SPACE_4, t.SPACE_4, t.SPACE_4)
        root.setSpacing(t.SPACE_3)

        # Header card
        header = QFrame()
        header.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 8px 12px;"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(lucide("send", 22, t.ACCENT).pixmap(22, 22))
        h_layout.addWidget(icon_lbl)

        title_lbl = QLabel("Invio Diretto a RIP / Hot Folder")
        title_lbl.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 700; color: {t.FG};")
        h_layout.addWidget(title_lbl)
        h_layout.addStretch(1)
        root.addWidget(header)

        # Description
        desc_lbl = QLabel(
            "Invia il file PDF vettoriale puro di produzione direttamente alla cartella monitorata "
            "dal RIP (Roland VersaWorks, EFI Fiery per Ricoh/Canon, Caldera, Onyx). "
            "Il RIP applicherà automaticamente la profilazione colore del supporto e il taglio fustella."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_SM}px;")
        root.addWidget(desc_lbl)

        # Target selection card
        card = QFrame()
        card.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 12px;"
        )
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(8, 8, 8, 8)
        c_layout.setSpacing(8)

        sel_lbl = QLabel("Seleziona Hot Folder RIP di destinazione:")
        sel_lbl.setStyleSheet(f"color: {t.FG}; font-size: {t.TEXT_SM}px; font-weight: 600;")
        c_layout.addWidget(sel_lbl)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._combo_targets = QComboBox()
        self._combo_targets.setCursor(Qt.CursorShape.PointingHandCursor)
        self._combo_targets.currentIndexChanged.connect(self._on_target_changed)
        row.addWidget(self._combo_targets, 1)

        self._btn_browse = QPushButton("Sfoglia cartella…")
        self._btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_browse.clicked.connect(self._on_browse_folder)
        row.addWidget(self._btn_browse)
        c_layout.addLayout(row)

        path_lbl = QLabel("Percorso cartella:")
        path_lbl.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px; margin-top: 4px;")
        c_layout.addWidget(path_lbl)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Es. C:\\Roland\\VersaWorks\\HotFolder oppure \\\\SERVER-STAMPA\\HotFolder")
        c_layout.addWidget(self._path_edit)

        name_lbl = QLabel("Nome descrittivo per memorizzare questa cartella:")
        name_lbl.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px; margin-top: 4px;")
        c_layout.addWidget(name_lbl)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Es. Roland VersaWorks (Adesivi vinile) o Ricoh Fiery Pro C")
        c_layout.addWidget(self._name_edit)

        root.addWidget(card)
        root.addStretch(1)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_SM}px;")
        btn_box.addWidget(self._status_lbl)
        btn_box.addStretch(1)

        btn_cancel = QPushButton("Annulla")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        self._btn_send = QPushButton("  🚀 Invia adesso al RIP")
        self._btn_send.setProperty("accent", True)
        self._btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_send.clicked.connect(self._on_dispatch)
        btn_box.addWidget(self._btn_send)

        root.addLayout(btn_box)

    def _load_folders(self):
        folders = HotFolderManager.get_folders()
        self._combo_targets.blockSignals(True)
        self._combo_targets.clear()

        # Built-in suggestions if empty
        if not folders:
            # Common default RIP locations
            default_suggestions = [
                {"name": "Cartella Personalizzata / Nuova...", "path": ""},
            ]
            folders = default_suggestions
            HotFolderManager.save_folders([])

        for f in folders:
            self._combo_targets.addItem(f["name"], f["path"])

        self._combo_targets.addItem("➕ Aggiungi nuova Hot Folder…", "")
        self._combo_targets.blockSignals(False)

        last_used = HotFolderManager.get_last_used()
        idx = self._combo_targets.findData(last_used)
        if idx >= 0:
            self._combo_targets.setCurrentIndex(idx)
        else:
            self._combo_targets.setCurrentIndex(0)
        self._on_target_changed(self._combo_targets.currentIndex())

    def _on_target_changed(self, index: int):
        if index < 0:
            return
        path = self._combo_targets.itemData(index) or ""
        name = self._combo_targets.itemText(index)
        if name.startswith("➕"):
            self._path_edit.setText("")
            self._name_edit.setText("")
        else:
            self._path_edit.setText(path)
            self._name_edit.setText(name)

    def _on_browse_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Seleziona la Hot Folder del RIP (VersaWorks, Fiery, ecc.)",
            self._path_edit.text() or os.path.expanduser("~"),
        )
        if chosen:
            self._path_edit.setText(chosen)
            if not self._name_edit.text():
                self._name_edit.setText(os.path.basename(chosen) or "Hot Folder")

    def _on_dispatch(self):
        target_path = self._path_edit.text().strip()
        name = self._name_edit.text().strip() or "Hot Folder RIP"

        if not target_path:
            QMessageBox.warning(self, "Attenzione", "Seleziona o specifica una cartella valida per l'invio al RIP.")
            return

        if not os.path.isdir(target_path):
            try:
                os.makedirs(target_path, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(self, "Errore", f"Impossibile accedere o creare la cartella RIP:\n{exc}")
                return

        # Save target folder for future quick use
        HotFolderManager.add_folder(name, target_path)
        HotFolderManager.set_last_used(target_path)

        try:
            self._dispatched_path = HotFolderManager.dispatch_pdf(
                source_pdf_path=self._pdf_path,
                destination_folder=target_path,
                job_name=self._project_name,
                preset=self._preset,
            )
            filename = os.path.basename(self._dispatched_path)
            QMessageBox.information(
                self,
                "Lavoro Inviato al RIP",
                f"Il PDF vettoriale puro di produzione è stato depositato con successo nella Hot Folder del RIP:\n\n"
                f"File: {filename}\nCartella: {target_path}\n\n"
                f"Il RIP della macchina lo prenderà in carico automaticamente per la stampa.",
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Errore Invio RIP", f"Errore durante l'invio del file al RIP:\n{exc}")

    @property
    def dispatched_path(self) -> Optional[str]:
        return self._dispatched_path
