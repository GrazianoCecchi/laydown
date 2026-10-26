"""
The settings panel, declared rather than wired.

Every control the user sees is described here once: what it edits (``target``, a
dotted path into ``Project``), how it renders, its default, and when it applies.
``ui/panel.py`` turns this into widgets; nothing else builds settings UI by hand.

Two rules carry most of the weight, both learned from the Toolcraft study
(docs/ai/REFERENCE_STUDY.md):

* **Every target must be honoured by the engine.** Enforced by a test against
  ``engine/capabilities.HONOURED``. A control that does nothing fails the build,
  which is the defect 0.2.0 shipped.
* **Hide, never disable.** A setting that doesn't apply to the current mode is
  removed via ``visible_when`` instead of being greyed out, so the panel only ever
  shows controls that can actually do something.

Section titles name the thing being edited (``Booklet``, `Bleed`), never
``Settings`` or ``Options``.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Sequence, Tuple

from laydown.engine.data_model import (
    BookletMode, LayoutType, Orientation, PageScaling, RepeatMode, SourceBox, SHEET_PRESETS_MM,
)


class ControlType(Enum):
    SEGMENTED = "segmented"    # full-width, finite, ≤4 short options
    SELECT = "select"          # dropdown
    SWITCH = "switch"          # binary
    NUMBER = "number"          # spin box
    TEXT = "text"              # single line
    COLLECTION = "collection"  # growable list with its own editor


@dataclass(frozen=True)
class When:
    """A visibility condition on another control's value."""
    target: str
    equals: Any = None
    one_of: Optional[Tuple[Any, ...]] = None

    def holds(self, value: Any) -> bool:
        if self.one_of is not None:
            return value in self.one_of
        return value == self.equals


@dataclass(frozen=True)
class Control:
    type: ControlType
    target: str
    label: str
    default: Any = None
    description: str = ""          # only when it adds meaning beyond the label
    options: Tuple[Tuple[Any, str], ...] = ()
    minimum: float = 0.0
    maximum: float = 1000.0
    step: float = 1.0
    decimals: int = 0
    suffix: str = ""
    placeholder: str = ""
    visible_when: Optional[When] = None


@dataclass(frozen=True)
class Section:
    title: str
    entity: str                    # the product thing these controls edit
    controls: Tuple[Control, ...]
    visible_when: Optional[When] = None


@dataclass(frozen=True)
class Tab:
    name: str
    sections: Tuple[Section, ...]


def _enum_options(enum_cls) -> Tuple[Tuple[Any, str], ...]:
    return tuple((member, member.value) for member in enum_cls)


_SOURCE_BOX_OPTIONS = (
    (SourceBox.MEDIA, "Media Box"),
    (SourceBox.CROP, "Crop Box"),
    (SourceBox.TRIM, "Trim Box"),
    (SourceBox.BLEED, "Bleed Box"),
)

_LAYOUT_OPTIONS = (
    (LayoutType.NUP, "N-Up"),
    (LayoutType.BOOKLET, "Opuscolo"),
)

_SCALING_OPTIONS = (
    (PageScaling.ACTUAL_SIZE, "Dim. reale"),
    (PageScaling.FIT_TO_CELL, "Adatta a cella"),
)

_BOOKLET_MODE_OPTIONS = (
    (BookletMode.SADDLE_STITCH, "P. metallico"),
    (BookletMode.PERFECT_BOUND, "Brossura"),
)

_ORIENTATION_OPTIONS = (
    (Orientation.LANDSCAPE, "Orizzontale"),
    (Orientation.PORTRAIT, "Verticale"),
)

_REPEAT_OPTIONS = (
    (RepeatMode.OFF, "Pagine consecutive"),
    (RepeatMode.FILL_SHEET, "Riempi foglio (Step & Repeat)"),
    (RepeatMode.COPIES, "Numero copie"),
)

_PRESET_OPTIONS = tuple((k, k) for k in SHEET_PRESETS_MM) + (("Custom", "Personalizzato"),)

_IS_BOOKLET = When(target="layout.layout_type", equals=LayoutType.BOOKLET)
_IS_NUP = When(target="layout.layout_type", equals=LayoutType.NUP)


SCHEMA: Tuple[Tab, ...] = (
    Tab(
        name="Origine",
        sections=(
            Section(
                title="Riquadro pagina",
                entity="le pagine in ingresso",
                controls=(
                    Control(
                        type=ControlType.SELECT,
                        target="source.box",
                        label="Riquadro",
                        default=SourceBox.TRIM,
                        description=(
                            "Quale riquadro della pagina di origine viene adattato a ogni cella. Il Trim "
                            "Box è la pagina finita descritta da un PDF per la stampa; se il file non "
                            "contiene riquadri, viene usata l'intera pagina."
                        ),
                        options=_SOURCE_BOX_OPTIONS,
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="source.bleed_mm",
                        label="Abbondanza",
                        default=0.0,
                        description=(
                            "Grafica aggiuntiva estesa oltre il riquadro consentita a fuoriuscire "
                            "dalla cella, per garantire la presenza di inchiostro anche con un taglio non "
                            "perfettamente a registro."
                        ),
                        maximum=50.0, decimals=1, step=0.5, suffix=" mm",
                    ),
                ),
            ),
            Section(
                title="Trasformazioni",
                entity="modifiche applicate prima dell'imposizione",
                controls=(
                    Control(
                        type=ControlType.COLLECTION,
                        target="preprocessors",
                        label="Passaggi",
                    ),
                ),
            ),
        ),
    ),
    Tab(
        name="Layout",
        sections=(
            Section(
                title="Disposizione",
                entity="come vengono disposte le pagine sul foglio",
                controls=(
                    Control(
                        type=ControlType.SEGMENTED,
                        target="layout.layout_type",
                        label="Tipo",
                        default=LayoutType.NUP,
                        options=_LAYOUT_OPTIONS,
                    ),
                    Control(
                        type=ControlType.SELECT,
                        target="layout.nup",
                        label="Pagine per foglio",
                        default=2,
                        options=((1, "1-Up"), (2, "2-Up"), (4, "4-Up"), (6, "6-Up"),
                                 (8, "8-Up"), (9, "9-Up"), (10, "10-Up (2 × 5)"),
                                 (12, "12-Up (3 × 4)"), (16, "16-Up (4 × 4)"),
                                 (20, "20-Up (4 × 5)"), (24, "24-Up (4 × 6)"),
                                 (25, "25-Up (5 × 5)"), (32, "32-Up (4 × 8)"),
                                 (0, "Griglia personalizzata (Righe × Colonne)")),
                        visible_when=_IS_NUP,
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.cols",
                        label="Colonne",
                        default=0,
                        minimum=1,
                        maximum=50,
                        step=1,
                        description="Numero di colonne della griglia (utilizzato con Griglia personalizzata).",
                        visible_when=When(target="layout.nup", equals=0),
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.rows",
                        label="Righe",
                        default=0,
                        minimum=1,
                        maximum=50,
                        step=1,
                        description="Numero di righe della griglia (utilizzato con Griglia personalizzata).",
                        visible_when=When(target="layout.nup", equals=0),
                    ),
                    Control(
                        type=ControlType.SWITCH,
                        target="layout.auto_rotate",
                        label="Ruota per adattare",
                        default=False,
                        description=(
                            "Ruota la pagina di un quarto di giro quando questo permette di riempire meglio "
                            "la cella — ad esempio grafica orizzontale su foglio verticale."
                        ),
                        visible_when=_IS_NUP,
                    ),
                    Control(
                        type=ControlType.SEGMENTED,
                        target="layout.scaling",
                        label="Dimensione",
                        default=PageScaling.ACTUAL_SIZE,
                        description=(
                            "Dim. reale stampa ogni pagina al 100%, riducendola solo se non entra "
                            "nella cella. Adatta a cella scala la pagina per riempire la cella, "
                            "ingrandendo quelle più piccole."
                        ),
                        options=_SCALING_OPTIONS,
                    ),
                ),
            ),
            Section(
                title="Ripetizione (Step & Repeat)",
                entity="duplicazione e riempimento delle pagine sul foglio",
                visible_when=_IS_NUP,
                controls=(
                    Control(
                        type=ControlType.SELECT,
                        target="layout.repeat_mode",
                        label="Modalità",
                        default=RepeatMode.OFF,
                        description=(
                            "Pagine consecutive distribuisce le pagine del PDF una dopo l'altra. "
                            "Riempi foglio (Step & Repeat) replica ciascuna pagina fino a saturare tutte le celle del foglio "
                            "(ideale per biglietti da visita, etichette e volantini). "
                            "Numero copie permette di specificare quante copie produrre per ciascuna pagina."
                        ),
                        options=_REPEAT_OPTIONS,
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.repeat_count",
                        label="Copie per pagina",
                        default=1,
                        minimum=1,
                        maximum=9999,
                        step=1,
                        description="Numero di copie da produrre per ciascuna pagina sorgente.",
                        visible_when=When(target="layout.repeat_mode", equals=RepeatMode.COPIES),
                    ),
                ),
            ),
            Section(
                title="Opuscolo",
                entity="come i fogli piegati formano un libro",
                visible_when=_IS_BOOKLET,
                controls=(
                    Control(
                        type=ControlType.SEGMENTED,
                        target="layout.booklet_mode",
                        label="Rilegatura",
                        default=BookletMode.SADDLE_STITCH,
                        description=(
                            "Punto metallico inserisce ogni foglio dentro l'altro in una sola segnatura "
                            "e pinza lungo la piega. Brossura piega separatamente segnature di "
                            "dimensione fissa e le incolla, cambiando l'ordine delle pagine."
                        ),
                        options=_BOOKLET_MODE_OPTIONS,
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.signature_sheets",
                        label="Fogli per segnatura",
                        default=4,
                        minimum=1, maximum=64, step=1,
                        visible_when=When(target="layout.booklet_mode",
                                          equals=BookletMode.PERFECT_BOUND),
                    ),
                    Control(
                        type=ControlType.SWITCH,
                        target="layout.right_to_left",
                        label="Rilegatura da destra a sinistra",
                        default=False,
                    ),
                    Control(
                        type=ControlType.SWITCH,
                        target="layout.fillers_in_middle",
                        label="Pagine bianche al centro",
                        default=False,
                        description=(
                            "Un opuscolo si estende sempre a un multiplo di quattro. Posiziona le pagine "
                            "bianche al centro invece che alla fine, per mantenere stampata la quarta "
                            "di copertina. Se è una sola pagina bianca, va appena dentro la quarta "
                            "di copertina, mantenendo ogni pagina sul proprio lato dell'apertura."
                        ),
                    ),
                ),
            ),
            Section(
                title="Scorrimento",
                entity="compensazione del margine esterno",
                visible_when=_IS_BOOKLET,
                controls=(
                    Control(
                        type=ControlType.SWITCH,
                        target="layout.creep_enabled",
                        label="Compensa scorrimento",
                        default=False,
                        description=(
                            "I fogli inseriti l'uno nell'altro fuoriescono sul bordo esterno e il rifilo "
                            "del blocco piegato toglie più margine ai fogli interni. Questa opzione sposta "
                            "le pagine verso la piega per pareggiare i margini."
                        ),
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.creep_outer_mm",
                        label="Foglio esterno",
                        default=0.0,
                        minimum=-50.0, maximum=50.0, decimals=2, step=0.1, suffix=" mm",
                        visible_when=When(target="layout.creep_enabled", equals=True),
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.creep_inner_mm",
                        label="Foglio interno",
                        default=0.0,
                        description="Il foglio più interno scorre di più; gli altri fogli interpolano lo spostamento in proporzione.",
                        minimum=-50.0, maximum=50.0, decimals=2, step=0.1, suffix=" mm",
                        visible_when=When(target="layout.creep_enabled", equals=True),
                    ),
                ),
            ),
            Section(
                title="Spaziature",
                entity="spaziature e canali tra le celle",
                controls=(
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.gutter_h_mm",
                        label="Orizzontale",
                        default=0.0,
                        maximum=100.0, decimals=1, step=0.5, suffix=" mm",
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="layout.gutter_v_mm",
                        label="Verticale",
                        default=0.0,
                        maximum=100.0, decimals=1, step=0.5, suffix=" mm",
                        visible_when=_IS_NUP,
                    ),
                ),
            ),
            Section(
                title="Intervallo pagine",
                entity="quali pagine di origine vengono impaginate",
                controls=(
                    Control(
                        type=ControlType.TEXT,
                        target="layout.page_range",
                        label="Pagine",
                        default="",
                        placeholder="es. 1-4,7,10-12  (tutte se vuoto)",
                        description="L'ordine conta: 5,2 impagina prima la pagina 5.",
                    ),
                ),
            ),
        ),
    ),
    Tab(
        name="Foglio",
        sections=(
            Section(
                title="Foglio macchina",
                entity="la carta su cui si stampa",
                controls=(
                    Control(
                        type=ControlType.SELECT,
                        target="sheet.preset",
                        label="Formato",
                        default="A3",
                        options=_PRESET_OPTIONS,
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="sheet.custom_width_mm",
                        label="Larghezza",
                        default=297.0,
                        minimum=1.0, maximum=5000.0, decimals=1, suffix=" mm",
                        visible_when=When(target="sheet.preset", equals="Custom"),
                    ),
                    Control(
                        type=ControlType.NUMBER,
                        target="sheet.custom_height_mm",
                        label="Altezza",
                        default=420.0,
                        minimum=1.0, maximum=5000.0, decimals=1, suffix=" mm",
                        visible_when=When(target="sheet.preset", equals="Custom"),
                    ),
                    Control(
                        type=ControlType.SEGMENTED,
                        target="sheet.orientation",
                        label="Orientamento",
                        default=Orientation.LANDSCAPE,
                        options=_ORIENTATION_OPTIONS,
                    ),
                ),
            ),
            Section(
                title="Margini",
                entity="il bordo non stampabile del foglio",
                controls=(
                    Control(type=ControlType.NUMBER, target="sheet.margin_top_mm",
                            label="Superiore", default=5.0, maximum=500.0, decimals=1, suffix=" mm"),
                    Control(type=ControlType.NUMBER, target="sheet.margin_bottom_mm",
                            label="Inferiore", default=5.0, maximum=500.0, decimals=1, suffix=" mm"),
                    Control(type=ControlType.NUMBER, target="sheet.margin_left_mm",
                            label="Sinistro", default=5.0, maximum=500.0, decimals=1, suffix=" mm"),
                    Control(type=ControlType.NUMBER, target="sheet.margin_right_mm",
                            label="Destro", default=5.0, maximum=500.0, decimals=1, suffix=" mm"),
                ),
            ),
        ),
    ),
    Tab(
        name="Segni",
        sections=(
            Section(
                title="Segni di stampa",
                entity="elementi stampati attorno alle pagine",
                controls=(
                    Control(
                        type=ControlType.COLLECTION,
                        target="marks",
                        label="Segni",
                    ),
                ),
            ),
        ),
    ),
)


# ── helpers ──────────────────────────────────────────

def all_controls() -> Tuple[Control, ...]:
    return tuple(c for tab in SCHEMA for s in tab.sections for c in s.controls)


def all_sections() -> Tuple[Section, ...]:
    return tuple(s for tab in SCHEMA for s in tab.sections)


def all_targets() -> Tuple[str, ...]:
    return tuple(c.target for c in all_controls())


def defaults() -> dict:
    """Every control's default, keyed by target."""
    return {c.target: c.default for c in all_controls()
            if c.type is not ControlType.COLLECTION}


def is_visible(control_or_section, values: dict) -> bool:
    """Whether a control/section applies, given the current values."""
    cond = control_or_section.visible_when
    if cond is None:
        return True
    return cond.holds(values.get(cond.target))


def is_length(control: Control) -> bool:
    """
    Whether a control edits a physical length, and so follows the display unit.

    The model is millimetres everywhere, so a millimetre suffix is exactly the
    marker: these are the fields that get shown in cm/in/pt when asked.
    """
    return control.type is ControlType.NUMBER and control.suffix.strip() == "mm"
