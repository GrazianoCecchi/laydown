"""
Production RIP & Direct Network Printing Integration for Laydown.

Provides direct vector PDF dispatch to:
1. Professional digital presses via direct TCP/IP RAW socket (port 9100 + PJL) or
   LPR / Virtual Printers (port 515), optimized for Ricoh Pro C5300SL, Canon imagePRESS,
   Xerox, Konica Minolta.
2. Monitored Hot Folders on disk/network shares (Roland VersaWorks, EFI Fiery,
   Canon PRISMAsync, Caldera, Onyx).
"""

from datetime import datetime
import os
import shutil
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from laydown.engine.net_print import (
    PrinterStatus,
    query_pjl_printer_info,
    send_pdf_lpr,
    send_pdf_raw_socket,
)
from laydown.ui import theme as t
from laydown.ui.icons import lucide


class HotFolderManager:
    """Manages persistent RIP Hot Folder targets and direct network printer preferences."""

    SETTINGS_KEY = "production/hot_folders"
    LAST_USED_KEY = "production/last_hot_folder"

    # Network printer settings
    NET_IP_KEY = "production/net_printer_ip"
    NET_PORT_KEY = "production/net_printer_port"
    NET_PROTO_KEY = "production/net_printer_proto"
    NET_QUEUE_KEY = "production/net_printer_queue"
    NET_COPIES_KEY = "production/net_printer_copies"
    NET_DUPLEX_KEY = "production/net_printer_duplex"
    LAST_TAB_KEY = "production/last_rip_tab"

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
        return str(settings.value(cls.LAST_USED_KEY, ""))

    @classmethod
    def set_last_used(cls, name_or_path: str) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.LAST_USED_KEY, name_or_path)

    @classmethod
    def add_folder(cls, name: str, path: str) -> None:
        folders = cls.get_folders()
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

    # ── Network printer persistent settings ──

    @classmethod
    def get_net_ip(cls) -> str:
        settings = QSettings("Laydown", "Laydown")
        return str(settings.value(cls.NET_IP_KEY, "192.168.1.155"))

    @classmethod
    def set_net_ip(cls, ip: str) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.NET_IP_KEY, ip)

    @classmethod
    def get_net_port(cls) -> int:
        settings = QSettings("Laydown", "Laydown")
        val = settings.value(cls.NET_PORT_KEY, 9100)
        try:
            return int(val)
        except (ValueError, TypeError):
            return 9100

    @classmethod
    def set_net_port(cls, port: int) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.NET_PORT_KEY, int(port))

    @classmethod
    def get_net_proto(cls) -> str:
        settings = QSettings("Laydown", "Laydown")
        return str(settings.value(cls.NET_PROTO_KEY, "raw"))

    @classmethod
    def set_net_proto(cls, proto: str) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.NET_PROTO_KEY, proto)

    @classmethod
    def get_net_queue(cls) -> str:
        settings = QSettings("Laydown", "Laydown")
        return str(settings.value(cls.NET_QUEUE_KEY, "lp"))

    @classmethod
    def set_net_queue(cls, queue: str) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.NET_QUEUE_KEY, queue)

    @classmethod
    def get_net_copies(cls) -> int:
        settings = QSettings("Laydown", "Laydown")
        val = settings.value(cls.NET_COPIES_KEY, 1)
        try:
            return max(1, int(val))
        except (ValueError, TypeError):
            return 1

    @classmethod
    def set_net_copies(cls, copies: int) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.NET_COPIES_KEY, max(1, int(copies)))

    @classmethod
    def get_net_duplex(cls) -> str:
        settings = QSettings("Laydown", "Laydown")
        return str(settings.value(cls.NET_DUPLEX_KEY, "simplex"))

    @classmethod
    def set_net_duplex(cls, duplex: str) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.NET_DUPLEX_KEY, duplex)

    @classmethod
    def get_last_tab(cls) -> int:
        settings = QSettings("Laydown", "Laydown")
        val = settings.value(cls.LAST_TAB_KEY, 0)
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @classmethod
    def set_last_tab(cls, idx: int) -> None:
        settings = QSettings("Laydown", "Laydown")
        settings.setValue(cls.LAST_TAB_KEY, int(idx))

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


# ── Background Workers ──

class NetworkProbeWorker(QThread):
    """Background worker to probe printer online identity without freezing UI."""
    probe_done = pyqtSignal(object)  # PrinterStatus

    def __init__(self, ip: str, port: int, parent=None):
        super().__init__(parent)
        self._ip = ip
        self._port = port

    def run(self):
        status = query_pjl_printer_info(self._ip, self._port, timeout=2.5)
        self.probe_done.emit(status)


class NetworkPrintWorker(QThread):
    """Background worker to transmit PDF stream to network printer."""
    progress = pyqtSignal(int, int)  # bytes_sent, total_bytes
    finished_send = pyqtSignal(int)  # total_sent
    error_occurred = pyqtSignal(str) # error_message

    def __init__(
        self,
        pdf_path: str,
        ip: str,
        port: int,
        proto: str,
        job_name: str,
        copies: int,
        duplex: str,
        queue: str,
        parent=None,
    ):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._ip = ip
        self._port = port
        self._proto = proto
        self._job_name = job_name
        self._copies = copies
        self._duplex = duplex
        self._queue = queue
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _cancel_check(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            if self._proto == "lpr":
                sent = send_pdf_lpr(
                    pdf_path=self._pdf_path,
                    ip=self._ip,
                    port=self._port,
                    queue=self._queue,
                    job_name=self._job_name,
                    progress_cb=self._on_progress,
                    cancel_check=self._cancel_check,
                )
            else:
                # RAW port 9100 with PJL
                binding = "short_edge" if self._duplex == "duplex_short" else "long_edge"
                duplex_val = "simplex" if self._duplex == "simplex" else "duplex"
                sent = send_pdf_raw_socket(
                    pdf_path=self._pdf_path,
                    ip=self._ip,
                    port=self._port,
                    job_name=self._job_name,
                    copies=self._copies,
                    duplex=duplex_val,
                    binding=binding,
                    use_pjl=True,
                    progress_cb=self._on_progress,
                    cancel_check=self._cancel_check,
                )
            self.finished_send.emit(sent)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def _on_progress(self, sent: int, total: int):
        self.progress.emit(sent, total)


# ── Dialog ──

class SendToRipDialog(QDialog):
    """
    Dialog allowing the operator to dispatch the imposed vector PDF:
    1. Directly over TCP network (Port 9100 RAW / Port 515 LPR) to Ricoh Pro C5300SL.
    2. To a monitored RIP Hot Folder on disk or network share.
    """

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
        self._active_worker: Optional[NetworkPrintWorker] = None
        self._active_probe: Optional[NetworkProbeWorker] = None

        self.setWindowTitle("Invia a RIP / Stampante di Produzione — Laydown")
        self.resize(600, 480)
        self.setMinimumSize(540, 420)

        self._build_ui()
        self._load_preferences()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(t.SPACE_4, t.SPACE_4, t.SPACE_4, t.SPACE_4)
        root.setSpacing(t.SPACE_3)

        # Header card
        header = QFrame()
        header.setStyleSheet(
            f"background: {t.SURFACE}; border: 1px solid {t.BORDER};"
            f"border-radius: {t.RADIUS_MD}px; padding: 6px 12px;"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(lucide("send", 22, t.ACCENT).pixmap(22, 22))
        h_layout.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_lbl = QLabel("Invio Diretto a Stampante di Produzione / RIP")
        title_lbl.setStyleSheet(f"font-size: {t.TEXT_MD}px; font-weight: 700; color: {t.FG};")
        title_box.addWidget(title_lbl)

        subtitle_lbl = QLabel("Trasmetti il PDF vettoriale puro direttamente via rete (Ricoh Direct) o alla Hot Folder.")
        subtitle_lbl.setStyleSheet(f"font-size: {t.TEXT_XS}px; color: {t.FG_MUTED};")
        title_box.addWidget(subtitle_lbl)

        h_layout.addLayout(title_box)
        h_layout.addStretch(1)
        root.addWidget(header)

        # Tabs
        self._tabs = QTabWidget()
        tabs_style = f"""
        QTabWidget::pane {{
            border: 1px solid {t.BORDER};
            border-radius: {t.RADIUS_MD}px;
            background: {t.SURFACE};
            top: -1px;
            padding: 8px;
        }}
        QTabBar::tab {{
            background: {t.RAISED};
            color: {t.FG_MUTED};
            border: 1px solid {t.BORDER};
            border-bottom: none;
            border-top-left-radius: {t.RADIUS_SM}px;
            border-top-right-radius: {t.RADIUS_SM}px;
            padding: 6px 14px;
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
        self._tabs.setStyleSheet(tabs_style)

        # Tab 1: Network Direct Print (Ricoh Pro C5300SL)
        net_tab = self._build_network_tab()
        self._tabs.addTab(net_tab, "  🌐 Stampante di Rete (Ricoh / Direct PDF)  ")

        # Tab 2: Hot Folder
        hf_tab = self._build_hotfolder_tab()
        self._tabs.addTab(hf_tab, "  📁 Cartella Hot Folder (Fiery, VersaWorks)  ")

        root.addWidget(self._tabs, 1)

        # Progress bar (for network uploads)
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {t.BORDER};
                border-radius: {t.RADIUS_SM}px;
                text-align: center;
                background: {t.INPUT_BG};
                color: {t.FG};
                font-size: {t.TEXT_XS}px;
                height: 18px;
            }}
            QProgressBar::chunk {{
                background: {t.ACCENT};
                border-radius: {t.RADIUS_SM - 1}px;
            }}
        """)
        root.addWidget(self._progress_bar)

        # Bottom buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_SM}px;")
        btn_box.addWidget(self._status_lbl)
        btn_box.addStretch(1)

        self._btn_cancel = QPushButton("Chiudi")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._on_cancel_or_reject)
        btn_box.addWidget(self._btn_cancel)

        self._btn_action = QPushButton("  🚀 Invia adesso alla Stampante")
        self._btn_action.setProperty("accent", True)
        self._btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_action.clicked.connect(self._on_main_action)
        btn_box.addWidget(self._btn_action)

        root.addLayout(btn_box)

        # Connect tab changes after action button is initialized
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _build_network_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        # Network address & Probe row
        conn_frame = QFrame()
        conn_frame.setStyleSheet(
            f"background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT};"
            f"border-radius: {t.RADIUS_SM}px; padding: 8px;"
        )
        conn_layout = QVBoxLayout(conn_frame)
        conn_layout.setContentsMargins(6, 6, 6, 6)
        conn_layout.setSpacing(6)

        c_title = QLabel("Connessione Stampante TCP/IP")
        c_title.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};")
        conn_layout.addWidget(c_title)

        row_ip = QHBoxLayout()
        row_ip.setSpacing(8)

        lbl_ip = QLabel("Indirizzo IP:")
        lbl_ip.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        row_ip.addWidget(lbl_ip)

        self._net_ip_edit = QLineEdit()
        self._net_ip_edit.setPlaceholderText("Es. 192.168.1.155")
        self._net_ip_edit.setText(HotFolderManager.get_net_ip())
        row_ip.addWidget(self._net_ip_edit, 2)

        lbl_port = QLabel("Porta:")
        lbl_port.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        row_ip.addWidget(lbl_port)

        self._net_port_spin = QSpinBox()
        self._net_port_spin.setRange(1, 65535)
        self._net_port_spin.setValue(HotFolderManager.get_net_port())
        row_ip.addWidget(self._net_port_spin, 1)

        self._btn_probe = QPushButton("Verifica connessione")
        self._btn_probe.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_probe.clicked.connect(self._on_probe_printer)
        row_ip.addWidget(self._btn_probe)

        conn_layout.addLayout(row_ip)

        self._badge_probe = QLabel("In attesa di verifica…")
        self._badge_probe.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_XS}px; margin-top: 2px;")
        conn_layout.addWidget(self._badge_probe)

        layout.addWidget(conn_frame)

        # Protocol selection card
        proto_frame = QFrame()
        proto_frame.setStyleSheet(
            f"background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT};"
            f"border-radius: {t.RADIUS_SM}px; padding: 8px;"
        )
        proto_layout = QVBoxLayout(proto_frame)
        proto_layout.setContentsMargins(6, 6, 6, 6)
        proto_layout.setSpacing(6)

        p_title = QLabel("Protocollo di Trasmissione")
        p_title.setStyleSheet(f"font-size: {t.TEXT_SM}px; font-weight: 600; color: {t.FG};")
        proto_layout.addWidget(p_title)

        self._radio_raw = QRadioButton("RAW Socket Porta 9100 (Consigliato per Ricoh Pro C — Diretto ad alta velocità)")
        self._radio_raw.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radio_raw.toggled.connect(self._on_proto_changed)
        proto_layout.addWidget(self._radio_raw)

        self._radio_lpr = QRadioButton("LPR / LPD Porta 515 (Per invio a Stampanti Virtuali configurate su Web Image Monitor)")
        self._radio_lpr.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radio_lpr.toggled.connect(self._on_proto_changed)
        proto_layout.addWidget(self._radio_lpr)

        self._lpr_row_widget = QWidget()
        lpr_row = QHBoxLayout(self._lpr_row_widget)
        lpr_row.setContentsMargins(20, 0, 0, 0)
        lpr_row.setSpacing(8)

        lbl_queue = QLabel("Nome Coda / Stampante Virtuale:")
        lbl_queue.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        lpr_row.addWidget(lbl_queue)

        self._queue_edit = QLineEdit()
        self._queue_edit.setPlaceholderText("Es. lp, raw oppure il nome della Stampante Virtuale WIM")
        self._queue_edit.setText(HotFolderManager.get_net_queue())
        lpr_row.addWidget(self._queue_edit, 1)

        proto_layout.addWidget(self._lpr_row_widget)
        layout.addWidget(proto_frame)

        # Job options card (PJL)
        opts_frame = QFrame()
        opts_frame.setStyleSheet(
            f"background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT};"
            f"border-radius: {t.RADIUS_SM}px; padding: 8px;"
        )
        opts_layout = QGridLayout(opts_frame)
        opts_layout.setContentsMargins(6, 6, 6, 6)
        opts_layout.setHorizontalSpacing(10)
        opts_layout.setVerticalSpacing(6)

        lbl_copies = QLabel("Copie:")
        lbl_copies.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        opts_layout.addWidget(lbl_copies, 0, 0)

        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 999)
        self._copies_spin.setValue(HotFolderManager.get_net_copies())
        opts_layout.addWidget(self._copies_spin, 0, 1)

        lbl_duplex = QLabel("Fronte / Retro:")
        lbl_duplex.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        opts_layout.addWidget(lbl_duplex, 0, 2)

        self._duplex_combo = QComboBox()
        self._duplex_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._duplex_combo.addItem("Solo Fronte (Simplex)", "simplex")
        self._duplex_combo.addItem("Fronte/Retro — Lato Lungo (Long Edge)", "duplex_long")
        self._duplex_combo.addItem("Fronte/Retro — Lato Corto / Booklet (Short Edge)", "duplex_short")
        opts_layout.addWidget(self._duplex_combo, 0, 3)

        layout.addWidget(opts_frame)
        layout.addStretch(1)

        return w

    def _build_hotfolder_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        card = QFrame()
        card.setStyleSheet(
            f"background: {t.RAISED}; border: 1px solid {t.BORDER_SOFT};"
            f"border-radius: {t.RADIUS_SM}px; padding: 8px;"
        )
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(6, 6, 6, 6)
        c_layout.setSpacing(6)

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
        self._path_edit.setPlaceholderText("Es. C:\\Roland\\VersaWorks\\HotFolder oppure \\\\SERVER\\HotFolder")
        c_layout.addWidget(self._path_edit)

        name_lbl = QLabel("Nome descrittivo per memorizzare questa cartella:")
        name_lbl.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_2XS}px; margin-top: 4px;")
        c_layout.addWidget(name_lbl)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Es. Roland VersaWorks (Adesivi vinile) o Fiery Rip")
        c_layout.addWidget(self._name_edit)

        layout.addWidget(card)
        layout.addStretch(1)

        return w

    def _load_preferences(self):
        # Protocol radio
        proto = HotFolderManager.get_net_proto()
        if proto == "lpr":
            self._radio_lpr.setChecked(True)
            self._lpr_row_widget.setVisible(True)
        else:
            self._radio_raw.setChecked(True)
            self._lpr_row_widget.setVisible(False)

        # Duplex combo
        duplex = HotFolderManager.get_net_duplex()
        idx = self._duplex_combo.findData(duplex)
        if idx >= 0:
            self._duplex_combo.setCurrentIndex(idx)

        # Hot folders
        self._load_folders()

        # Tab index
        last_tab = HotFolderManager.get_last_tab()
        if 0 <= last_tab < self._tabs.count():
            self._tabs.setCurrentIndex(last_tab)
        self._on_tab_changed(self._tabs.currentIndex())

    def _load_folders(self):
        folders = HotFolderManager.get_folders()
        self._combo_targets.blockSignals(True)
        self._combo_targets.clear()

        if not folders:
            folders = [{"name": "Cartella Personalizzata / Nuova...", "path": ""}]
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

    def _on_tab_changed(self, index: int):
        HotFolderManager.set_last_tab(index)
        if not hasattr(self, "_btn_action") or self._btn_action is None:
            return
        if index == 0:
            self._btn_action.setText("  🚀 Invia adesso alla Stampante")
        else:
            self._btn_action.setText("  📁 Deposita nella Hot Folder")

    def _on_proto_changed(self):
        is_lpr = self._radio_lpr.isChecked()
        self._lpr_row_widget.setVisible(is_lpr)
        if is_lpr and self._net_port_spin.value() == 9100:
            self._net_port_spin.setValue(515)
        elif not is_lpr and self._net_port_spin.value() == 515:
            self._net_port_spin.setValue(9100)

    def _on_probe_printer(self):
        ip = self._net_ip_edit.text().strip()
        port = self._net_port_spin.value()
        if not ip:
            self._badge_probe.setText("Inserisci un indirizzo IP valido.")
            self._badge_probe.setStyleSheet(f"color: {t.FG_FAINT}; font-size: {t.TEXT_XS}px;")
            return

        self._badge_probe.setText("🔍 Connessione in corso a " + ip + "…")
        self._badge_probe.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_XS}px;")
        self._btn_probe.setEnabled(False)

        self._active_probe = NetworkProbeWorker(ip=ip, port=port, parent=self)
        self._active_probe.probe_done.connect(self._on_probe_result)
        self._active_probe.start()

    def _on_probe_result(self, status: PrinterStatus):
        self._btn_probe.setEnabled(True)
        if status.is_online:
            txt = f"🟢 Connesso: {status.model}"
            if status.status_display:
                txt += f" — Stato: {status.status_display}"
            self._badge_probe.setText(txt)
            self._badge_probe.setStyleSheet("color: #22c55e; font-size: 12px; font-weight: 600;")
        else:
            self._badge_probe.setText(f"🔴 Non in linea: {status.error_message}")
            self._badge_probe.setStyleSheet("color: #ef4444; font-size: 12px;")

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

    def _on_cancel_or_reject(self):
        if self._active_worker and self._active_worker.isRunning():
            self._active_worker.cancel()
            self._active_worker.wait(1000)
        self.reject()

    def _on_main_action(self):
        if self._tabs.currentIndex() == 0:
            self._dispatch_network()
        else:
            self._dispatch_hotfolder()

    def _dispatch_network(self):
        ip = self._net_ip_edit.text().strip()
        port = self._net_port_spin.value()
        copies = self._copies_spin.value()
        duplex = self._duplex_combo.currentData() or "simplex"
        proto = "lpr" if self._radio_lpr.isChecked() else "raw"
        queue = self._queue_edit.text().strip() or "lp"

        if not ip:
            QMessageBox.warning(self, "Attenzione", "Specifica un indirizzo IP valido per la stampante.")
            return

        # Persist settings
        HotFolderManager.set_net_ip(ip)
        HotFolderManager.set_net_port(port)
        HotFolderManager.set_net_proto(proto)
        HotFolderManager.set_net_queue(queue)
        HotFolderManager.set_net_copies(copies)
        HotFolderManager.set_net_duplex(duplex)

        self._btn_action.setEnabled(False)
        self._btn_probe.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._status_lbl.setText("Avvio trasmissione in corso…")

        self._active_worker = NetworkPrintWorker(
            pdf_path=self._pdf_path,
            ip=ip,
            port=port,
            proto=proto,
            job_name=self._project_name,
            copies=copies,
            duplex=duplex,
            queue=queue,
            parent=self,
        )
        self._active_worker.progress.connect(self._on_net_progress)
        self._active_worker.finished_send.connect(self._on_net_finished)
        self._active_worker.error_occurred.connect(self._on_net_error)
        self._active_worker.start()

    def _on_net_progress(self, sent: int, total: int):
        if total > 0:
            pct = int(min(100, max(0, sent * 100 / total)))
            self._progress_bar.setValue(pct)
            mb_sent = sent / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._status_lbl.setText(f"Inviati {mb_sent:.1f} MB di {mb_total:.1f} MB ({pct}%)…")

    def _on_net_finished(self, total_sent: int):
        self._progress_bar.setValue(100)
        self._btn_action.setEnabled(True)
        self._btn_probe.setEnabled(True)
        self._status_lbl.setText("Trasmissione completata con successo.")
        mb = total_sent / (1024 * 1024)

        target_model = self._badge_probe.text().replace("🟢 Connesso: ", "").split(" —")[0]
        if not target_model or "Non in linea" in target_model:
            target_model = f"Stampante {self._net_ip_edit.text()}:{self._net_port_spin.value()}"

        QMessageBox.information(
            self,
            "Lavoro Inviato con Successo",
            f"Il PDF vettoriale puro di produzione è stato trasmesso con successo alla stampante:\n\n"
            f"Destinatario: {target_model}\n"
            f"Protocollo: {'LPR (coda ' + self._queue_edit.text() + ')' if self._radio_lpr.isChecked() else 'RAW Socket (Porta ' + str(self._net_port_spin.value()) + ')'}\n"
            f"Dati trasmessi: {mb:.2f} MB\n\n"
            f"La stampante sta rippando il file ed elaborando la stampa.",
        )
        self.accept()

    def _on_net_error(self, err_msg: str):
        self._progress_bar.setVisible(False)
        self._btn_action.setEnabled(True)
        self._btn_probe.setEnabled(True)
        self._status_lbl.setText("Errore durante l'invio.")
        QMessageBox.critical(
            self,
            "Errore Invio Stampante",
            f"Si è verificato un errore durante la trasmissione dei dati alla stampante di rete:\n\n{err_msg}\n\n"
            f"Verifica che l'indirizzo IP ({self._net_ip_edit.text()}) sia corretto e che la stampante sia accesa.",
        )

    def _dispatch_hotfolder(self):
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
