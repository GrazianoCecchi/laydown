"""
Sheet Format Manager — User-defined and professional paper formats for Laydown.

Handles persistence of custom sheet sizes to JSON, synchronization with the
engine's USER_SHEET_PRESETS_MM registry, and generation of UI display options.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from laydown.engine.data_model import (
    SHEET_PRESETS_MM,
    clear_user_sheet_presets,
    register_user_sheet_preset,
    unregister_user_sheet_preset,
)

FORMAT_VERSION = 1

# Standard human labels for built-in presets
_BUILTIN_LABELS = {
    "A5": "A5 (148 × 210 mm)",
    "A4": "A4 (210 × 297 mm)",
    "SRA4": "SRA4 (225 × 320 mm)",
    "A3": "A3 (297 × 420 mm)",
    "SRA3": "SRA3 (320 × 450 mm)",
    "A3+": "A3+ / Super A3 (329 × 483 mm)",
    "12x18": "12×18\" (304.8 × 457.2 mm)",
    "A2": "A2 (420 × 594 mm)",
    "SRA2": "SRA2 (450 × 640 mm)",
    "B4": "B4 (250 × 353 mm)",
    "B3": "B3 (353 × 500 mm)",
    "B2": "B2 (500 × 707 mm)",
    "SRA1": "SRA1 (640 × 900 mm)",
    "64x88": "64×88 Tipografico (640 × 880 mm)",
    "70x100": "70×100 Tipografico (700 × 1000 mm)",
    "Letter": "Letter (215.9 × 279.4 mm)",
    "Legal": "Legal (215.9 × 355.6 mm)",
    "Tabloid": "Tabloid (279.4 × 431.8 mm)",
}


def _dim_str(w: float, h: float) -> str:
    """Format dimensions cleanly, omitting .0 when exact."""
    sw = f"{w:.0f}" if w.is_integer() else f"{w:.1f}"
    sh = f"{h:.0f}" if h.is_integer() else f"{h:.1f}"
    return f"{sw} × {sh} mm"


def get_sheet_formats_file_path() -> str:
    """Return the absolute path to sheet_formats.json in user AppData."""
    try:
        from PyQt6.QtCore import QStandardPaths
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    except Exception:
        app_data = ""
    if not app_data:
        app_data = os.path.expanduser("~/.laydown")
    os.makedirs(app_data, exist_ok=True)
    return os.path.join(app_data, "sheet_formats.json")


class SheetFormatManager:
    """
    Manages custom paper formats and synchronizes them with the engine.
    """

    _cached_formats: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def get_file_path(cls) -> str:
        return get_sheet_formats_file_path()

    @classmethod
    def load_custom_formats(cls, reload: bool = False) -> List[Dict[str, Any]]:
        """Load custom formats from JSON and sync with the engine."""
        if cls._cached_formats is not None and not reload:
            return list(cls._cached_formats)

        path = cls.get_file_path()
        if not os.path.exists(path):
            cls._cached_formats = []
            cls.sync_to_engine()
            return []

        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            formats = payload.get("formats", [])
            if not isinstance(formats, list):
                formats = []
            cls._cached_formats = formats
        except Exception:
            cls._cached_formats = []

        cls.sync_to_engine()
        return list(cls._cached_formats)

    @classmethod
    def save_catalog(cls, formats: List[Dict[str, Any]]) -> None:
        """Persist custom formats list to disk."""
        path = cls.get_file_path()
        payload = {
            "format": FORMAT_VERSION,
            "application": "Laydown",
            "formats": formats,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        cls._cached_formats = list(formats)
        cls.sync_to_engine()

    @classmethod
    def sync_to_engine(cls) -> None:
        """Register all cached custom formats in the engine's USER_SHEET_PRESETS_MM."""
        clear_user_sheet_presets()
        formats = cls._cached_formats or []
        for fmt in formats:
            name = fmt.get("name", "").strip()
            w = float(fmt.get("width_mm", 0.0))
            h = float(fmt.get("height_mm", 0.0))
            if name and w > 0 and h > 0:
                register_user_sheet_preset(name, w, h)

    @classmethod
    def save_custom_format(
        cls,
        name: str,
        width_mm: float,
        height_mm: float,
        description: str = "",
    ) -> Dict[str, Any]:
        """Add or update a custom format and persist to JSON."""
        name = name.strip()
        if not name:
            raise ValueError("Il nome del formato non può essere vuoto.")
        if name in SHEET_PRESETS_MM or name == "Custom":
            raise ValueError(f"'{name}' è un nome riservato per i formati standard.")

        w = float(width_mm)
        h = float(height_mm)
        if w <= 0 or h <= 0:
            raise ValueError("Le dimensioni devono essere maggiori di zero.")

        # Ensure portrait orientation for canonical storage (w < h)
        canon_w, canon_h = min(w, h), max(w, h)

        formats = cls.load_custom_formats()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Check if updating existing
        existing_idx = next((i for i, f in enumerate(formats) if f.get("name") == name), -1)
        record = {
            "name": name,
            "width_mm": canon_w,
            "height_mm": canon_h,
            "description": description.strip(),
            "created_at": formats[existing_idx].get("created_at", now) if existing_idx >= 0 else now,
            "updated_at": now,
        }

        if existing_idx >= 0:
            formats[existing_idx] = record
        else:
            formats.append(record)

        cls.save_catalog(formats)
        return record

    @classmethod
    def delete_custom_format(cls, name: str) -> bool:
        """Remove a custom format by name."""
        formats = cls.load_custom_formats()
        initial_len = len(formats)
        formats = [f for f in formats if f.get("name") != name]
        if len(formats) < initial_len:
            cls.save_catalog(formats)
            unregister_user_sheet_preset(name)
            return True
        return False

    @classmethod
    def export_formats(cls, target_path: str) -> None:
        """Export custom formats to an external JSON file."""
        formats = cls.load_custom_formats()
        payload = {
            "format": FORMAT_VERSION,
            "application": "Laydown",
            "type": "custom_sheet_formats",
            "formats": formats,
        }
        with open(target_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    @classmethod
    def import_formats(cls, source_path: str) -> int:
        """Import formats from an external JSON file. Returns count of new/updated formats."""
        with open(source_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict) or "formats" not in payload:
            raise ValueError("Il file selezionato non è un catalogo di formati Laydown valido.")

        incoming = payload.get("formats", [])
        if not isinstance(incoming, list):
            raise ValueError("Formato file non valido (elenco 'formats' assente).")

        current = cls.load_custom_formats()
        by_name = {f["name"]: f for f in current}
        count = 0

        for item in incoming:
            name = item.get("name", "").strip()
            w = float(item.get("width_mm", 0.0))
            h = float(item.get("height_mm", 0.0))
            if name and w > 0 and h > 0 and name not in SHEET_PRESETS_MM and name != "Custom":
                by_name[name] = {
                    "name": name,
                    "width_mm": min(w, h),
                    "height_mm": max(w, h),
                    "description": item.get("description", "").strip(),
                    "created_at": item.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                count += 1

        cls.save_catalog(list(by_name.values()))
        return count

    @classmethod
    def get_preset_options(cls) -> Tuple[Tuple[str, str], ...]:
        """
        Return the complete (key, label) tuples for the sheet preset dropdown.
        Combines built-in presets, user-defined custom formats, and 'Custom'.
        """
        options: List[Tuple[str, str]] = []

        # 1. Built-in presets
        for key in SHEET_PRESETS_MM:
            label = _BUILTIN_LABELS.get(key, f"{key} ({_dim_str(*SHEET_PRESETS_MM[key])})")
            options.append((key, label))

        # 2. User custom formats
        custom_formats = cls.load_custom_formats()
        for f in custom_formats:
            name = f["name"]
            w = float(f.get("width_mm", 0.0))
            h = float(f.get("height_mm", 0.0))
            options.append((name, f"★ {name} ({_dim_str(w, h)})"))

        # 3. Custom freeform entry
        options.append(("Custom", "Personalizzato"))
        return tuple(options)
