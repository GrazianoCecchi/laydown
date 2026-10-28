"""
Presets — the value store, on disk.

Because every setting lives in one dict keyed by schema target, a preset is that
dict and nothing more. Enums and dataclasses are written as tagged objects so the
file stays readable and a hand-edit is possible.

Unknown keys are dropped on load rather than raising: a preset written by a future
version should still load what this one understands.
"""

import json
import os
from datetime import datetime
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from laydown.engine import data_model
from laydown.engine.data_model import (
    BookletMode, LayoutType, MarkItem, MarkType,
    Orientation, PageScaling, SourceBox,
)
from laydown.ui.schema import defaults

FORMAT_VERSION = 1


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"__enum__": type(value).__name__, "value": value.name}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": type(value).__name__,
            "fields": {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value

    if "__enum__" in value:
        enum_cls = getattr(data_model, value["__enum__"], None)
        if enum_cls is None:
            raise ValueError(f"Unknown setting type: {value['__enum__']}")
        try:
            return enum_cls[value["value"]]
        except KeyError:
            raise ValueError(
                f"{value['__enum__']} has no option {value['value']!r} in this version"
            ) from None

    if "__dataclass__" in value:
        cls = getattr(data_model, value["__dataclass__"], None)
        if cls is None:
            raise ValueError(f"Unknown setting group: {value['__dataclass__']}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: _decode(v) for k, v in value["fields"].items() if k in known})

    return value


def save_preset(path: str, values: dict) -> None:
    payload = {
        "format": FORMAT_VERSION,
        "application": "Laydown",
        "values": {k: _encode(v) for k, v in values.items()},
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def load_preset(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict) or "values" not in payload:
        raise ValueError("That file is not a Laydown preset")
    if payload.get("format", 0) > FORMAT_VERSION:
        raise ValueError(
            f"This preset was written by a newer Laydown (format "
            f"{payload['format']}, this build reads {FORMAT_VERSION})"
        )

    known = set(defaults()) | {"preprocessors", "marks"}
    return {k: _decode(v) for k, v in payload["values"].items() if k in known}


# ── Central Presets Catalog & Factory Templates ─────────────

def get_presets_file_path() -> str:
    """Return the absolute path to the central presets JSON file in user AppData."""
    try:
        from PyQt6.QtCore import QStandardPaths
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    except Exception:
        app_data = ""
    if not app_data:
        app_data = os.path.expanduser("~/.laydown")
    os.makedirs(app_data, exist_ok=True)
    return os.path.join(app_data, "presets.json")


def _get_factory_presets() -> Dict[str, dict]:
    """Return the standard factory presets shipped with Laydown."""
    base_defaults = defaults()
    base_defaults["preprocessors"] = []
    base_defaults["marks"] = []

    factory = {}

    # 1. Standard 2-up su A3
    p1 = dict(base_defaults)
    p1["layout.layout_type"] = LayoutType.NUP
    p1["layout.nup"] = 2
    p1["layout.scaling"] = PageScaling.ACTUAL_SIZE
    p1["sheet.preset"] = "A3"
    p1["sheet.orientation"] = Orientation.LANDSCAPE
    p1["marks"] = [MarkItem(mark_type=MarkType.CROP_MARKS)]
    factory["Standard (2-up su A3)"] = {
        "name": "Standard (2-up su A3)",
        "description": "2 pose affiancate su foglio A3 orizzontale (100% 1:1) con crocini di taglio",
        "created_at": "2026-10-28 00:00:00",
        "updated_at": "2026-10-28 00:00:00",
        "is_factory": True,
        "values": p1,
    }

    # 2. Biglietti da visita 10-up (A3)
    p2 = dict(base_defaults)
    p2["layout.layout_type"] = LayoutType.NUP
    p2["layout.nup"] = 10
    p2["layout.rows"] = 5
    p2["layout.cols"] = 2
    p2["layout.scaling"] = PageScaling.ACTUAL_SIZE
    p2["source.box"] = SourceBox.TRIM
    p2["source.bleed_mm"] = 2.0
    p2["sheet.preset"] = "A3"
    p2["sheet.orientation"] = Orientation.LANDSCAPE
    p2["marks"] = [MarkItem(mark_type=MarkType.CROP_MARKS)]
    factory["Biglietti da visita (10-up su A3)"] = {
        "name": "Biglietti da visita (10-up su A3)",
        "description": "10 pose (2×5) su foglio A3 orizzontale, 100% Reale, TrimBox + 2 mm sbordatura e crocini",
        "created_at": "2026-10-28 00:00:00",
        "updated_at": "2026-10-28 00:00:00",
        "is_factory": True,
        "values": p2,
    }

    # 3. Volantini A5 4-up (A3)
    p3 = dict(base_defaults)
    p3["layout.layout_type"] = LayoutType.NUP
    p3["layout.nup"] = 4
    p3["layout.rows"] = 2
    p3["layout.cols"] = 2
    p3["layout.scaling"] = PageScaling.ACTUAL_SIZE
    p3["source.box"] = SourceBox.TRIM
    p3["source.bleed_mm"] = 2.0
    p3["sheet.preset"] = "A3"
    p3["sheet.orientation"] = Orientation.LANDSCAPE
    p3["marks"] = [MarkItem(mark_type=MarkType.CROP_MARKS)]
    factory["Volantini A5 (4-up su A3)"] = {
        "name": "Volantini A5 (4-up su A3)",
        "description": "4 pose (2×2) su foglio A3 orizzontale, 100% Reale, TrimBox + 2 mm sbordatura e crocini",
        "created_at": "2026-10-28 00:00:00",
        "updated_at": "2026-10-28 00:00:00",
        "is_factory": True,
        "values": p3,
    }

    # 4. Opuscolo A4 a punto metallico (su A3)
    p4 = dict(base_defaults)
    p4["layout.layout_type"] = LayoutType.BOOKLET
    p4["layout.booklet_mode"] = BookletMode.SADDLE_STITCH
    p4["layout.scaling"] = PageScaling.ACTUAL_SIZE
    p4["sheet.preset"] = "A3"
    p4["sheet.orientation"] = Orientation.LANDSCAPE
    p4["marks"] = [
        MarkItem(mark_type=MarkType.CROP_MARKS),
        MarkItem(mark_type=MarkType.FOLDING_MARKS),
    ]
    factory["Opuscolo A4 punto metallico (su A3)"] = {
        "name": "Opuscolo A4 punto metallico (su A3)",
        "description": "Libretto A4 con rilegatura a sella/punto metallico su fogli A3 orizzontali fronte/retro con linea di piega",
        "created_at": "2026-10-28 00:00:00",
        "updated_at": "2026-10-28 00:00:00",
        "is_factory": True,
        "values": p4,
    }

    # 5. Locandina A3 (1-up a vivo)
    p5 = dict(base_defaults)
    p5["layout.layout_type"] = LayoutType.NUP
    p5["layout.nup"] = 1
    p5["layout.scaling"] = PageScaling.ACTUAL_SIZE
    p5["source.box"] = SourceBox.TRIM
    p5["source.bleed_mm"] = 3.0
    p5["sheet.preset"] = "A3"
    p5["sheet.orientation"] = Orientation.PORTRAIT
    p5["marks"] = [MarkItem(mark_type=MarkType.CROP_MARKS)]
    factory["Locandina A3 (1-up a vivo)"] = {
        "name": "Locandina A3 (1-up a vivo)",
        "description": "Stampa singola a tutta pagina su foglio A3 verticale con 3 mm di sbordatura e crocini",
        "created_at": "2026-10-28 00:00:00",
        "updated_at": "2026-10-28 00:00:00",
        "is_factory": True,
        "values": p5,
    }

    return factory


class PresetManager:
    """
    Manages the central catalog of presets stored in a single JSON file.
    """

    _cached_catalog: Optional[Dict[str, dict]] = None

    @classmethod
    def get_catalog_path(cls) -> str:
        return get_presets_file_path()

    @classmethod
    def load_catalog(cls, reload: bool = False) -> Dict[str, dict]:
        """
        Load all presets from the central presets.json file.
        Initializes with factory templates if the file does not exist.
        """
        if cls._cached_catalog is not None and not reload:
            return dict(cls._cached_catalog)

        path = cls.get_catalog_path()
        if not os.path.exists(path):
            factory = _get_factory_presets()
            cls.save_catalog(factory)
            cls._cached_catalog = factory
            return dict(factory)

        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or "presets" not in payload:
                raise ValueError("Struttura presets.json non valida")

            catalog = {}
            known_targets = set(defaults()) | {"preprocessors", "marks"}
            for name, item in payload.get("presets", {}).items():
                if not isinstance(item, dict) or "values" not in item:
                    continue
                raw_values = item["values"]
                decoded_values = {
                    k: _decode(v) for k, v in raw_values.items() if k in known_targets
                }
                catalog[name] = {
                    "name": name,
                    "description": item.get("description", ""),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                    "is_factory": item.get("is_factory", False),
                    "values": decoded_values,
                }
            if not catalog:
                catalog = _get_factory_presets()
                cls.save_catalog(catalog)
            cls._cached_catalog = catalog
            return dict(catalog)
        except Exception:
            factory = _get_factory_presets()
            cls.save_catalog(factory)
            cls._cached_catalog = factory
            return dict(factory)

    @classmethod
    def save_catalog(cls, catalog: Dict[str, dict]) -> None:
        """Save all presets into the central presets.json file."""
        cls._cached_catalog = dict(catalog)
        path = cls.get_catalog_path()
        payload = {
            "format": FORMAT_VERSION,
            "application": "Laydown",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "presets": {},
        }
        for name, item in catalog.items():
            vals = item.get("values", {})
            payload["presets"][name] = {
                "name": name,
                "description": item.get("description", ""),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "is_factory": item.get("is_factory", False),
                "values": {k: _encode(v) for k, v in vals.items()},
            }
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        if os.path.exists(path):
            os.replace(tmp_path, path)
        else:
            os.rename(tmp_path, path)

    @classmethod
    def list_names(cls) -> List[str]:
        catalog = cls.load_catalog()
        return sorted(list(catalog.keys()))

    @classmethod
    def get_preset(cls, name: str) -> Optional[dict]:
        catalog = cls.load_catalog()
        item = catalog.get(name)
        if item:
            return dict(item["values"])
        return None

    @classmethod
    def get_preset_meta(cls, name: str) -> Optional[dict]:
        catalog = cls.load_catalog()
        return catalog.get(name)

    @classmethod
    def save_preset(
        cls,
        name: str,
        values: dict,
        description: str = "",
        is_factory: bool = False,
    ) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Il nome della preimpostazione non può essere vuoto.")
        catalog = cls.load_catalog()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = catalog.get(name)
        created_at = existing.get("created_at", now) if existing else now

        catalog[name] = {
            "name": name,
            "description": description or (existing.get("description", "") if existing else ""),
            "created_at": created_at,
            "updated_at": now,
            "is_factory": is_factory,
            "values": dict(values),
        }
        cls.save_catalog(catalog)

    @classmethod
    def delete_preset(cls, name: str) -> bool:
        catalog = cls.load_catalog()
        if name in catalog:
            del catalog[name]
            cls.save_catalog(catalog)
            return True
        return False

    @classmethod
    def rename_preset(
        cls,
        old_name: str,
        new_name: str,
        new_description: Optional[str] = None,
    ) -> bool:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("Il nuovo nome non può essere vuoto.")
        catalog = cls.load_catalog()
        if old_name not in catalog:
            return False
        item = catalog.pop(old_name)
        item["name"] = new_name
        if new_description is not None:
            item["description"] = new_description
        item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        catalog[new_name] = item
        cls.save_catalog(catalog)
        return True

    @classmethod
    def import_from_file(cls, path: str) -> str:
        """Import a standalone .json preset file into the central catalog."""
        raw_vals = load_preset(path)
        base = os.path.splitext(os.path.basename(path))[0]
        base = base.replace(".laydown", "").strip()
        name = base
        catalog = cls.load_catalog()
        idx = 1
        while name in catalog:
            name = f"{base} ({idx})"
            idx += 1
        cls.save_preset(name, raw_vals, description=f"Importato da {os.path.basename(path)}")
        return name

    @classmethod
    def export_to_file(cls, name: str, path: str) -> None:
        """Export a preset from the catalog to a standalone file."""
        vals = cls.get_preset(name)
        if vals is None:
            raise ValueError(f"Preimpostazione '{name}' non trovata.")
        save_preset(path, vals)

    @classmethod
    def reset_to_factory(cls) -> None:
        """Reset the catalog to default factory presets."""
        factory = _get_factory_presets()
        cls.save_catalog(factory)
