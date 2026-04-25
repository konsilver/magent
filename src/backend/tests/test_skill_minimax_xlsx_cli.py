"""Integration tests for minimax-xlsx's xlsx_cli.py create wrapper.

The skill's original CREATE pipeline (copy-template → hand-edit-XML → pack)
does not work in the script-runner sandbox because each run_skill_script
call gets a fresh work_dir — there is no persistent fs between calls.
xlsx_cli.py collapses the pipeline into one call by accepting a workbook
JSON spec via stdin. These tests drive that script end-to-end.
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

SKILL_ROOT = (
    Path(__file__).resolve().parents[1]
    / "agent_skills"
    / "skills"
    / "minimax-xlsx"
)
XLSX_CLI = SKILL_ROOT / "scripts" / "xlsx_cli.py"
FORMULA_CHECK = SKILL_ROOT / "scripts" / "formula_check.py"

OOXML_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def financial_model_spec() -> dict:
    """Two-sheet workbook with strings, inputs, formulas, and a cross-sheet ref."""
    return {
        "sheets": [
            {
                "name": "Assumptions",
                "freeze_header": True,
                "columns": [{"width": 24}, {"width": 14}, {"width": 14}],
                "rows": [
                    {"role": "header", "cells": ["Metric", "FY2024", "FY2025"]},
                    {"cells": [
                        "Revenue Growth",
                        {"value": 0.12, "role": "input_pct"},
                        {"value": 0.15, "role": "input_pct"},
                    ]},
                    {"cells": [
                        "Gross Margin",
                        {"value": 0.45, "role": "input_pct"},
                        {"value": 0.47, "role": "input_pct"},
                    ]},
                ],
            },
            {
                "name": "Model",
                "rows": [
                    {"role": "header", "cells": ["Item", "FY2024", "FY2025"]},
                    {"cells": [
                        "Revenue",
                        {"value": 85000000, "role": "input_currency"},
                        {"formula": "B2*(1+Assumptions!C2)",
                         "role": "formula_currency"},
                    ]},
                    {"cells": [
                        "Gross Profit",
                        {"formula": "B2*Assumptions!B3",
                         "role": "formula_currency"},
                        {"formula": "C2*Assumptions!C3",
                         "role": "formula_currency"},
                    ]},
                    {"cells": [
                        "Total",
                        {"formula": "SUM(B2:B3)", "role": "formula_currency"},
                        {"formula": "'Assumptions'!B2+'Assumptions'!C2",
                         "role": "xref"},
                    ]},
                ],
            },
        ]
    }


def _run_xlsx_cli(workbook_spec: dict, output: Path) -> subprocess.CompletedProcess:
    payload = json.dumps({"workbook": workbook_spec}, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(XLSX_CLI), "create", "--output", str(output)],
        input=payload,
        text=True,
        capture_output=True,
    )


# ── happy path ──────────────────────────────────────────────────────────────

def test_create_produces_valid_xlsx(tmp_path: Path, financial_model_spec: dict):
    out = tmp_path / "model.xlsx"
    r = _run_xlsx_cli(financial_model_spec, out)

    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
    assert out.is_file() and out.stat().st_size > 0

    # Archive sanity — OOXML essentials present.
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert "[Content_Types].xml" in names
    assert "xl/workbook.xml" in names
    assert "xl/sharedStrings.xml" in names
    assert "xl/styles.xml" in names
    assert "xl/worksheets/sheet1.xml" in names
    assert "xl/worksheets/sheet2.xml" in names
    # No stray template sheet from before regeneration.
    assert "xl/worksheets/sheet3.xml" not in names


def test_formula_first_is_enforced(tmp_path: Path, financial_model_spec: dict):
    """Every cell that came from a {"formula": ...} spec must emit <f>…</f>
    with an EMPTY <v></v> — never a hardcoded cached value. This is the
    skill's #1 rule (Formula-First)."""
    out = tmp_path / "model.xlsx"
    assert _run_xlsx_cli(financial_model_spec, out).returncode == 0

    with zipfile.ZipFile(out) as z:
        sheet2 = z.read("xl/worksheets/sheet2.xml").decode("utf-8")

    root = ET.fromstring(sheet2)
    f_cells = root.findall(f".//{OOXML_MAIN_NS}c/{OOXML_MAIN_NS}f/..")
    assert len(f_cells) >= 4, "expected at least 4 formula cells in Model sheet"
    for cell in f_cells:
        v = cell.find(f"{OOXML_MAIN_NS}v")
        # Either no <v>, or an empty one — never a cached number.
        assert v is None or (v.text or "").strip() == "", (
            f"Formula-First violated at {cell.get('r')}: v={v.text!r}"
        )


def test_formula_check_passes(tmp_path: Path, financial_model_spec: dict):
    """Skill's own formula_check.py must be happy."""
    out = tmp_path / "model.xlsx"
    assert _run_xlsx_cli(financial_model_spec, out).returncode == 0

    r = subprocess.run(
        [sys.executable, str(FORMULA_CHECK), str(out), "--json"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["error_count"] == 0, report
    assert report["formula_count"] >= 4
    assert set(report["sheets_checked"]) == {"Assumptions", "Model"}


def test_openpyxl_can_read_output(tmp_path: Path, financial_model_spec: dict):
    """openpyxl (used by Excel-adjacent consumers and by xlsx_reader.py) must
    be able to load the file without tripping over XML comments in styles.xml."""
    openpyxl = pytest.importorskip("openpyxl")
    out = tmp_path / "model.xlsx"
    assert _run_xlsx_cli(financial_model_spec, out).returncode == 0

    wb = openpyxl.load_workbook(out, data_only=False)
    assert wb.sheetnames == ["Assumptions", "Model"]

    model = wb["Model"]
    # Row 2: Revenue — input in B2, formula in C2.
    assert model["B2"].value == 85000000
    assert str(model["C2"].value).startswith("=")
    # Row 4: Totals — both formula cells.
    assert str(model["B4"].value).startswith("=SUM")
    assert "Assumptions" in str(model["C4"].value)


# ── error / edge cases ──────────────────────────────────────────────────────

def test_missing_workbook_fails_with_clear_error(tmp_path: Path):
    r = subprocess.run(
        [sys.executable, str(XLSX_CLI), "create", "--output", str(tmp_path / "x.xlsx")],
        input="{}",
        text=True,
        capture_output=True,
    )
    assert r.returncode != 0
    assert "workbook" in r.stderr


def test_forbidden_sheet_name_rejected(tmp_path: Path):
    spec = {"sheets": [{"name": "bad/name", "rows": []}]}
    r = _run_xlsx_cli(spec, tmp_path / "x.xlsx")
    assert r.returncode != 0
    assert "forbidden" in r.stderr.lower() or "name" in r.stderr.lower()


def test_unknown_role_rejected(tmp_path: Path):
    spec = {
        "sheets": [{
            "name": "Sheet1",
            "rows": [{"cells": [{"value": 1, "role": "not_a_role"}]}],
        }]
    }
    r = _run_xlsx_cli(spec, tmp_path / "x.xlsx")
    assert r.returncode != 0
    assert "role" in r.stderr.lower()


def test_ampersand_in_string_is_escaped(tmp_path: Path):
    """Strings containing & / < / > must be XML-escaped in sharedStrings.xml."""
    spec = {"sheets": [{"name": "S", "rows": [{"cells": ["R&D <Budget>"]}]}]}
    out = tmp_path / "x.xlsx"
    assert _run_xlsx_cli(spec, out).returncode == 0

    with zipfile.ZipFile(out) as z:
        sst = z.read("xl/sharedStrings.xml").decode("utf-8")
    # If escaping worked, the literal `&` / `<` do not appear as-is in text nodes.
    assert "R&amp;D" in sst
    assert "&lt;Budget&gt;" in sst
    # XML parse succeeds (would fail if `&` were raw).
    ET.fromstring(sst)


def test_leading_equals_in_formula_is_stripped(tmp_path: Path):
    """Agents often write '=SUM(...)' — wrapper must silently strip the '='."""
    spec = {
        "sheets": [{
            "name": "S",
            "rows": [
                {"cells": [{"value": 10, "role": "input"}]},
                {"cells": [{"formula": "=A1*2", "role": "formula"}]},
            ],
        }]
    }
    out = tmp_path / "x.xlsx"
    assert _run_xlsx_cli(spec, out).returncode == 0

    with zipfile.ZipFile(out) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    root = ET.fromstring(sheet)
    f = root.find(f".//{OOXML_MAIN_NS}f")
    assert f is not None and f.text == "A1*2"


def test_multi_sheet_rids_sync(tmp_path: Path):
    """When writing N sheets, workbook.xml / workbook.xml.rels / [Content_Types].xml
    must all agree: rId1 for sheet1, rId2/3 for styles+sst, rId4+ for sheet2+."""
    spec = {
        "sheets": [
            {"name": f"S{i}", "rows": [{"cells": [f"value{i}"]}]}
            for i in range(1, 5)  # 4 sheets
        ]
    }
    out = tmp_path / "x.xlsx"
    assert _run_xlsx_cli(spec, out).returncode == 0

    with zipfile.ZipFile(out) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        ct = ET.fromstring(z.read("[Content_Types].xml"))

    # workbook.xml: 4 sheets with expected r:ids (rId1, rId4, rId5, rId6)
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    sheet_rids = [
        sh.get(f"{rel_ns}id") for sh in wb.findall(f"{OOXML_MAIN_NS}sheets/{OOXML_MAIN_NS}sheet")
    ]
    assert sheet_rids == ["rId1", "rId4", "rId5", "rId6"]

    # Every r:id in workbook must exist as a Relationship targeting a worksheet XML.
    rels_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    targets = {
        r.get("Id"): r.get("Target")
        for r in rels.findall(f"{rels_ns}Relationship")
    }
    for rid in sheet_rids:
        assert rid in targets
        assert targets[rid].startswith("worksheets/sheet")

    # [Content_Types].xml: one Override per sheetN.xml
    ct_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    overrides = {o.get("PartName") for o in ct.findall(f"{ct_ns}Override")}
    for i in range(1, 5):
        assert f"/xl/worksheets/sheet{i}.xml" in overrides


def test_template_xml_comments_are_stripped(tmp_path: Path):
    """The shipped template has inline `<!-- -->` docstrings which lxml (used
    by openpyxl / Excel) parses as real children and chokes on. The wrapper
    must strip them before packing."""
    spec = {"sheets": [{"name": "S", "rows": [{"cells": ["hi"]}]}]}
    out = tmp_path / "x.xlsx"
    assert _run_xlsx_cli(spec, out).returncode == 0

    with zipfile.ZipFile(out) as z:
        for name in z.namelist():
            if not (name.endswith(".xml") or name.endswith(".rels")):
                continue
            content = z.read(name).decode("utf-8", errors="replace")
            assert "<!--" not in content, f"stray comment left in {name}"


# ── EDIT subcommand ─────────────────────────────────────────────────────────

def _run_edit(input_xlsx: Path, patches: list, output: Path) -> subprocess.CompletedProcess:
    payload = json.dumps({"patches": patches}, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(XLSX_CLI), "edit",
         "--input", str(input_xlsx), "--output", str(output)],
        input=payload, text=True, capture_output=True,
    )


@pytest.fixture
def base_xlsx(tmp_path: Path) -> Path:
    """Minimal 2-sheet workbook to exercise edits against."""
    spec = {
        "sheets": [
            {
                "name": "Sales",
                "rows": [
                    {"role": "header", "cells": ["Product", "Q1 2025", "Q2 2025"]},
                    {"cells": [
                        "Widget",
                        {"value": 100, "role": "input_int"},
                        {"value": 120, "role": "input_int"},
                    ]},
                    {"cells": [
                        "Gadget",
                        {"value": 80, "role": "input_int"},
                        {"value": 95, "role": "input_int"},
                    ]},
                    {"cells": [
                        "Total",
                        {"formula": "SUM(B2:B3)", "role": "formula_int"},
                        {"formula": "SUMM(C2:C3)",  # intentionally broken
                         "role": "formula_int"},
                    ]},
                ],
            },
            {
                "name": "Summary",
                "rows": [
                    {"role": "header", "cells": ["Metric", "Value"]},
                    {"cells": [
                        "Total Sales",
                        {"formula": "Sales!B4+Sales!C4", "role": "xref"},
                    ]},
                ],
            },
        ]
    }
    out = tmp_path / "base.xlsx"
    r = subprocess.run(
        [sys.executable, str(XLSX_CLI), "create", "--output", str(out)],
        input=json.dumps({"workbook": spec}), text=True, capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    return out


def _load(path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    return openpyxl.load_workbook(path, data_only=False)


def test_edit_set_cell_value_and_formula(base_xlsx: Path, tmp_path: Path):
    out = tmp_path / "edited.xlsx"
    patches = [
        {"op": "set_cell", "sheet": "Sales", "cell": "A5",
         "value": "Gizmo", "role": "input"},
        {"op": "set_cell", "sheet": "Sales", "cell": "B5",
         "value": 60, "role": "input_int"},
        {"op": "set_cell", "sheet": "Sales", "cell": "D2",
         "formula": "B2+C2", "role": "formula_int"},
    ]
    r = _run_edit(base_xlsx, patches, out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    ws = wb["Sales"]
    assert ws["A5"].value == "Gizmo"
    assert ws["B5"].value == 60
    assert str(ws["D2"].value) == "=B2+C2"
    # Original cells untouched.
    assert ws["A2"].value == "Widget"
    assert ws["B2"].value == 100


def test_edit_fix_formula_repairs_broken_cell(base_xlsx: Path, tmp_path: Path):
    """fix_formula is the sanctioned way to repair a #NAME? etc. — it's just
    set_cell with a formula but its own op name keeps FIX tasks legible."""
    out = tmp_path / "edited.xlsx"
    patches = [{
        "op": "fix_formula", "sheet": "Sales", "cell": "C4",
        "formula": "SUM(C2:C3)", "role": "formula_int",
    }]
    r = _run_edit(base_xlsx, patches, out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    assert str(wb["Sales"]["C4"].value) == "=SUM(C2:C3)"
    # Other sheets and the SUM(B2:B3) cell MUST be unchanged.
    assert wb.sheetnames == ["Sales", "Summary"]
    assert str(wb["Sales"]["B4"].value) == "=SUM(B2:B3)"


def test_edit_replace_text_affects_shared_strings(base_xlsx: Path, tmp_path: Path):
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [
        {"op": "replace_text", "search": "2025", "replace": "2026"}
    ], out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    assert wb["Sales"]["B1"].value == "Q1 2026"
    assert wb["Sales"]["C1"].value == "Q2 2026"


def test_edit_insert_row(base_xlsx: Path, tmp_path: Path):
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [{
        "op": "insert_row", "sheet": "Sales", "at": 4,
        "text": {"A": "Doohickey"},
        "values": {"B": 50, "C": 55},
        "formulas": {"D": "B{row}+C{row}"},
        "copy_style_from": 3,
    }], out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    ws = wb["Sales"]
    assert ws["A4"].value == "Doohickey"
    assert ws["B4"].value == 50
    assert ws["C4"].value == 55
    assert str(ws["D4"].value).startswith("=")
    # Total shifts down to row 5, and its SUM range should stay valid:
    assert ws["A5"].value == "Total"


def test_edit_add_column(base_xlsx: Path, tmp_path: Path):
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [{
        "op": "add_column", "sheet": "Sales", "col": "D",
        "header": "Growth %",
        "formula": "C{row}/B{row}-1", "formula_rows": "2:3",
        "numfmt": "0.0%",
    }], out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    ws = wb["Sales"]
    assert ws["D1"].value == "Growth %"
    assert str(ws["D2"].value) == "=C2/B2-1"
    assert str(ws["D3"].value) == "=C3/B3-1"


def test_edit_rename_sheet_rewrites_cross_sheet_formula(base_xlsx: Path, tmp_path: Path):
    """Summary!B2 references Sales!B4+Sales!C4. After renaming Sales, both
    formula references must point at the new name."""
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [
        {"op": "rename_sheet", "from": "Sales", "to": "Sales 2025"}
    ], out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    assert set(wb.sheetnames) == {"Sales 2025", "Summary"}
    # Sheet name has a space → formulas must quote it.
    f = str(wb["Summary"]["B2"].value)
    assert "'Sales 2025'!B4" in f
    assert "'Sales 2025'!C4" in f


def test_edit_rename_sheet_skip_formula_update(base_xlsx: Path, tmp_path: Path):
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [{
        "op": "rename_sheet", "from": "Sales", "to": "Sales2",
        "update_formulas": False,
    }], out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    # Old reference is preserved verbatim — will break in Excel unless the
    # user chose this explicitly.
    f = str(wb["Summary"]["B2"].value)
    assert "Sales!B4" in f


def test_edit_delete_row_shifts_subsequent_formulas(base_xlsx: Path, tmp_path: Path):
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [{
        "op": "delete_row", "sheet": "Sales", "at": 3,
    }], out)
    assert r.returncode == 0, r.stderr

    wb = _load(out)
    ws = wb["Sales"]
    # Gadget (was row 3) gone; Total (was row 4) shifts up to row 3.
    assert ws["A3"].value == "Total"
    # xlsx_shift_rows collapses SUM(B2:B3) to SUM(B2:B2) when row 3 is deleted.
    assert "SUM(B2:B2)" in str(ws["B3"].value)


def test_edit_chains_all_ops_in_one_call(base_xlsx: Path, tmp_path: Path):
    """The whole point of the wrapper: a multi-patch plan runs in ONE
    run_skill_script call, with each patch seeing the prior patches'
    mutations on disk."""
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [
        {"op": "fix_formula", "sheet": "Sales", "cell": "C4",
         "formula": "SUM(C2:C3)", "role": "formula_int"},
        {"op": "set_cell", "sheet": "Sales", "cell": "A5",
         "value": "Gizmo", "role": "input"},
        {"op": "replace_text", "search": "2025", "replace": "2026"},
        {"op": "rename_sheet", "from": "Sales", "to": "Sales FY2026"},
    ], out)
    assert r.returncode == 0, r.stderr
    assert "patches_applied\": 4" in r.stdout

    wb = _load(out)
    assert "Sales FY2026" in wb.sheetnames
    ws = wb["Sales FY2026"]
    assert ws["B1"].value == "Q1 2026"
    assert str(ws["C4"].value) == "=SUM(C2:C3)"
    assert ws["A5"].value == "Gizmo"


def test_edit_missing_input_fails_clearly(tmp_path: Path):
    r = subprocess.run(
        [sys.executable, str(XLSX_CLI), "edit",
         "--input", str(tmp_path / "does-not-exist.xlsx"),
         "--output", str(tmp_path / "x.xlsx")],
        input='{"patches":[]}', text=True, capture_output=True,
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower()


def test_edit_unknown_op_rejected(base_xlsx: Path, tmp_path: Path):
    r = _run_edit(base_xlsx, [{"op": "delete_workbook"}], tmp_path / "x.xlsx")
    assert r.returncode != 0
    assert "not supported" in r.stderr.lower()


def test_edit_set_cell_preserves_other_sheets_bytewise(base_xlsx: Path, tmp_path: Path):
    """Core EDIT guarantee: only sheets you touch are modified. The unpack →
    edit one sheet → pack path must leave untouched sheet XML structurally
    equivalent (sheet names, row count, cell content)."""
    out = tmp_path / "edited.xlsx"
    r = _run_edit(base_xlsx, [{
        "op": "set_cell", "sheet": "Sales", "cell": "A99",
        "value": "Footer", "role": "default",
    }], out)
    assert r.returncode == 0, r.stderr

    before = _load(base_xlsx)["Summary"]
    after = _load(out)["Summary"]
    assert [(c.coordinate, c.value) for row in before.iter_rows() for c in row if c.value is not None] \
        == [(c.coordinate, c.value) for row in after.iter_rows() for c in row if c.value is not None]
