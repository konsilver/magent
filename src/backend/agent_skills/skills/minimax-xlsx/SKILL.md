---
name: minimax-xlsx
display_name: Excel表格处理
description: "**结构化生成/编辑/分析** Excel 时使用：需要公式交叉引用、多 sheet 模型、透视表、财务格式、公式校验/修复，或需要分析读取现有 xlsx/csv/tsv 数据、编辑已有文件、增删列/行/公式。典型场景：\"做一个带公式的三表财务模型\"\"给这份 xlsx 加一列算增长\"\"分析这份表的月度趋势\"\"校验并修复公式错误\"\"按角色染色（input/formula/header）\"。支持 READ（pandas 分析）、CREATE（xlsx_cli.py 一次性 JSON 生成，Formula-First）、EDIT（XML unpack→edit→pack）、FIX（公式修复）、VALIDATE（公式校验）五种 pipeline。\n\n⚠️ **不适用场景**：若用户只是把对话中已生成的 Markdown 表格（`| col | col |` 标准格式）直接转成 .xlsx 供下载（无需公式/多 sheet/精细格式），请改用 `export_table_to_excel` MCP 工具——更快更轻量。"
license: MIT
tags: spreadsheet,xlsx,excel,office,data-analysis
metadata:
  version: "1.0"
  category: productivity
  sources:
    - ECMA-376 Office Open XML File Formats
    - Microsoft Open XML SDK documentation
---

# MiniMax XLSX Skill

Handle the request directly. Do NOT spawn sub-agents. Always write the output file the user requests.

## Task Routing

| Task | Sandbox entrypoint | Internal guide |
|------|--------|-------|
| **READ** — analyze existing data | `xlsx_reader.py input.xlsx` (single call; input via `input_files_b64` or `"artifact:<id>"`) | `references/read-analyze.md` |
| **CREATE** — new xlsx from scratch | **`xlsx_cli.py create`** (one call, `workbook` JSON spec) | CREATE section below |
| **EDIT** — modify existing xlsx | **`xlsx_cli.py edit`** (one call, `patches` JSON spec) | EDIT section below |
| **FIX** — repair broken formulas | **`xlsx_cli.py edit`** with `op=fix_formula` patches | EDIT section below |
| **VALIDATE** — check formulas | `formula_check.py file.xlsx --json` (single call) | `references/validate.md` |

> ⚠️ **Sandbox agents (run_skill_script path)**: always use the `xlsx_cli.py` entry points above. Do NOT follow `references/create.md`, `references/edit.md`, `references/fix.md` — they describe a multi-call `cp -r templates/… → Edit tool → xlsx_pack.py` workflow that only works in a local shell with a persistent filesystem. The script-runner sandbox deletes its workdir after every call.

## CREATE — One-shot via `xlsx_cli.py create`

Use `scripts/xlsx_cli.py create` to produce a complete .xlsx in a single `run_skill_script` call. The wrapper reads a `workbook` JSON from stdin, renders the template → sharedStrings → worksheet XML → workbook.xml + rels + [Content_Types].xml, then packs. You never touch raw XML.

**Formula-First is enforced by the wrapper** — `{"formula": "SUM(B2:B9)"}` always produces `<f>...</f><v></v>`, never a hardcoded number.

### Agent call shape (sandbox)

```python
run_skill_script(
    skill_id="minimax-xlsx",
    script_name="scripts/xlsx_cli.py",
    params=json.dumps({
        "_args": ["create", "--output", "financial_model.xlsx"],
        "workbook": {
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
                            {"formula": "B2*(1+Assumptions!C2)", "role": "formula_currency"},
                        ]},
                        {"cells": [
                            "Gross Profit",
                            {"formula": "B2*Assumptions!B3", "role": "formula_currency"},
                            {"formula": "C2*Assumptions!C3", "role": "formula_currency"},
                        ]},
                    ],
                },
            ],
        },
    }, ensure_ascii=False),
)
```

### Workbook JSON schema

```
workbook.sheets[*]:
  name              str   required, ≤31 chars, no / \ ? * [ ] :
  freeze_header     bool  optional — freeze row 1 when true
  columns[*].width  num   optional — column width in Excel units
  rows[*]:
    role            str   optional — row-level default role applied to cells with no role
    height          num   optional — row height in points
    cells[*]:              (one per column, left to right)
      str | int | float | bool      shorthand: plain value, inherits row role
      { "value": ...,  "role": ... } explicit value with role
      { "formula": "SUM(B2:B9)", "role": "formula_currency" }  formulas (NEVER include leading `=`)
      null                           leave empty
```

### Role → style index (13 pre-built slots in styles.xml)

| Role | Index | Font | Number format |
|---|---|---|---|
| `default` / `text` | 0 | theme | General |
| `input` | 1 | blue | General |
| `formula` | 2 | black | General |
| `xref` (cross-sheet) | 3 | **green** | General |
| `header` | 4 | **bold** black | General |
| `input_currency` | 5 | blue | `$#,##0` |
| `formula_currency` | 6 | black | `$#,##0` |
| `input_pct` | 7 | blue | `0.0%` |
| `formula_pct` | 8 | black | `0.0%` |
| `input_int` | 9 | blue | `#,##0` |
| `formula_int` | 10 | black | `#,##0` |
| `year` | 11 | blue | plain integer (no comma) |
| `highlight` | 12 | blue on yellow | General |

Rules to remember:
- **Percentages are decimals**: 12.5% → `0.125`.
- **Formulas have no leading `=`**: write `"SUM(B2:B9)"`, not `"=SUM(B2:B9)"` (if you include `=` the wrapper silently strips it).
- **Cross-sheet refs** use `'Sheet Name'!B5` quoting when the name has spaces; use role `xref` to tag them green.
- **All column-letter math is handled by the wrapper** — cells are emitted left-to-right in the order you list them.
- The wrapper auto-counts sharedStrings, wires up rIds, and syncs `workbook.xml` / `workbook.xml.rels` / `[Content_Types].xml`. You do not touch those files.
- After success the sandbox packages `output.xlsx` as an artifact; the caller reads `artifacts[0].id`.

### Validation (optional but recommended)

```python
run_skill_script(
    skill_id="minimax-xlsx",
    script_name="scripts/formula_check.py",
    params=json.dumps({"_args": ["financial_model.xlsx", "--json"]}),
)
```

### Internal reference (maintainers only)

`references/create.md`, `references/edit.md`, `references/fix.md`, `references/format.md` describe the manual XML editing flow that `xlsx_cli.py` implements internally. **Sandbox agents should NOT read or follow those files** — they assume a persistent shell filesystem (`cp -r /tmp/xlsx_work/ …`) which the script-runner sandbox does not provide. They are preserved only so humans maintaining `xlsx_cli.py` can understand what the wrapper is doing under the hood.

## EDIT / FIX — One-shot via `xlsx_cli.py edit`

EDIT and FIX both use the same wrapper: send a list of `patches` over stdin; the wrapper unpacks the input xlsx, applies every patch in order (sharing on-disk state), and repacks. **All ops run inside one `run_skill_script` call** — no multi-step orchestration needed.

**Integrity guarantees:**
- Never touches a sheet you don't patch; other sheets' XML is packed back byte-for-byte.
- Shared strings are preserved by index; new strings are appended, existing indices never shift.
- Every calculated cell remains a `<f>` formula (Formula-First rule holds for EDIT too).
- `openpyxl` is never used for round-trip — the underlying `xlsx_unpack.py`/`xlsx_pack.py` path preserves VBA, pivots, sparklines, etc.

### Agent call shape (sandbox)

```python
run_skill_script(
    skill_id="minimax-xlsx",
    script_name="scripts/xlsx_cli.py",
    params=json.dumps({
        "_args": ["edit", "--input", "in.xlsx", "--output", "out.xlsx"],
        "patches": [
            {"op": "fix_formula", "sheet": "Sales", "cell": "C4",
             "formula": "SUM(C2:C3)", "role": "formula_int"},
            {"op": "set_cell", "sheet": "Sales", "cell": "A5",
             "value": "新增行", "role": "input"},
            {"op": "replace_text", "search": "2025年度", "replace": "2026年度"},
            {"op": "insert_row", "sheet": "Budget", "at": 5,
             "text": {"A": "Utilities"},
             "values": {"B": 3000, "C": 3000, "D": 3500, "E": 3500},
             "formulas": {"F": "SUM(B{row}:E{row})"},
             "copy_style_from": 4},
            {"op": "add_column", "sheet": "Sales", "col": "G",
             "header": "% of Total", "formula": "F{row}/$F$10",
             "formula_rows": "2:9", "total_row": 10,
             "total_formula": "SUM(G2:G9)", "numfmt": "0.0%"},
            {"op": "rename_sheet", "from": "Sales", "to": "Sales FY2026"},
            {"op": "delete_row", "sheet": "Sales FY2026", "at": 7}
        ],
    }, ensure_ascii=False),
    input_files=json.dumps({"in.xlsx": "artifact:<source xlsx id>"}),
)
```

### Patch operation reference

| `op` | Required fields | Optional fields | What it does |
|---|---|---|---|
| `set_cell` | `sheet`, `cell` (e.g. `"B3"`), one of `value` / `formula` | `role` (see CREATE role table) | Set value or formula at one cell. Creates the row/cell if missing. |
| `fix_formula` | same as `set_cell` + `formula` | `role` | Alias of `set_cell` with required formula — use this to repair broken `<f>` nodes so FIX tasks read naturally. |
| `replace_text` | `search`, `replace` | `sheet` (limit inline-string scope) | Global find/replace in sharedStrings + optional one-sheet inline strings. Never touches formulas. |
| `insert_row` | `at` (row number) | `sheet`, `text{col:str}`, `values{col:num}`, `formulas{col:str with {row} placeholder}`, `copy_style_from` | Shift rows down, insert a new row, auto-update `<f>` refs. Wraps `xlsx_insert_row.py`. |
| `add_column` | `col` (e.g. `"G"`) | `sheet`, `header`, `formula` (with `{row}`), `formula_rows` (e.g. `"2:9"`), `total_row`, `total_formula`, `numfmt`, `border_row`, `border_style` | Add a new column with optional formulas + total + borders. Wraps `xlsx_add_column.py`. |
| `rename_sheet` | `from`, `to` | `update_formulas` (default `true`) | Rename a sheet; by default also rewrites every `SheetName!` / `'Sheet Name'!` reference in all formulas, quoting the new name if needed. |
| `delete_row` | `at` (row number) | `sheet` | Delete the row and shift all subsequent `<row>` / `<c>` / `<f>` references up by 1. |

### Rules that apply to all patches

- **Formulas have no leading `=`** — write `"SUM(B2:B9)"`, not `"=SUM(B2:B9)"` (stripped silently if present).
- **Sheet defaults to the first sheet** when `sheet` is omitted (useful for single-sheet workbooks).
- **Role is optional on EDIT** — when omitted, the cell keeps its existing `s="N"` style. Only set `role` when you actually want to change the style.
- **Patches run in order** — later patches see the mutations from earlier ones (e.g., `insert_row` shifts rows before the next `set_cell` runs, `rename_sheet` must come before any patch that targets the new name).
- **Binary input files** (`in.xlsx`) must be delivered via `input_files={"in.xlsx": "artifact:<id>"}` or `input_files_b64`. The sandbox drops them into the working directory before execution.

### Validation (recommended after EDIT / FIX)

```python
run_skill_script(
    skill_id="minimax-xlsx",
    script_name="scripts/formula_check.py",
    params=json.dumps({"_args": ["out.xlsx", "--json"]}),
    input_files=json.dumps({"out.xlsx": "artifact:<edited xlsx id>"}),
)
```

## READ — Analyze existing data

`xlsx_reader.py` is a read-only tool that does the analysis in a single sandbox call.

```python
run_skill_script(
    skill_id="minimax-xlsx",
    script_name="scripts/xlsx_reader.py",
    params=json.dumps({"_args": ["input.xlsx", "--json"]}),
    input_files=json.dumps({"input.xlsx": "artifact:<source xlsx id>"}),
)
# --sheet "Sales"      → analyze one sheet
# --quality            → data-quality audit
```

For custom pandas analysis, read `references/read-analyze.md`. Never modify the source file.

**Formatting rule**: When the user specifies decimal places (e.g. "2 decimal places"), apply that format to ALL numeric values — use `f'{v:.2f}'` on every number. Never output `12875` when `12875.00` is required.

**Aggregation rule**: Always compute sums/means/counts directly from the DataFrame column — e.g. `df['Revenue'].sum()`. Never re-derive column values before aggregation.

## VALIDATE — Check formulas

```python
run_skill_script(
    skill_id="minimax-xlsx",
    script_name="scripts/formula_check.py",
    params=json.dumps({"_args": ["file.xlsx", "--json"]}),
    input_files=json.dumps({"file.xlsx": "artifact:<xlsx id>"}),
)
```

Exit code `0` and `error_count: 0` in the JSON output = safe to deliver. See `references/validate.md` for the full error taxonomy (`#DIV/0!`, `#NAME?`, `#REF!`, …).

## Financial Color Standard

| Cell Role | Font Color | Hex Code |
|-----------|-----------|----------|
| Hard-coded input / assumption | Blue | `0000FF` |
| Formula / computed result | Black | `000000` |
| Cross-sheet reference formula | Green | `00B050` |

## Key Rules

1. **Formula-First**: Every calculated cell MUST use an Excel formula, not a hardcoded number
2. **CREATE → `xlsx_cli.py create`** (one call, `workbook` JSON) — see CREATE section
3. **EDIT / FIX → `xlsx_cli.py edit`** (one call, `patches` JSON) — see EDIT section
4. **Never openpyxl round-trip** on existing files — `xlsx_cli.py edit` routes through unpack/pack to preserve VBA / pivots / sparklines
5. **Always produce the output file** — this is the #1 priority
6. **Validate before delivery**: `formula_check.py` exit code 0 = safe

## Utility Scripts

**Sandbox entry points (use these):**

```bash
python3 SKILL_DIR/scripts/xlsx_cli.py create --output out.xlsx      # CREATE — one-shot from workbook JSON (stdin)
python3 SKILL_DIR/scripts/xlsx_cli.py edit --input in.xlsx --output out.xlsx
                                                                    # EDIT / FIX — one-shot from patches JSON (stdin)
python3 SKILL_DIR/scripts/xlsx_reader.py input.xlsx [--json|--quality|--sheet NAME]
                                                                    # READ — structure discovery / analysis
python3 SKILL_DIR/scripts/formula_check.py file.xlsx --json         # VALIDATE — static formula check
```

**Low-level building blocks (for maintainers — `xlsx_cli.py edit` composes these internally):**

```bash
python3 SKILL_DIR/scripts/xlsx_unpack.py in.xlsx /tmp/work/         # unpack for XML editing
python3 SKILL_DIR/scripts/xlsx_pack.py /tmp/work/ out.xlsx          # repack after editing
python3 SKILL_DIR/scripts/xlsx_shift_rows.py /tmp/work/ insert 5 1  # shift rows for insertion
python3 SKILL_DIR/scripts/xlsx_add_column.py /tmp/work/ --col G ... # add column with formulas
python3 SKILL_DIR/scripts/xlsx_insert_row.py /tmp/work/ --at 6 ...  # insert row with data
python3 SKILL_DIR/scripts/formula_check.py file.xlsx --report       # standardized report
```
