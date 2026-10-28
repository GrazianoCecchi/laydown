"""
Main application window — two columns: sheet canvas on the left, settings on the right.

The settings column is not built here. It is rendered from ``ui/schema.SCHEMA`` by
``ui/panel.py``, so adding a setting means adding a schema entry, and a control that
the engine doesn't honour fails the test suite rather than the operator's job.
"""

import logging
import os
import pathlib
import sys
import tempfile
import traceback
import webbrowser
from typing import Optional

from PyQt6.QtCore import QSize, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QAction, QActionGroup, QDragEnterEvent, QDropEvent, QIcon, QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressDialog, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QStatusBar, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from laydown import __version__
from laydown.engine.data_model import LayoutType, Project
from laydown.engine.impose import impose
from laydown.engine.preflight import Finding, Severity, preflight
from laydown.engine.utils import Unit
from laydown.ui import theme as t
from laydown.ui.help import tutorials_html
from laydown.ui.icons import lucide
from laydown.ui.marks_tab import MarksTab
from laydown.ui.panel import SchemaTab, ValueStore
from laydown.ui.preprocessors_tab import PreprocessorsTab
from laydown.ui.preview_panel import SheetCanvas
from laydown.ui.schema import SCHEMA

_LOG = logging.getLogger(__name__)


def _is_same_path(p1: str, p2: str) -> bool:
    """True when both paths point to the same filesystem target."""
    if not p1 or not p2:
        return False
    try:
        return os.path.normcase(os.path.realpath(p1)) == os.path.normcase(os.path.realpath(p2))
    except Exception:
        return False


_APP_TITLE = "Laydown"
_MAX_RECENT = 8
_ICON_SZ = 22
_TAB_SZ = 24

_TOOL_BTN_STYLE = (
    f"QToolButton {{ padding: 4px; border-radius: {t.RADIUS_SM}px; border: none;"
    f"               background: transparent; }}"
    f"QToolButton:hover {{ background: {t.HOVER_WASH}; }}"
    f"QToolButton:checked {{ background: {t.SELECTED_WASH}; }}"
)


# ── icons — the Lucide set (see laydown/ui/icons.py) ─

# Order matches the four settings tabs: Source, Layout, Sheet, Marks.
_TAB_ICON_NAMES = ("tab_source", "tab_layout", "tab_sheet", "tab_marks")


def _tool_btn(icon, tooltip, checkable=False, checked=False):
    b = QToolButton()
    b.setIcon(icon)
    b.setToolTip(tooltip)
    b.setCheckable(checkable)
    b.setChecked(checked)
    b.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(_TOOL_BTN_STYLE)
    return b


def _vsep(h=20):
    f = QFrame()
    f.setFixedSize(1, h)
    f.setStyleSheet(f"background: {t.BORDER};")
    return f


def app_icon() -> QIcon:
    """The application / window icon, assembled from the packaged PNGs.

    An empty QIcon here is exactly what leaves the Windows taskbar button blank, so
    this must never silently return nothing. We try every plausible asset root — a
    source checkout, and a PyInstaller *onedir* bundle where the data lives under
    ``sys._MEIPASS`` (the ``_internal`` folder) or, on some layouts, beside the exe —
    and fall back to the multi-resolution ``.ico`` if the PNGs can't be found.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(pathlib.Path(meipass) / "assets" / "icons")
        exe_dir = pathlib.Path(sys.executable).resolve().parent
        roots.append(exe_dir / "assets" / "icons")
        roots.append(exe_dir / "_internal" / "assets" / "icons")
    roots.append(
        pathlib.Path(__file__).resolve().parent.parent.parent / "assets" / "icons")

    icon = QIcon()
    for base in roots:
        for size in (16, 24, 32, 48, 64, 128, 256):
            p = base / f"icon_{size}x{size}.png"
            if p.exists():
                icon.addFile(str(p), QSize(size, size))
        if not icon.isNull():
            return icon

    # Last resort: the multi-size Windows .ico, wherever it can be found.
    for base in roots:
        ico = base / "laydown.ico"
        if ico.exists():
            icon.addFile(str(ico))
            break
    return icon


# ── export worker ────────────────────────────────────

class _ExportWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(int, str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._output = ""
        self._cancel = False

    def setup(self, project: Project, output_path: str):
        self._project = project
        self._output = output_path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        if not self._project or not self._output:
            self.failed.emit("Nessun progetto o percorso di destinazione specificato")
            return
        if self._project.source_pdf_path and _is_same_path(self._project.source_pdf_path, self._output):
            self.failed.emit("Impossibile sovrascrivere il PDF di origine")
            return

        temp_path: Optional[str] = None
        try:
            target_dir = os.path.dirname(os.path.abspath(self._output))
            if not os.path.isdir(target_dir):
                raise FileNotFoundError(f"La cartella non esiste: {target_dir}")

            fd, temp_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(self._output)}.",
                suffix=".tmp",
                dir=target_dir,
            )
            os.close(fd)

            def report(current, total):
                if self._cancel:
                    raise _Cancelled()
                self.progress.emit(current, total)

            sheets = impose(self._project, temp_path, progress_callback=report)
            if self._cancel:
                raise _Cancelled()

            os.replace(temp_path, self._output)
            temp_path = None
            self.done.emit(sheets, self._output)
        except _Cancelled:
            self.failed.emit("Esportazione annullata")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


class _Cancelled(Exception):
    """Raised inside the worker when the user cancels — the only way to stop mid-sheet."""


_SEVERITY_LABELS = {
    Severity.ERROR: "Errore",
    Severity.WARNING: "Avviso",
    Severity.NOTE: "Nota",
}

_UNIT_NAMES = {
    Unit.MM: "Millimetri",
    Unit.CM: "Centimetri",
    Unit.INCH: "Pollici",
    Unit.POINT: "Punti",
}


# ── dialogs ──────────────────────────────────────────

class _TutorialsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Laydown — Guida e documentazione")
        self.resize(760, 640)
        column = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(tutorials_html())
        column.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        column.addWidget(buttons)


# ── main window ──────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Dress the whole application, not just this window: dialogs and any widget a
        # QSS rule doesn't reach need the dark palette too, or they fall back to the
        # platform's light one.
        app = QApplication.instance()
        if app is not None:
            t.apply(app)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1100, 750)
        self.resize(1440, 900)
        self.setAcceptDrops(True)

        self._source_path = ""
        self._source_doc = None   # kept open so preflight need not reopen it
        self._findings: list = []
        self._recent: list[str] = []
        self._progress: Optional[QProgressDialog] = None
        self._settings = QSettings("Laydown", "Laydown")

        self._store = ValueStore(self)
        self._store.changed.connect(self._on_values_changed)

        self._export = _ExportWorker(self)
        self._export.progress.connect(self._on_export_progress)
        self._export.done.connect(self._on_export_done)
        self._export.failed.connect(self._on_export_failed)

        self._update_title()
        self._load_recent()
        self._build_menubar()
        self._build_main_area()
        self._populate_preset_combo()
        self._restore_view_settings()
        self._on_values_changed()

    # ── title ────────────────────────────────────

    def _update_title(self, filename: str = ""):
        suffix = filename or "Imposizione PDF"
        self.setWindowTitle(f"{_APP_TITLE} — {suffix}")

    # ── menus ────────────────────────────────────

    def _build_menubar(self):
        bar = self.menuBar()

        def mi(name):  # a menu-sized Lucide icon
            return lucide(name, 16)

        file_menu = bar.addMenu("&File")
        self._act_open = QAction(mi("open"), "&Apri PDF…", self,
                                 shortcut=QKeySequence("Ctrl+O"), triggered=self._on_open)
        file_menu.addAction(self._act_open)
        self._recent_menu = file_menu.addMenu("Apri &recenti")
        self._recent_menu.setIcon(mi("recent"))
        self._rebuild_recent_menu()

        self._act_close = QAction(mi("close"), "&Chiudi PDF", self,
                                  shortcut=QKeySequence("Ctrl+F4"),
                                  enabled=False, triggered=self._on_close_pdf)
        file_menu.addAction(self._act_close)

        self._act_inspect = QAction(mi("inspect"), "&Ispeziona PDF…", self,
                                    shortcut=QKeySequence("Ctrl+I"),
                                    enabled=False, triggered=self._on_inspect_pdf)
        file_menu.addAction(self._act_inspect)
        file_menu.addSeparator()

        self._act_generate = QAction(mi("generate"), "&Genera PDF", self,
                                     shortcut=QKeySequence("Ctrl+G"),
                                     enabled=False, triggered=self._on_export)
        file_menu.addAction(self._act_generate)

        self._act_print = QAction(mi("print"), "&Stampa…", self,
                                  shortcut=QKeySequence("Ctrl+P"),
                                  enabled=False, triggered=self._on_print)
        file_menu.addAction(self._act_print)

        self._act_send_rip = QAction(mi("send"), "Invia a &RIP (Hot Folder)…", self,
                                     enabled=False, triggered=self._on_send_to_rip)
        file_menu.addAction(self._act_send_rip)
        file_menu.addSeparator()

        preset_menu = file_menu.addMenu("Preim&postazioni")
        preset_menu.setIcon(mi("save_preset"))
        preset_menu.addAction(QAction(mi("save_preset"), "&Salva impostazioni correnti…", self,
                                      triggered=self._on_save_preset))
        preset_menu.addAction(QAction(mi("load_preset"), "&Gestione preimpostazioni…", self,
                                      triggered=self._on_manage_presets))
        preset_menu.addSeparator()
        preset_menu.addAction(QAction(mi("load_preset"), "&Importa da file esterno…", self,
                                      triggered=self._on_import_preset_file))
        preset_menu.addAction(QAction(mi("save_preset"), "&Esporta su file esterno…", self,
                                      triggered=self._on_export_preset_file))
        file_menu.addSeparator()
        file_menu.addAction(QAction(mi("quit"), "&Esci", self,
                                    shortcut=QKeySequence("Alt+F4"), triggered=self.close))

        edit_menu = bar.addMenu("&Modifica")
        self._act_undo = QAction(mi("undo"), "&Annulla", self, shortcut="Ctrl+Z",
                                 enabled=False, triggered=self._undo)
        self._act_redo = QAction(mi("redo"), "&Ripeti", self, shortcut="Ctrl+Y",
                                 enabled=False, triggered=self._redo)
        edit_menu.addAction(self._act_undo)
        edit_menu.addAction(self._act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(QAction(mi("reset"), "Ripristina &tutte le impostazioni", self,
                                    triggered=self._on_reset_all))

        view_menu = bar.addMenu("&Visualizza")
        view_menu.addAction(QAction(mi("zoom_in"), "In&grandisci", self, shortcut="Ctrl++",
                                    triggered=lambda: self._canvas.zoom_in()))
        view_menu.addAction(QAction(mi("zoom_out"), "Riduci &zoom", self, shortcut="Ctrl+-",
                                    triggered=lambda: self._canvas.zoom_out()))
        view_menu.addAction(QAction(mi("fit_width"), "&Reimposta zoom", self, shortcut="Ctrl+0",
                                    triggered=lambda: self._canvas.fit_width()))
        units_menu = view_menu.addMenu("&Unità di misura")
        units_menu.setIcon(mi("tab_sheet"))
        unit_group = QActionGroup(self)
        unit_group.setExclusive(True)
        for unit in Unit:
            uname = _UNIT_NAMES.get(unit, unit.name.title())
            action = QAction(f"{uname} ({unit.value})", self, checkable=True,
                             checked=(unit is Unit.MM))
            action.triggered.connect(lambda _, u=unit: self._on_unit_changed(u))
            unit_group.addAction(action)
            units_menu.addAction(action)
        view_menu.addSeparator()

        self._act_nums = QAction("Mostra &numeri di pagina", self, checkable=True, checked=True,
                                 shortcut="Alt+1")
        self._act_frames = QAction("Mostra linee di &taglio", self, checkable=True, checked=True,
                                   shortcut="Alt+2")
        self._act_tops = QAction("Mostra parte &superiore pagine", self, checkable=True, checked=False,
                                 shortcut="Alt+3")
        self._act_previews = QAction("Mostra ante&prime pagine", self, checkable=True, checked=True,
                                     shortcut="Alt+4")
        for action in (self._act_nums, self._act_frames, self._act_tops, self._act_previews):
            action.toggled.connect(self._on_view_toggle)
            view_menu.addAction(action)

        view_menu.addSeparator()
        self._act_margins = QAction(mi("printable_area"), "Mostra area stampabile &periferica", self, checkable=True,
                                    shortcut="Alt+5")
        self._act_margins.toggled.connect(self._on_margins_toggle)
        view_menu.addAction(self._act_margins)

        help_menu = bar.addMenu("&Aiuto")
        help_menu.addAction(QAction(mi("tutorials"), "&Guida e tutorial", self, shortcut="F1",
                                    triggered=self._on_tutorials))
        help_menu.addAction(QAction(mi("preflight"), "&Controllo preliminare (Preflight)…", self, shortcut="F7",
                                    triggered=self._show_preflight_details))
        help_menu.addSeparator()
        help_menu.addAction(QAction(mi("sysdir"), "Apri cartella di &sistema", self,
                                    triggered=self._on_sysdir))
        help_menu.addSeparator()
        help_menu.addAction(QAction(mi("about"), "&Informazioni su Laydown…", self, triggered=self._on_about))

    # ── layout ───────────────────────────────────

    def _build_main_area(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._create_toolbar())
        column.addWidget(self._create_preflight_bar())
        self._canvas = SheetCanvas()
        self._canvas.open_requested.connect(self._on_open)  # double-click empty area
        column.addWidget(self._canvas, 1)
        root.addWidget(left, 1)

        root.addWidget(self._create_settings_panel())

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Pronto — apri un PDF per iniziare")

    def _create_toolbar(self):
        bar = QFrame()
        bar.setFixedHeight(40)
        bar.setObjectName("toolbar")
        bar.setStyleSheet(
            f"#toolbar {{ background: {t.RAISED}; border-bottom: 1px solid {t.BORDER}; }}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(t.SPACE_3, t.SPACE_1, t.SPACE_3, t.SPACE_1)
        row.setSpacing(2)

        columns = QButtonGroup(self)
        columns.setExclusive(True)
        for count, glyph, tip in ((1, "columns_1", "Colonna singola"),
                                  (2, "columns_2", "Due colonne"),
                                  (4, "columns_4", "Quattro colonne")):
            button = _tool_btn(lucide(glyph), tip, checkable=True, checked=(count == 2))
            columns.addButton(button, count)
            row.addWidget(button)
        columns.idClicked.connect(lambda n: self._canvas.set_columns(n))

        row.addWidget(_vsep())
        self._btn_inspect = _tool_btn(lucide("inspect"), "Ispeziona PDF (Ctrl+I)")
        self._btn_inspect.setEnabled(False)
        self._btn_inspect.clicked.connect(self._on_inspect_pdf)
        row.addWidget(self._btn_inspect)

        self._btn_margins = _tool_btn(
            lucide("printable_area"),
            "Mostra area utile e margini periferica (Alt+5)",
            checkable=True,
        )
        self._btn_margins.toggled.connect(self._on_margins_toggle)
        row.addWidget(self._btn_margins)

        row.addWidget(_vsep())

        # ── Preset selector & actions ──
        preset_lbl = QLabel("Predefinito:")
        preset_lbl.setStyleSheet(f"color: {t.FG_MUTED}; font-size: {t.TEXT_2XS}px; font-weight: 600; margin-left: 4px; margin-right: 2px;")
        row.addWidget(preset_lbl)

        self._combo_preset = QComboBox()
        self._combo_preset.setFixedWidth(210)
        self._combo_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._combo_preset.setStyleSheet(
            f"QComboBox {{ background: {t.INPUT_BG}; border: 1px solid {t.BORDER};"
            f"             border-radius: {t.RADIUS_SM}px; padding: 2px 6px;"
            f"             color: {t.FG}; font-size: {t.TEXT_2XS}px; }}"
            f"QComboBox::drop-down {{ border: none; width: 16px; }}"
        )
        self._combo_preset.activated.connect(self._on_preset_combo_activated)
        row.addWidget(self._combo_preset)

        btn_save_p = _tool_btn(lucide("save_preset", 15), "Salva impostazioni correnti come preimpostazione…")
        btn_save_p.clicked.connect(self._on_save_preset)
        row.addWidget(btn_save_p)

        btn_manage_p = _tool_btn(lucide("load_preset", 15), "Gestione preimpostazioni (elenco, modifica, elimina, esporta)…")
        btn_manage_p.clicked.connect(self._on_manage_presets)
        row.addWidget(btn_manage_p)

        row.addStretch(1)
        for glyph, tip, slot in (
            ("zoom_in", "Ingrandisci", lambda: self._canvas.zoom_in()),
            ("zoom_out", "Riduci zoom", lambda: self._canvas.zoom_out()),
        ):
            button = _tool_btn(lucide(glyph), tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addWidget(_vsep())
        for glyph, tip, slot in (
            ("fit_width", "Adatta alla larghezza", lambda: self._canvas.fit_width()),
            ("fit_page", "Adatta foglio intero", lambda: self._canvas.fit_page()),
            ("actual_size", "Dimensione reale (1:1)", lambda: self._canvas.actual_size()),
        ):
            button = _tool_btn(lucide(glyph), tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        # No trailing stretch: the single stretch above pushes the whole zoom/fit
        # group to the right edge, with the column-view buttons kept at the left.
        return bar

    def _create_preflight_bar(self):
        """
        A strip that says what's wrong while there's still time to fix it.

        0.2.0 only reported trouble by raising at export — "margins too large" arrived
        after the operator hit Generate, and subtler problems arrived at the guillotine.
        """
        self._preflight_bar = QFrame()
        self._preflight_bar.setObjectName("preflight")
        self._preflight_bar.setVisible(False)
        row = QHBoxLayout(self._preflight_bar)
        row.setContentsMargins(t.SPACE_3, t.SPACE_2, t.SPACE_3, t.SPACE_2)
        row.setSpacing(t.SPACE_2)

        self._preflight_text = QLabel("")
        self._preflight_text.setWordWrap(True)
        row.addWidget(self._preflight_text, 1)

        self._preflight_more = QPushButton("Dettagli")
        self._preflight_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preflight_more.clicked.connect(self._show_preflight_details)
        row.addWidget(self._preflight_more, 0)
        return self._preflight_bar

    def _run_preflight(self):
        self._findings = []
        try:
            self._findings = preflight(self.build_project(), self._source_doc)
        except Exception as exc:
            _LOG.exception("Preflight could not run: %s", exc)
            self._findings = [
                Finding(
                    Severity.ERROR,
                    f"Impossibile eseguire il preflight: {exc}",
                    detail=traceback.format_exc(),
                )
            ]

        # The strip is for things that need doing something about. Notes are true but
        # not urgent; they stay in Details (Help > Preflight) rather than sitting on
        # screen forever and teaching the operator to tune the strip out.
        loud = [f for f in self._findings
                if f.severity in (Severity.ERROR, Severity.WARNING)]
        notes = len(self._findings) - len(loud)
        if not loud:
            self._preflight_bar.setVisible(False)
            if notes and self._source_path:
                self._status.showMessage(
                    f"{notes} note di controllo preliminare — Aiuto \u2192 Controllo preliminare (Preflight)…")
            return

        worst = loud[0]
        colour = {Severity.ERROR: t.DESTRUCTIVE,
                  Severity.WARNING: t.ACCENT,
                  Severity.NOTE: t.FG_MUTED}[worst.severity]
        self._preflight_bar.setStyleSheet(
            f"#preflight {{ background: {t.RAISED}; border-bottom: 1px solid {colour}; }}")
        extra = len(self._findings) - 1
        suffix = f"   (+{extra} altri)" if extra > 0 else ""
        sev_label = _SEVERITY_LABELS.get(worst.severity, worst.severity.value)
        self._preflight_text.setText(
            f"<span style='color:{colour}'><b>{sev_label}</b></span> "
            f"<span style='color:{t.FG}'>{worst.message}</span>"
            f"<span style='color:{t.FG_FAINT}'>{suffix}</span>"
        )
        self._preflight_more.setVisible(bool(worst.detail) or extra > 0)
        self._preflight_bar.setVisible(True)

    def _show_preflight_details(self):
        if not getattr(self, "_findings", None):
            QMessageBox.information(self, "Controllo preliminare", "Nessun problema rilevato — il lavoro è corretto.")
            return
        body = "".join(
            f"<p style='margin:0 0 10px 0'><b>{_SEVERITY_LABELS.get(f.severity, f.severity.value)}:</b> {f.message}"
            + (f"<br><span style='color:{t.FG_MUTED}'>{f.detail}</span>" if f.detail else "")
            + "</p>"
            for f in self._findings
        )
        QMessageBox.information(self, "Controllo preliminare", f"<div>{body}</div>")

    def _collection_for(self, control):
        """Supply the bespoke editors for the two genuinely growable settings."""
        if control.target == "preprocessors":
            editor = PreprocessorsTab()
            editor.changed.connect(
                lambda: self._store.set("preprocessors", editor.get_steps()))
        elif control.target == "marks":
            editor = MarksTab()
            editor.changed.connect(lambda: self._store.set("marks", editor.get_marks()))
        else:
            return None
        self._collections[control.target] = editor
        return editor

    def _create_settings_panel(self):
        panel = QFrame()
        panel.setObjectName("settings")
        panel.setFixedWidth(t.PANEL_W)
        panel.setStyleSheet(
            f"#settings {{ background: {t.SURFACE}; border-left: 1px solid {t.BORDER}; }}")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        # Tab strip: four QToolButtons that share the panel width equally (each a
        # quarter), each centring its own icon. QToolButtons are used rather than a
        # QTabBar because a QTabBar left-biases an icon-only tab by ~7px (its empty
        # text still reserves layout); a QToolButton centres its icon exactly, and an
        # Expanding size policy lets the four grow with the panel.
        tab_header = QFrame()
        tab_header.setObjectName("tabheader")
        tab_header.setStyleSheet(f"#tabheader {{ background: {t.RAISED}; }}")
        header_row = QHBoxLayout(tab_header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)

        tab_btn_style = (
            f"QToolButton {{ background: transparent; border: none;"
            f"               border-bottom: 2px solid transparent; padding: 9px 0; }}"
            f"QToolButton:hover:!checked {{ background: {t.HOVER_WASH}; }}"
            f"QToolButton:checked {{ border-bottom: 2px solid {t.ACCENT};"
            f"                       background: {t.SELECTED_WASH}; }}"
        )
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_buttons = []
        for index, tab in enumerate(SCHEMA):
            btn = QToolButton()
            btn.setIcon(lucide(_TAB_ICON_NAMES[index], _TAB_SZ))
            btn.setIconSize(QSize(_TAB_SZ, _TAB_SZ))
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.setToolTip(tab.name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(tab_btn_style)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._tab_group.addButton(btn, index)
            self._tab_buttons.append(btn)
            header_row.addWidget(btn, 1)   # equal stretch → equal quarter widths

        column.addWidget(tab_header)

        self._heading = QLabel(SCHEMA[0].name)
        self._heading.setStyleSheet(
            f"font-size: {t.TEXT_LG}px; font-weight: 600; color: {t.FG};"
            f"padding: {t.SPACE_3}px {t.SPACE_3}px {t.SPACE_2}px {t.SPACE_3}px;"
        )
        column.addWidget(self._heading)

        self._collections: dict = {}
        self._stack = QStackedWidget()
        self._tabs = []
        for tab in SCHEMA:
            page = SchemaTab(tab, self._store, self._collection_for)
            self._tabs.append(page)
            area = QScrollArea()
            area.setWidget(page)
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._stack.addWidget(area)
        column.addWidget(self._stack, 1)
        self._tab_group.idClicked.connect(self._on_tab_switch)

        column.addWidget(self._create_footer())
        return panel

    def _create_footer(self):
        """
        Sticky footer for the one action that delivers the product.

        Toolcraft keeps final delivery actions pinned rather than in the toolbar, so
        the thing you came to do is always reachable without hunting.
        """
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setStyleSheet(
            f"#footer {{ background: {t.RAISED}; border-top: 1px solid {t.BORDER}; }}")
        row = QVBoxLayout(footer)
        row.setContentsMargins(t.SPACE_3, t.SPACE_3, t.SPACE_3, t.SPACE_3)
        row.setSpacing(t.SPACE_2)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(t.SPACE_2)

        self._btn_print = QPushButton("  Stampa…")
        self._btn_print.setIcon(lucide("print", 16, t.FG))
        self._btn_print.setEnabled(False)
        self._btn_print.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_print.clicked.connect(self._on_print)

        self._generate = QPushButton("  Genera PDF")
        self._generate.setIcon(lucide("generate", 16, t.ACCENT_FG))
        self._generate.setProperty("accent", True)
        self._generate.setEnabled(False)
        self._generate.setCursor(Qt.CursorShape.PointingHandCursor)
        self._generate.clicked.connect(self._on_export)

        btns_row.addWidget(self._btn_print, 1)
        btns_row.addWidget(self._generate, 1)
        row.addLayout(btns_row)
        return footer

    def _on_tab_switch(self, index):
        self._stack.setCurrentIndex(index)
        if 0 <= index < len(SCHEMA):
            self._heading.setText(SCHEMA[index].name)

    # ── the product ──────────────────────────────

    def build_project(self) -> Project:
        return self._store.to_project(self._source_path)

    def _on_values_changed(self):
        values = self._store.values()
        for tab in self._tabs:
            tab.refresh(values)
        self._act_undo.setEnabled(self._store.can_undo())
        self._act_redo.setEnabled(self._store.can_redo())
        self._run_preflight()
        if self._source_path:
            if getattr(self, "_act_margins", None) and self._act_margins.isChecked():
                self._canvas.set_printable_margins(True, self._get_printer_margins_pt())
            self._canvas.update_project(self.build_project())

    def _undo(self):
        self._store.undo()
        self._resync_widgets()
        self._rebuild_collection_editors()

    def _redo(self):
        self._store.redo()
        self._resync_widgets()
        self._rebuild_collection_editors()

    def _on_unit_changed(self, unit):
        self._store.set_unit(unit)
        self._settings.setValue("display_unit", unit.name)
        self._status.showMessage(f"Lunghezze visualizzate in {unit.value}")

    def _restore_view_settings(self):
        show_margins = self._settings.value("view/show_printable_margins", False, type=bool)
        if show_margins:
            self._act_margins.setChecked(True)
            self._btn_margins.setChecked(True)
            self._canvas.set_printable_margins(True, self._get_printer_margins_pt())

    def _get_printer_margins_pt(self) -> tuple[float, float, float, float]:
        """Return (left, top, right, bottom) hardware unprintable margins in points."""
        try:
            from laydown.ui.win_print import get_printer_hardware_margins
            printer_name = self._settings.value("print/last_printer", "")
            if not printer_name:
                from PyQt6.QtPrintSupport import QPrinterInfo
                def_p = QPrinterInfo.defaultPrinter()
                if def_p and not def_p.isNull():
                    printer_name = def_p.printerName()
            if printer_name:
                return get_printer_hardware_margins(printer_name)
        except Exception as exc:
            _LOG.debug("Could not determine printer margins: %s", exc)
        # Fallback: 4.2 mm = ~11.9 pt
        fallback_pt = 4.2 * 72.0 / 25.4
        return (fallback_pt, fallback_pt, fallback_pt, fallback_pt)

    def _on_margins_toggle(self, checked: bool):
        if hasattr(self, "_act_margins") and self._act_margins.isChecked() != checked:
            self._act_margins.blockSignals(True)
            self._act_margins.setChecked(checked)
            self._act_margins.blockSignals(False)
        if hasattr(self, "_btn_margins") and self._btn_margins.isChecked() != checked:
            self._btn_margins.blockSignals(True)
            self._btn_margins.setChecked(checked)
            self._btn_margins.blockSignals(False)

        self._settings.setValue("view/show_printable_margins", checked)
        margins = self._get_printer_margins_pt() if checked else None
        self._canvas.set_printable_margins(checked, margins)
        if checked and margins:
            m_l, m_t, m_r, m_b = margins
            self._status.showMessage(
                f"Area stampabile periferica attiva (margini: {m_l*25.4/72:.1f}mm SX, {m_t*25.4/72:.1f}mm ALTO, {m_r*25.4/72:.1f}mm DX, {m_b*25.4/72:.1f}mm BASSO)"
            )
        else:
            self._status.showMessage("Area stampabile periferica nascosta")

    def _on_view_toggle(self, _=None):
        self._canvas.set_overlays(
            show_tops=self._act_tops.isChecked(),
            show_numbers=self._act_nums.isChecked(),
            show_frames=self._act_frames.isChecked(),
            show_previews=self._act_previews.isChecked(),
        )

    # ── files ────────────────────────────────────

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Apri PDF", "", "File PDF (*.pdf);;Tutti i file (*)")
        if path:
            self.load_pdf(path)

    def load_pdf(self, path: str):
        if not os.path.isfile(path):
            self._status.showMessage(f"File non trovato: {path}")
            return
        try:
            import fitz
            doc = fitz.open(path)
            if doc.is_encrypted:
                doc.close()
                self._close_source_doc()
                self._source_path = ""
                self._update_title()
                for widget in (self._act_close, self._act_inspect, self._btn_inspect,
                               self._act_generate, self._generate,
                               self._act_print, self._btn_print, self._act_send_rip):
                    widget.setEnabled(False)
                self._canvas.clear_all()
                msg = "Questo PDF è protetto da password — rimuovi la password e aprilo di nuovo"
                self._findings = [Finding(Severity.ERROR, msg)]
                self._status.showMessage(msg)
                self._preflight_bar.setStyleSheet(
                    f"#preflight {{ background: {t.RAISED}; border-bottom: 1px solid {t.DESTRUCTIVE}; }}")
                self._preflight_text.setText(
                    f"<span style='color:{t.DESTRUCTIVE}'><b>Errore</b></span> "
                    f"<span style='color:{t.FG}'>{msg}</span>"
                )
                self._preflight_more.setVisible(False)
                self._preflight_bar.setVisible(True)
                return
            count = len(doc)
        except Exception as exc:
            self._status.showMessage(f"Impossibile aprire: {exc}")
            return

        self._close_source_doc()
        self._source_doc = doc
        self._source_path = path
        name = os.path.basename(path)
        self._update_title(name)
        self._status.showMessage(f"{name} — {count} pagin{'a' if count == 1 else 'e'}")
        for widget in (self._act_close, self._act_inspect, self._btn_inspect,
                       self._act_generate, self._generate,
                       self._act_print, self._btn_print, self._act_send_rip):
            widget.setEnabled(True)
        self._add_recent(path)
        self._on_values_changed()

    def _close_source_doc(self):
        if self._source_doc is not None:
            try:
                self._source_doc.close()
            except Exception:
                pass
            self._source_doc = None

    def _on_close_pdf(self):
        self._close_source_doc()
        self._source_path = ""
        self._update_title()
        for widget in (self._act_close, self._act_inspect, self._btn_inspect,
                       self._act_generate, self._generate,
                       self._act_print, self._btn_print, self._act_send_rip):
            widget.setEnabled(False)
        self._canvas.clear_all()
        self._run_preflight()
        self._status.showMessage("PDF chiuso")

    def _on_inspect_pdf(self):
        if not self._source_doc or not self._source_path:
            return
        from laydown.ui.inspector_dialog import PdfInspectorDialog
        dialog = PdfInspectorDialog(self._source_doc, self._source_path, self)
        dialog.exec()

    def dragEnterEvent(self, event: QDragEnterEvent):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
            self.load_pdf(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── recent ───────────────────────────────────

    def _load_recent(self):
        raw = self._settings.value("recent_files", [])
        self._recent = [f for f in (raw or []) if os.path.isfile(f)]

    def _add_recent(self, path: str):
        path = os.path.normpath(path)
        if path in self._recent:
            self._recent.remove(path)
        self._recent.insert(0, path)
        del self._recent[_MAX_RECENT:]
        self._settings.setValue("recent_files", self._recent)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        if not self._recent:
            self._recent_menu.addAction("(nessun file recente)").setEnabled(False)
            return
        for path in self._recent:
            action = self._recent_menu.addAction(os.path.basename(path))
            action.setToolTip(path)
            action.triggered.connect(lambda _, p=path: self.load_pdf(p))
        self._recent_menu.addSeparator()
        self._recent_menu.addAction("Cancella recenti", self._clear_recent)

    def _clear_recent(self):
        self._recent.clear()
        self._settings.setValue("recent_files", [])
        self._rebuild_recent_menu()

    # ── presets ──────────────────────────────────

    def _populate_preset_combo(self, selected_name: str = ""):
        from laydown.ui.presets import PresetManager
        self._combo_preset.blockSignals(True)
        self._combo_preset.clear()
        self._combo_preset.addItem("— Seleziona preimpostazione —", "")
        names = PresetManager.list_names()
        target_idx = 0
        for idx, name in enumerate(names, start=1):
            self._combo_preset.addItem(name, name)
            if name == selected_name:
                target_idx = idx
        self._combo_preset.setCurrentIndex(target_idx)
        self._combo_preset.blockSignals(False)

    def _on_preset_combo_activated(self, index: int):
        name = self._combo_preset.itemData(index)
        if not name:
            return
        from laydown.ui.presets import PresetManager
        vals = PresetManager.get_preset(name)
        if vals:
            self._apply_preset(name, vals)

    def _apply_preset(self, name: str, vals: dict):
        self._store.load(vals)
        self._rebuild_collection_editors()
        self._resync_widgets()
        self._populate_preset_combo(selected_name=name)
        self._status.showMessage(f"Preimpostazione applicata: {name}")

    def _on_save_preset(self):
        from laydown.ui.preset_dialogs import PresetSaveDialog
        cur_name = self._combo_preset.currentData() or ""
        dlg = PresetSaveDialog(current_values=self._store.values(), default_name=cur_name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved_name = dlg.saved_name
            self._populate_preset_combo(selected_name=saved_name)
            self._status.showMessage(f"Preimpostazione salvata: {saved_name}")

    def _on_manage_presets(self):
        from laydown.ui.preset_dialogs import PresetManagerDialog
        dlg = PresetManagerDialog(current_values=self._store.values(), parent=self)
        dlg.preset_applied.connect(self._apply_preset)
        dlg.exec()
        cur_name = self._combo_preset.currentData() or ""
        self._populate_preset_combo(selected_name=cur_name)

    def _on_import_preset_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importa preimpostazione da file", "", "Predefiniti Laydown (*.json *.laydown.json);;Tutti i file (*)"
        )
        if not path:
            return
        from laydown.ui.presets import PresetManager
        try:
            name = PresetManager.import_from_file(path)
            vals = PresetManager.get_preset(name)
            if vals:
                self._apply_preset(name, vals)
            else:
                self._populate_preset_combo(selected_name=name)
            self._status.showMessage(f"Preimpostazione importata nel catalogo: {name}")
        except Exception as exc:
            QMessageBox.critical(self, "Impossibile importare la preimpostazione", str(exc))

    def _on_export_preset_file(self):
        cur_name = self._combo_preset.currentData()
        from laydown.ui.presets import PresetManager
        names = PresetManager.list_names()
        target_name = cur_name if cur_name in names else (names[0] if names else "")
        if not target_name:
            QMessageBox.warning(self, "Attenzione", "Nessuna preimpostazione disponibile da esportare.")
            return
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in target_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Esporta preimpostazione", f"{safe_name}.laydown.json", "Predefiniti Laydown (*.laydown.json *.json)"
        )
        if not path:
            return
        try:
            PresetManager.export_to_file(target_name, path)
            self._status.showMessage(f"Preimpostazione '{target_name}' esportata in: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Impossibile esportare la preimpostazione", str(exc))

    def _on_load_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Carica predefinito", "", "Predefinito Laydown (*.json);;Tutti i file (*)")
        if not path:
            return
        from laydown.ui.presets import load_preset
        try:
            self._store.load(load_preset(path))
            self._rebuild_collection_editors()
            self._resync_widgets()
            self._status.showMessage(f"Predefinito caricato — {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Impossibile caricare il predefinito", str(exc))

    def _on_reset_all(self):
        from laydown.ui.schema import defaults
        values = defaults()
        values["preprocessors"] = []
        values["marks"] = []
        self._store.load(values)
        self._rebuild_collection_editors()

    def _resync_widgets(self):
        values = self._store.values()
        for tab in self._tabs:
            tab.sync_from_store(values)

    def _rebuild_collection_editors(self):
        if "preprocessors" in self._collections:
            self._collections["preprocessors"].set_steps(self._store.get("preprocessors") or [])
        if "marks" in self._collections:
            self._collections["marks"].set_marks(self._store.get("marks") or [])

    # ── export ───────────────────────────────────

    @staticmethod
    def _is_offscreen() -> bool:
        app = QApplication.instance()
        if app is not None and app.platformName() == "offscreen":
            return True
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True
        return False

    def _on_export(self):
        if not self._source_path:
            return
        project = self.build_project()
        base = os.path.splitext(os.path.basename(self._source_path))[0]
        layout = project.layout
        suffix = "booklet" if layout.layout_type == LayoutType.BOOKLET else f"{layout.nup}up"
        suggested = f"{base}_{suffix}_{project.sheet.preset}.pdf"

        path, _ = QFileDialog.getSaveFileName(
            self, "Esporta PDF impaginato", suggested, "File PDF (*.pdf)")
        if not path:
            return

        self.export_to(path)

    def _on_print(self):
        if not self._source_path or not self._source_doc:
            return
        errors = [f for f in self._findings if f.severity == Severity.ERROR]
        if errors:
            if not self._is_offscreen():
                QMessageBox.warning(
                    self,
                    "Preflight — Errori bloccanti",
                    f"Impossibile procedere con la stampa: sono presenti errori di preflight bloccanti:\n\n{errors[0].message}",
                )
            return

        from laydown.ui.print_dialog import PrintDialog
        project = self.build_project()
        dialog = PrintDialog(project=project, parent=self)
        dialog.exec()
        if getattr(self, "_act_margins", None) and self._act_margins.isChecked():
            self._canvas.set_printable_margins(True, self._get_printer_margins_pt())

    def _on_send_to_rip(self):
        """Open Send to RIP dialog directly from the main window."""
        if not self._source_path or not self._source_doc:
            return
        errors = [f for f in self._findings if f.severity == Severity.ERROR]
        if errors:
            if not self._is_offscreen():
                QMessageBox.warning(
                    self,
                    "Preflight — Errori bloccanti",
                    f"Impossibile procedere con l'invio al RIP: sono presenti errori di preflight bloccanti:\n\n{errors[0].message}",
                )
            return

        from laydown.engine.impose import impose_to_temp
        from laydown.ui.hot_folder import SendToRipDialog
        project = self.build_project()
        temp_pdf, _ = impose_to_temp(project)
        try:
            name = getattr(project, "name", "") or os.path.splitext(os.path.basename(self._source_path))[0]
            preset = getattr(getattr(project, "sheet", None), "preset", "A3")
            dlg = SendToRipDialog(pdf_path=temp_pdf, project_name=name, preset=preset, parent=self)
            dlg.exec()
        finally:
            if temp_pdf and os.path.exists(temp_pdf):
                try:
                    os.unlink(temp_pdf)
                except Exception:
                    pass

    def export_to(self, path: str) -> bool:
        """Export the current project to *path* atomically.

        Returns False if the export cannot proceed (e.g. attempting to overwrite source).
        """
        if not self._source_path:
            return False
        if _is_same_path(self._source_path, path):
            self._status.showMessage("Cannot overwrite source PDF — choose a different output file (Impossibile sovrascrivere il PDF di origine)")
            return False

        project = self.build_project()
        self._progress = QProgressDialog("Impaginazione in corso…", "Annulla", 0, 100, self)
        self._progress.setWindowTitle("Generazione in corso")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.canceled.connect(self._export.cancel)  # actually stops the work
        self._generate.setEnabled(False)
        self._btn_print.setEnabled(False)
        self._act_generate.setEnabled(False)
        self._act_print.setEnabled(False)
        self._act_send_rip.setEnabled(False)
        self._export.setup(project, path)
        self._export.start()
        return True

    def _on_export_progress(self, current, total):
        if not self._progress:
            return
        try:
            self._progress.setValue(int(current / total * 100) if total else 0)
            self._progress.setLabelText(f"Foglio {min(current + 1, total)} di {total}")
        except RuntimeError:
            pass

    def _close_progress(self):
        try:
            if self._progress:
                self._progress.close()
            pass
        except RuntimeError:
            pass
        self._progress = None
        has_source = bool(self._source_path)
        self._generate.setEnabled(has_source)
        self._btn_print.setEnabled(has_source)
        self._act_generate.setEnabled(has_source)
        self._act_print.setEnabled(has_source)
        self._act_send_rip.setEnabled(has_source)

    def _on_export_done(self, sheets, path):
        self._close_progress()
        self._status.showMessage(f"Esportati {sheets} fogli → {os.path.basename(path)}")
        if not self._is_offscreen():
            QMessageBox.information(self, "Esportazione completata",
                                    f"Salvati {sheets} fogli in:\n{path}")

    def _on_export_failed(self, message):
        self._close_progress()
        self._status.showMessage(message)
        if message != "Esportazione annullata" and not self._is_offscreen():
            QMessageBox.critical(self, "Esportazione non riuscita", message)

    # ── help ─────────────────────────────────────

    def _on_tutorials(self):
        _TutorialsDialog(self).exec()

    def _on_sysdir(self):
        folder = os.path.dirname(os.path.abspath(sys.argv[0]))
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            webbrowser.open(f"file://{folder}")

    def _on_about(self):
        html = (
            f"<h2 style='color:{t.ACCENT}; margin-bottom:2px'>Laydown {__version__}</h2>"
            f"<p style='color:{t.FG_MUTED}; margin-top:0'>Imposizione PDF per la stampa commerciale</p>"
            "<p>Disponi le pagine del tuo PDF sul foglio di stampa — layout n-up, opuscoli "
            "(punto metallico e brossura), segni e crocini di stampa, e posizionamento "
            "con rifilo/abbondanza — con un'anteprima in tempo reale del foglio prima della stampa.</p>"
            "<p>L'imposizione è <b>vettoriale</b>: le pagine sono incorporate, mai rasterizzate, quindi la qualità "
            "corrisponde sempre all'originale. Tutto viene eseguito in locale sul tuo computer — nessun caricamento online, "
            "nessun account, nessuna telemetria.</p>"
            "<p>Creato con Python, PyQt6 e PyMuPDF.</p>"
            "<p><b>Creato da Daniel Mevit</b> — "
            "<a href='https://damt.xyz'>damt.xyz</a><br>"
            "Software libero e open source sotto licenza "
            "<a href='https://www.gnu.org/licenses/agpl-3.0.html'>GNU AGPL v3.0</a> · "
            "<a href='https://github.com/danielmevit/laydown'>codice sorgente su GitHub</a></p>"
            f"<p style='color:{t.FG_FAINT}'>&copy; 2026 Daniel Mevit</p>"
        )
        box = QMessageBox(self)
        box.setWindowTitle("Informazioni su Laydown")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(html)
        box.setIconPixmap(app_icon().pixmap(72, 72))
        # QMessageBox labels don't open links by default; enable it so damt.xyz works.
        for label in box.findChildren(QLabel):
            label.setOpenExternalLinks(True)
        box.exec()

    # ── cleanup ──────────────────────────────────

    def closeEvent(self, event):
        self._close_source_doc()
        self._canvas.cleanup()
        self._export.cancel()
        self._export.wait()
        super().closeEvent(event)
