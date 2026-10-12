"""
Headless command-line interface for Laydown.

Supports imposing and preflighting PDFs without loading Qt.
"""

import argparse
import copy
import json
import os
import sys
import tempfile
import traceback
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

import fitz

from laydown.engine.capabilities import assign
from laydown.engine.data_model import Project
from laydown.engine.impose import impose
from laydown.engine.preflight import Finding, Severity, preflight
from laydown.ui.presets import load_preset
from laydown.ui.schema import defaults


class CLIUsageError(Exception):
    """Raised for command-line syntax and usage errors."""
    pass


class CLIArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises CLIUsageError on bad options rather than sys.exit(2)."""

    def error(self, message: str) -> None:
        raise CLIUsageError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)


def format_finding_human(finding: Finding) -> str:
    """Format a single finding for human-readable output."""
    line = f"{finding.severity.value}: {finding.message}"
    if finding.detail:
        line += f"\n  {finding.detail}"
    return line


def coerce_setting(target: str, raw_value: str, defaults_map: Dict[str, Any]) -> Any:
    """
    Coerce raw_value according to the target's default type.

    Allowed types in schema defaults: bool, int, float, str, Enum.
    """
    if target not in defaults_map:
        raise ValueError(f"Unknown setting target: {target!r}")

    default_val = defaults_map[target]

    if isinstance(default_val, bool):
        val = raw_value.strip().lower()
        if val in ("true", "1", "yes"):
            return True
        elif val in ("false", "0", "no"):
            return False
        raise ValueError(
            f"Invalid value for {target!r}: {raw_value!r}. "
            "Allowed values: true, false, 1, 0, yes, no"
        )
    elif isinstance(default_val, int):
        try:
            return int(raw_value.strip())
        except ValueError:
            raise ValueError(
                f"Invalid value for {target!r}: {raw_value!r}. Expected an integer."
            )
    elif isinstance(default_val, float):
        try:
            return float(raw_value.strip())
        except ValueError:
            raise ValueError(
                f"Invalid value for {target!r}: {raw_value!r}. Expected a float."
            )
    elif isinstance(default_val, Enum):
        enum_cls = type(default_val)
        val_clean = raw_value.strip()
        val_lower = val_clean.lower()
        # Case-insensitive member name match
        for member in enum_cls:
            if member.name.lower() == val_lower:
                return member
        # Case-insensitive or exact value match
        for member in enum_cls:
            if str(member.value).lower() == val_lower:
                return member
        allowed = [m.name for m in enum_cls] + [str(m.value) for m in enum_cls]
        seen = set()
        allowed_unique = [x for x in allowed if not (x in seen or seen.add(x))]
        raise ValueError(
            f"Invalid value for {target!r}: {raw_value!r}. "
            f"Allowed values: {', '.join(allowed_unique)}"
        )
    elif isinstance(default_val, str):
        return raw_value

    return raw_value


def build_project(
    src_path: str,
    preset_path: Optional[str] = None,
    set_args: Optional[List[str]] = None,
) -> Project:
    """Build a Project instance from defaults, optional preset, and --set overrides."""
    values = defaults()
    values["preprocessors"] = []
    values["marks"] = []

    if preset_path:
        preset_values = load_preset(preset_path)
        values.update(preset_values)

    if set_args:
        defaults_map = defaults()
        for item in set_args:
            if "=" not in item:
                raise ValueError(f"Invalid --set format {item!r}: expected TARGET=VALUE")
            target, raw_value = item.split("=", 1)
            target = target.strip()
            coerced = coerce_setting(target, raw_value, defaults_map)
            values[target] = coerced

    project = Project()
    for target, value in values.items():
        assign(project, target, copy.deepcopy(value))
    project.source_pdf_path = src_path
    return project


def _build_parser() -> CLIArgumentParser:
    parser = CLIArgumentParser(
        prog="laydown",
        description="Laydown — Headless PDF imposition and preflight.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # laydown impose SRC.pdf -o OUT.pdf [--preset P.json] [--set TARGET=VALUE ...] [--force]
    impose_p = subparsers.add_parser("impose", help="Impose a PDF document")
    impose_p.add_argument("src", help="Source PDF file")
    impose_p.add_argument("-o", "--output", required=True, help="Output PDF file")
    impose_p.add_argument("--preset", help="Path to preset JSON file")
    impose_p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="TARGET=VALUE",
        help="Override a setting target with a value",
    )
    impose_p.add_argument(
        "--force",
        action="store_true",
        help="Attempt imposition even if preflight finds errors",
    )

    # laydown preflight SRC.pdf [--preset P.json] [--set TARGET=VALUE ...] [--json]
    pf_p = subparsers.add_parser("preflight", help="Preflight a PDF document")
    pf_p.add_argument("src", help="Source PDF file")
    pf_p.add_argument("--preset", help="Path to preset JSON file")
    pf_p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="TARGET=VALUE",
        help="Override a setting target with a value",
    )
    pf_p.add_argument(
        "--json",
        action="store_true",
        help="Output findings as a JSON list",
    )

    return parser


def run_impose(args: argparse.Namespace) -> int:
    src_path = args.src
    out_path = args.output

    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Source file not found: {src_path}")

    # Refuse OUT == SRC (compare os.path.realpath + normcase)
    src_real = os.path.normcase(os.path.realpath(src_path))
    out_real = os.path.normcase(os.path.realpath(out_path))
    if src_real == out_real:
        raise ValueError(f"Output path cannot be identical to source path: {out_path}")

    project = build_project(src_path, preset_path=args.preset, set_args=args.set)

    # Preflight check
    try:
        with fitz.open(src_path) as doc:
            findings = preflight(project, doc)
    except Exception as exc:
        raise RuntimeError(f"Failed to read source PDF {src_path}: {exc}") from exc

    has_error = any(f.severity == Severity.ERROR for f in findings)
    if has_error and not args.force:
        for f in findings:
            print(format_finding_human(f))
        return 2

    # Imposition with atomic write
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".laydown_tmp_", suffix=".pdf", dir=out_dir)
    os.close(fd)

    try:
        sheets = impose(project, tmp_path)
        os.replace(tmp_path, out_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

    print(f"Wrote {sheets} sheet(s) to {out_path}")
    return 0


def run_preflight(args: argparse.Namespace) -> int:
    src_path = args.src

    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Source file not found: {src_path}")

    project = build_project(src_path, preset_path=args.preset, set_args=args.set)

    try:
        with fitz.open(src_path) as doc:
            findings = preflight(project, doc)
    except Exception as exc:
        raise RuntimeError(f"Failed to read source PDF {src_path}: {exc}") from exc

    if args.json:
        payload = [
            {
                "severity": f.severity.value,
                "message": f.message,
                "detail": f.detail,
            }
            for f in findings
        ]
        print(json.dumps(payload, indent=2))
    else:
        for f in findings:
            print(format_finding_human(f))

    has_error = any(f.severity == Severity.ERROR for f in findings)
    return 2 if has_error else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if args.subcommand == "impose":
            return run_impose(args)
        elif args.subcommand == "preflight":
            return run_preflight(args)
        else:
            sys.stderr.write(f"error: unknown subcommand: {args.subcommand}\n")
            return 1
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except (CLIUsageError, FileNotFoundError, ValueError, RuntimeError) as exc:
        if os.environ.get("LAYDOWN_DEBUG") == "1":
            traceback.print_exc()
        else:
            sys.stderr.write(f"error: {exc}\n")
        return 1
    except Exception as exc:
        if os.environ.get("LAYDOWN_DEBUG") == "1":
            traceback.print_exc()
        else:
            sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
