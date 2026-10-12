"""Tests for the headless CLI (laydown impose / laydown preflight)."""

import json
import os
import subprocess
import sys

import fitz
import pytest

from laydown.__main__ import main as main_entry
from laydown.cli import build_project, coerce_setting, main as cli_main
from laydown.engine.data_model import Orientation
from laydown.ui.presets import save_preset
from laydown.ui.schema import defaults


class TestCLIImpose:
    def test_impose_8_page_nup_4(self, source_pdf, out_path, capsys):
        """1. impose an 8-page source with --set layout.nup=4 -> exit 0, output exists, sheet count correct."""
        src = source_pdf(n_pages=8)
        out = out_path("imposed_4up.pdf")
        rc = main_entry(["impose", src, "-o", out, "--set", "layout.nup=4"])
        assert rc == 0
        assert os.path.exists(out)
        with fitz.open(out) as doc:
            assert len(doc) == 2
        captured = capsys.readouterr()
        assert "Wrote 2 sheet(s)" in captured.out
        assert out in captured.out

    def test_out_equals_src_refused(self, source_pdf, capsys):
        """6. OUT == SRC -> exit 1 and the source file is byte-identical."""
        src = source_pdf(n_pages=4)
        with open(src, "rb") as f:
            before_bytes = f.read()

        rc = main_entry(["impose", src, "-o", src])
        assert rc == 1
        err = capsys.readouterr().err
        assert "identical" in err.lower() or "same" in err.lower()

        with open(src, "rb") as f:
            after_bytes = f.read()
        assert before_bytes == after_bytes

    def test_preflight_error_blocks_impose(self, source_pdf, out_path, capsys):
        """4. A job with a preflight ERROR -> impose exit 2 and no output file."""
        src = source_pdf(n_pages=4)
        out = out_path("blocked.pdf")
        rc = main_entry([
            "impose", src, "-o", out,
            "--set", "sheet.margin_top_mm=300.0",
            "--set", "sheet.margin_bottom_mm=300.0",
        ])
        assert rc == 2
        assert not os.path.exists(out)
        captured = capsys.readouterr()
        assert "Error:" in (captured.out + captured.err)

    def test_force_attempts_imposition_despite_preflight_error(self, source_pdf, out_path, capsys):
        """4. With --force, imposition is attempted (exit 1 is acceptable if engine refuses)."""
        src = source_pdf(n_pages=4)
        out = out_path("forced.pdf")
        rc = main_entry([
            "impose", src, "-o", out,
            "--set", "sheet.margin_top_mm=300.0",
            "--set", "sheet.margin_bottom_mm=300.0",
            "--force",
        ])
        assert rc in (0, 1)
        assert not os.path.exists(out)


class TestCLIPresetAndSet:
    def test_preset_and_set_produce_identical_projects(self, source_pdf, tmp_path):
        """2. A preset file and the equivalent --set produce identical Projects."""
        src = source_pdf(n_pages=4)
        preset_path = str(tmp_path / "preset.json")
        preset_dict = {
            "layout.nup": 4,
            "sheet.orientation": Orientation.PORTRAIT,
            "sheet.preset": "A4",
            "layout.auto_rotate": True,
        }
        save_preset(preset_path, preset_dict)

        proj_preset = build_project(src, preset_path=preset_path)
        proj_set = build_project(
            src,
            set_args=[
                "layout.nup=4",
                "sheet.orientation=PORTRAIT",
                "sheet.preset=A4",
                "layout.auto_rotate=true",
            ],
        )
        assert proj_preset == proj_set

    def test_unknown_set_target(self, source_pdf, out_path, capsys):
        """3. Unknown --set target -> exit 1."""
        src = source_pdf(n_pages=4)
        out = out_path("out.pdf")
        rc = main_entry(["impose", src, "-o", out, "--set", "nonexistent.target=123"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "nonexistent.target" in err

    def test_bad_enum_value(self, source_pdf, out_path, capsys):
        """3. Bad enum value -> exit 1."""
        src = source_pdf(n_pages=4)
        out = out_path("out.pdf")
        rc = main_entry(["impose", src, "-o", out, "--set", "sheet.orientation=DIAGONAL"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "sheet.orientation" in err
        assert "Allowed values" in err

    def test_missing_source(self, out_path, capsys):
        """3. Missing source -> exit 1."""
        out = out_path("out.pdf")
        rc = main_entry(["impose", "nonexistent_source_file.pdf", "-o", out])
        assert rc == 1
        err = capsys.readouterr().err
        assert "nonexistent_source_file.pdf" in err

    def test_bad_set_format(self, source_pdf, out_path, capsys):
        """Bad --set format without = -> exit 1."""
        src = source_pdf(n_pages=4)
        out = out_path("out.pdf")
        rc = main_entry(["impose", src, "-o", out, "--set", "layout.nup"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Invalid --set format" in err

    def test_coercion_types(self):
        """Test coerce_setting directly for bool, int, float, Enum, str."""
        d = defaults()
        # bool
        assert coerce_setting("layout.auto_rotate", "yes", d) is True
        assert coerce_setting("layout.auto_rotate", "1", d) is True
        assert coerce_setting("layout.auto_rotate", "true", d) is True
        assert coerce_setting("layout.auto_rotate", "no", d) is False
        assert coerce_setting("layout.auto_rotate", "0", d) is False
        assert coerce_setting("layout.auto_rotate", "false", d) is False
        with pytest.raises(ValueError, match="Allowed values"):
            coerce_setting("layout.auto_rotate", "maybe", d)

        # int
        assert coerce_setting("layout.nup", "4", d) == 4
        with pytest.raises(ValueError, match="Expected an integer"):
            coerce_setting("layout.nup", "invalid_int", d)

        # float
        assert coerce_setting("source.bleed_mm", "3.5", d) == 3.5
        with pytest.raises(ValueError, match="Expected a float"):
            coerce_setting("source.bleed_mm", "invalid_float", d)

        # enum
        assert coerce_setting("sheet.orientation", "portrait", d) is Orientation.PORTRAIT
        assert coerce_setting("sheet.orientation", "Portrait", d) is Orientation.PORTRAIT


class TestCLIPreflight:
    def test_preflight_json(self, source_pdf, capsys):
        """5. preflight --json output parses as JSON with the three keys."""
        src = source_pdf(n_pages=4)
        rc = main_entry(["preflight", src, "--json"])
        assert rc in (0, 2)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        for item in data:
            assert "severity" in item
            assert "message" in item
            assert "detail" in item

    def test_preflight_human_readable(self, source_pdf, capsys):
        """preflight without --json prints human-readable findings."""
        src = source_pdf(n_pages=4)
        rc = main_entry(["preflight", src])
        assert rc in (0, 2)
        captured = capsys.readouterr()
        # If there are findings, at least one severity name or message appears
        if captured.out.strip():
            assert any(s in captured.out for s in ("Note:", "Warning:", "Error:"))

    def test_preflight_exit_2_on_error(self, source_pdf, capsys):
        """preflight exits 2 if any ERROR is found."""
        src = source_pdf(n_pages=4)
        rc = main_entry([
            "preflight", src,
            "--set", "sheet.margin_top_mm=300.0",
            "--set", "sheet.margin_bottom_mm=300.0",
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "Error: Margins/gutters too large" in captured.out


class TestQtIsolationAndSmoke:
    def test_preflight_subprocess_never_loads_qt(self, source_pdf):
        """7. In a subprocess: confirm that PyQt6 was never imported."""
        src = source_pdf(n_pages=2)
        code = (
            "import sys, runpy; "
            f"sys.argv = ['laydown', 'preflight', {src!r}]; "
            "runpy.run_module('laydown', run_name='__main__'); "
            "has_qt = any(m.startswith('PyQt6') for m in sys.modules); "
            "sys.exit(1 if has_qt else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"PyQt6 was imported! Stderr: {result.stderr}"

    def test_smoke_still_works(self):
        """8. python -m laydown --smoke still exits 0."""
        rc = main_entry(["--smoke"])
        assert rc == 0
