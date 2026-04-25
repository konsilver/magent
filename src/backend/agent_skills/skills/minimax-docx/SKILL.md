---
name: minimax-docx
display_name: Word文档编辑生成
description: "**结构化生成/编辑** Word 文档时使用：需要自定义样式、多节布局、页眉页脚、目录、图片排版，或需要编辑已有 .docx、套用模板、填充占位符。典型场景：\"起草一份合同\"\"按这个模板套我的内容\"\"在这份公文里填甲乙方信息\"\"生成 10 页带封面目录的项目建议书\"\"给已有文档加页眉页脚\"。支持 CREATE（从零新建）、FILL-EDIT（编辑已有内容）、FORMAT-APPLY（套模板样式）三种 pipeline。\n\n⚠️ **不适用场景**：若用户只是把对话中已生成的 Markdown 文本一键导出为 .docx 下载（无需自定义结构/样式/编辑），请改用 `export_report_to_docx` MCP 工具——更快更轻量，且自带方正公文字体。"
version: 1.0.0
tags: document-processing,docx,word,office
---

# minimax-docx

Create, edit, and format DOCX documents via CLI tools or direct C# scripts built on OpenXML SDK (.NET).

## Setup

**First time:** `bash scripts/setup.sh` (or `powershell scripts/setup.ps1` on Windows, `--minimal` to skip optional deps).

**First operation in session:** `scripts/env_check.sh` — do not proceed if `NOT READY`. (Skip on subsequent operations within the same session.)

## Quick Start: Direct C# Path

When the task requires structural document manipulation (custom styles, complex tables, multi-section layouts, headers/footers, TOC, images), write C# directly instead of wrestling with CLI limitations. Use this scaffold:

```csharp
// File: scripts/dotnet/task.csx  (or a new .cs in a Console project)
// dotnet run --project scripts/dotnet/MiniMaxAIDocx.Cli -- run-script task.csx
#r "nuget: DocumentFormat.OpenXml, 3.2.0"

using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

using var doc = WordprocessingDocument.Create("output.docx", WordprocessingDocumentType.Document);
var mainPart = doc.AddMainDocumentPart();
mainPart.Document = new Document(new Body());

// --- Your logic here ---
// Read the relevant Samples/*.cs file FIRST for tested patterns.
// See Samples/ table in References section below.
```

**Before writing any C#, read the relevant `Samples/*.cs` file** — they contain compilable, SDK-version-verified patterns. The Samples table in the References section below maps topics to files.

## CLI shorthand

All CLI commands below use `$CLI` as shorthand for:
```bash
dotnet run --project scripts/dotnet/MiniMaxAIDocx.Cli --
```

## Pipeline routing

Route by checking: does the user have an input .docx file?

```
User task
├─ No input file → Pipeline A: CREATE
│   signals: "write", "create", "draft", "generate", "new", "make a report/proposal/memo"
│   → Read references/scenario_a_create.md
│
└─ Has input .docx
    ├─ Replace/fill/modify content → Pipeline B: FILL-EDIT
    │   signals: "fill in", "replace", "update", "change text", "add section", "edit"
    │   → Read references/scenario_b_edit_content.md
    │
    └─ Reformat/apply style/template → Pipeline C: FORMAT-APPLY
        signals: "reformat", "apply template", "restyle", "match this format", "套模板", "排版"
        ├─ Template is pure style (no content) → C-1: OVERLAY (apply styles to source)
        └─ Template has structure (cover/TOC/example sections) → C-2: BASE-REPLACE
            (use template as base, replace example content with user content)
        → Read references/scenario_c_apply_template.md
```

If the request spans multiple pipelines, run them sequentially (e.g., Create then Format-Apply).

## Pre-processing

Convert `.doc` → `.docx` if needed: `scripts/doc_to_docx.sh input.doc output_dir/`

Preview before editing (avoids reading raw XML): `scripts/docx_preview.sh document.docx`

Analyze structure for editing scenarios: `$CLI analyze --input document.docx`

## Scenario A: Create

Read `references/scenario_a_create.md`, `references/typography_guide.md`, and `references/design_principles.md` first. Pick an aesthetic recipe from `Samples/AestheticRecipeSamples.cs` that matches the document type — do not invent formatting values. For CJK, also read `references/cjk_typography.md`.

**Choose your path:**
- **Simple** (plain text, minimal formatting): use CLI — `$CLI create --type report --output out.docx --config content.json`
- **Structural** (custom styles, multi-section, TOC, images, complex tables): write C# directly. Read the relevant `Samples/*.cs` first.

CLI options: `--type` (report|letter|memo|academic), `--title`, `--author`, `--page-size` (letter|a4|legal|a3), `--margins` (standard|narrow|wide), `--header`, `--footer`, `--page-numbers`, `--toc`, `--content-json`.

### 通过 `run_skill_script` 调用（沙盒执行）

**必须同时传 `_args` 和 `content` 两个参数**，否则生成的 docx 只有标题、没有正文。

`params` 是 JSON 对象（字符串形式）：
```json
{
  "_args": ["create", "--output", "out.docx", "--title", "报告标题", "--page-size", "a4"],
  "content": {
    "sections": [
      {"heading": "第一章 引言", "level": 1,
       "paragraphs": ["第一段正文……", "第二段正文……"]},
      {"heading": "第二章 数据", "level": 1,
       "paragraphs": ["这是第二章正文。"],
       "table": {"headers": ["指标", "值"], "rows": [["A", "1"], ["B", "2"]]}},
      {"heading": "小节", "level": 2,
       "items": ["要点一", "要点二"], "list_style": "bullet"}
    ]
  }
}
```

关键规则：
- `_args` 是命令行参数数组。**不要**把 `--content-json` 写进 `_args`，wrapper 会自动从 `content` 生成临时文件并注入。
- `content.sections[]` 里每个 section 支持的字段：`heading` (str), `level` (1–6), `paragraphs` (str[]), `table` ({headers, rows}), `items` (str[]), `list_style` ("bullet"|"numbered"), `sections` (嵌套)。
- 每个 section **至少要有 `paragraphs` 或 `items`**，否则该节只会渲染一个标题，没有正文。

完整示例（JSON 字符串单行传入 `params` 字段）：
```
params = '{"_args":["create","--output","out.docx","--title","Q3 报告","--page-size","a4","--toc"],"content":{"sections":[{"heading":"摘要","level":1,"paragraphs":["本报告总结 Q3 的关键进展……"]},{"heading":"收入","level":1,"paragraphs":["Q3 收入同比增长 18%……"],"table":{"headers":["季度","收入"],"rows":[["Q1","100"],["Q2","120"],["Q3","142"]]}}]}}'
```

Then run the **validation pipeline** (below).

## Scenario B: Edit / Fill

Read `references/scenario_b_edit_content.md` first. Preview → analyze → edit → validate.

**Choose your path:**
- **Simple** (text replacement, placeholder fill): use CLI subcommands.
- **Structural** (add/reorganize sections, modify styles, manipulate tables, insert images): write C# directly. Read `references/openxml_element_order.md` and the relevant `Samples/*.cs`.

Available CLI edit subcommands (all nested under `$CLI edit <sub>`):

| Subcommand | Required flags | Optional flags |
|---|---|---|
| `replace-text`      | `--input` `--search` `--replace`       | `--output`, `--regex` |
| `fill-table`        | `--input` `--csv`                       | `--output`, `--table-index` (default 0), `--append` |
| `insert-paragraph`  | `--input` `--text`                      | `--output`, `--style` (e.g. `Heading1`), `--after-paragraph` |
| `update-field`      | `--input` `--field` `--value`           | `--output` |
| `list-placeholders` | `--input`                               | `--pattern` (default `\{\{(\w+)\}\}`) |
| `fill-placeholders` | `--input` `--mapping`                   | `--output`, `--pattern` |

```bash
$CLI edit replace-text      --input in.docx --output out.docx --search "OLD" --replace "NEW"
$CLI edit fill-placeholders --input in.docx --output out.docx --mapping mapping.json
$CLI edit fill-table        --input in.docx --output out.docx --csv data.csv --table-index 0
```

`--mapping` and `--content-json` both accept **either a file path or an inline JSON string**. `--csv` expects a file path only.

### 通过 `run_skill_script` 调用（沙盒执行）

沙盒里没有持久文件系统，输入的 docx / csv / mapping **必须通过 `input_files`（文本）或 `input_files_b64`（二进制）投放**。因为 docx 是二进制 zip，所以它**必须走 artifact 引用**（形如 `"artifact:<id>"`）——agent 先用上传 / 生成产物获得 artifact id，再在 `input_files` 里用 `"in.docx": "artifact:abc123..."` 引用。

#### Fill-placeholders（最常见）

```python
run_skill_script(
    skill_id="minimax-docx",
    script_name="scripts/docx_cli.sh",
    params=json.dumps({
        "_args": [
            "edit", "fill-placeholders",          # ← 两级子命令（edit + fill-placeholders）
            "--input", "in.docx",
            "--output", "out.docx",
            "--mapping", '{"name":"张三","date":"2026-04-14","amount":"¥12,500"}',
        ]
    }, ensure_ascii=False),
    input_files=json.dumps({
        "in.docx": "artifact:<源 docx 的 artifact id>",   # 沙盒会解析成二进制文件放到工作目录
    }, ensure_ascii=False),
)
```

要点：
- `_args` 里前两个位置参数固定是 `"edit"` + 具体子命令（`fill-placeholders` / `replace-text` / ...）
- `--mapping` 值是**内联 JSON 字符串**（C# 会先尝试文件路径，失败再按内联 JSON 解析）。短数据推荐内联，省一次 `input_files` 传参
- 长数据或 CSV：用 `input_files`/`input_files_b64` 投放文件，`--mapping mapping.json` 引用

#### Replace-text

```python
params = json.dumps({
    "_args": ["edit", "replace-text",
              "--input", "in.docx", "--output", "out.docx",
              "--search", "2025年度", "--replace", "2026年度"],
}, ensure_ascii=False)
```

正则替换加 `--regex`（boolean flag，无值）：`_args: [..., "--search", "\\d{4}年", "--replace", "2026年", "--regex"]`

#### Fill-table（CSV 数据）

```python
run_skill_script(
    skill_id="minimax-docx",
    script_name="scripts/docx_cli.sh",
    params=json.dumps({
        "_args": ["edit", "fill-table",
                  "--input", "in.docx", "--output", "out.docx",
                  "--csv", "rows.csv", "--table-index", "0"],
    }, ensure_ascii=False),
    input_files=json.dumps({
        "in.docx": "artifact:<docx id>",
        "rows.csv": "date,region,value\n2026-01,A区,100\n2026-02,A区,102\n",   # 直接文本内容
    }, ensure_ascii=False),
)
```

#### 输出获取

沙盒执行完会扫描工作目录里新生成的白名单文件（含 `.docx`）并打包为 artifact，backend 再把 `stored_refs` 塞回 `ToolResponse`。调用方从返回里读 `artifacts[0].id` / `.url` 拿到产物。

Then run the **validation pipeline**. Also run diff to verify minimal changes:
```bash
$CLI diff --before in.docx --after out.docx
```

## Scenario C: Apply Template

Read `references/scenario_c_apply_template.md` first. Preview and analyze both source and template.

```bash
$CLI apply-template --input source.docx --template template.docx --output out.docx
```

`apply-template` 子命令的 flag 全集：

| Flag | Required | Default | 说明 |
|---|---|---|---|
| `--input`                 | ✓ | — | 源文档（内容保留） |
| `--template`              | ✓ | — | 模板文档（格式来源） |
| `--output`                | ✓ | — | 产物路径 |
| `--apply-styles`          | — | `true`  | 复制 `styles.xml` |
| `--apply-theme`           | — | `true`  | 复制主题 |
| `--apply-numbering`       | — | `true`  | 复制 `numbering.xml` |
| `--apply-sections`        | — | `true`  | 应用分节属性（页面设置、栏等） |
| `--apply-headers-footers` | — | `false` | 默认不复制页眉页脚（多节模板谨慎开） |

For complex template operations (multi-template merge, per-section headers/footers, style merging), write C# directly — see Critical Rules below for required patterns.

### 通过 `run_skill_script` 调用（沙盒执行）

两个输入 docx 都要通过 `input_files`（以 artifact 引用的形式）投放。模板可以来自用户上传，也可以是技能自带的资源（放在 `assets/` 下作为 resource file 会自动带进来）。

```python
run_skill_script(
    skill_id="minimax-docx",
    script_name="scripts/docx_cli.sh",
    params=json.dumps({
        "_args": [
            "apply-template",
            "--input", "source.docx",
            "--template", "template.docx",
            "--output", "out.docx",
            "--apply-styles",          # bool flag，传值时写 "true"/"false"（backend 会小写化）
            "--apply-theme",
            "--apply-headers-footers", # 需要时显式打开
        ]
    }, ensure_ascii=False),
    input_files=json.dumps({
        "source.docx":   "artifact:<源 docx id>",
        "template.docx": "artifact:<模板 docx id>",
    }, ensure_ascii=False),
)
```

选项关闭的写法：`"--apply-theme", "false"`（backend 的 `_params_to_cli_args` 把 Python `False` 序列化为 `"false"` 字符串，C# `System.CommandLine` 能识别）。

### Validation + Gate-check（对 Scenario C **强制**）

```python
# 1. 结构校验
run_skill_script(skill_id="minimax-docx", script_name="scripts/docx_cli.sh",
    params=json.dumps({"_args": ["validate", "--input", "out.docx",
                                 "--xsd", "assets/xsd/wml-subset.xsd"]}, ensure_ascii=False),
    input_files=json.dumps({"out.docx": "artifact:<out id>"}, ensure_ascii=False))

# 2. 业务规则 gate-check（Scenario C 必须跑）
run_skill_script(skill_id="minimax-docx", script_name="scripts/docx_cli.sh",
    params=json.dumps({"_args": ["validate", "--input", "out.docx",
                                 "--gate-check", "assets/xsd/business-rules.xsd"]}, ensure_ascii=False),
    input_files=json.dumps({"out.docx": "artifact:<out id>"}, ensure_ascii=False))
```

Gate-check is a **hard requirement**. Do NOT deliver until it passes. If it fails: diagnose, fix, re-run.

Also diff to verify content preservation:
```python
run_skill_script(skill_id="minimax-docx", script_name="scripts/docx_cli.sh",
    params=json.dumps({"_args": ["diff", "--before", "source.docx", "--after", "out.docx"]},
                       ensure_ascii=False),
    input_files=json.dumps({"source.docx": "artifact:...", "out.docx": "artifact:..."},
                            ensure_ascii=False))
```

## Validation pipeline

Run after every write operation. For Scenario C the full pipeline is **mandatory**; for A/B it is **recommended** (skip only if the operation was trivially simple).

```bash
$CLI merge-runs --input doc.docx                                    # 1. consolidate runs
$CLI validate --input doc.docx --xsd assets/xsd/wml-subset.xsd     # 2. XSD structure
$CLI validate --input doc.docx --business                           # 3. business rules
```

If XSD fails, auto-repair and retry:
```bash
$CLI fix-order --input doc.docx
$CLI validate --input doc.docx --xsd assets/xsd/wml-subset.xsd
```

If XSD still fails, fall back to business rules + preview:
```bash
$CLI validate --input doc.docx --business
scripts/docx_preview.sh doc.docx
# Verify: font contamination=0, table count correct, drawing count correct, sectPr count correct
```

Final preview: `scripts/docx_preview.sh doc.docx`

## Critical rules

These prevent file corruption — OpenXML is strict about element ordering.

**Element order** (properties always first):

| Parent | Order |
|--------|-------|
| `w:p`  | `pPr` → runs |
| `w:r`  | `rPr` → `t`/`br`/`tab` |
| `w:tbl`| `tblPr` → `tblGrid` → `tr` |
| `w:tr` | `trPr` → `tc` |
| `w:tc` | `tcPr` → `p` (min 1 `<w:p/>`) |
| `w:body` | block content → `sectPr` (LAST child) |

**Direct format contamination:** When copying content from a source document, inline `rPr` (fonts, color) and `pPr` (borders, shading, spacing) override template styles. Always strip direct formatting — keep only `pStyle` reference and `t` text. Clean tables too (including `pPr/rPr` inside cells).

**Track changes:** `<w:del>` uses `<w:delText>`, never `<w:t>`. `<w:ins>` uses `<w:t>`, never `<w:delText>`.

**Font size:** `w:sz` = points × 2 (12pt → `sz="24"`). Margins/spacing in DXA (1 inch = 1440, 1cm ≈ 567).

**Heading styles MUST have OutlineLevel:** When defining heading styles (Heading1, ThesisH1, etc.), always include `new OutlineLevel { Val = N }` in `StyleParagraphProperties` (H1→0, H2→1, H3→2). Without this, Word sees them as plain styled text — TOC and navigation pane won't work.

**Multi-template merge:** When given multiple template files (font, heading, breaks), read `references/scenario_c_apply_template.md` section "Multi-Template Merge" FIRST. Key rules:
- Merge styles from all templates into one styles.xml. Structure (sections/breaks) comes from the breaks template.
- Each content paragraph must appear exactly ONCE — never duplicate when inserting section breaks.
- NEVER insert empty/blank paragraphs as padding or section separators. Output paragraph count must equal input. Use section break properties (`w:sectPr` inside `w:pPr`) and style spacing (`w:spacing` before/after) for visual separation.
- Insert oddPage section breaks before EVERY chapter heading, not just the first. Even if a chapter has dual-column content, it MUST start with oddPage; use a second continuous break after the heading for column switching.
- Dual-column chapters need THREE section breaks: (1) oddPage in preceding para's pPr, (2) continuous+cols=2 in the chapter HEADING's pPr, (3) continuous+cols=1 in the last body para's pPr to revert.
- Copy `titlePg` settings from the breaks template for EACH section. Abstract and TOC sections typically need `titlePg=true`.

**Multi-section headers/footers:** Templates with 10+ sections (e.g., Chinese thesis) have DIFFERENT headers/footers per section (Roman vs Arabic page numbers, different header text per zone). Rules:
- Use C-2 Base-Replace: copy the TEMPLATE as output base, then replace body content. This preserves all sections, headers, footers, and titlePg settings automatically.
- NEVER recreate headers/footers from scratch — copy template header/footer XML byte-for-byte.
- NEVER add formatting (borders, alignment, font size) not present in the template header XML.
- Non-cover sections MUST have header/footer XML files (at least empty header + page number footer).
- See `references/scenario_c_apply_template.md` section "Multi-Section Header/Footer Transfer".

## References

Load as needed — don't load all at once. Pick the most relevant files for the task.

**The C# samples and design references below are the project's knowledge base ("encyclopedia").** When writing OpenXML code, ALWAYS read the relevant sample file first — it contains compilable, SDK-version-verified patterns that prevent common errors. When making aesthetic decisions, read the design principles and recipe files — they encode tested, harmonious parameter sets from authoritative sources (IEEE, ACM, APA, Nature, etc.), not guesses.

### Scenario guides (read first for each pipeline)

| File | When |
|------|------|
| `references/scenario_a_create.md` | Pipeline A: creating from scratch |
| `references/scenario_b_edit_content.md` | Pipeline B: editing existing content |
| `references/scenario_c_apply_template.md` | Pipeline C: applying template formatting |

### C# code samples (compilable, heavily commented — read when writing code)

| File | Topic |
|------|-------|
| `Samples/DocumentCreationSamples.cs` | Document lifecycle: create, open, save, streams, doc defaults, settings, properties, page setup, multi-section |
| `Samples/StyleSystemSamples.cs` | Styles: Normal/Heading chain, character/table/list styles, DocDefaults, latentStyles, CJK 公文, APA 7th, import, resolve inheritance |
| `Samples/CharacterFormattingSamples.cs` | RunProperties: fonts, size, bold/italic, all underlines, color, highlight, strike, sub/super, caps, spacing, shading, border, emphasis marks |
| `Samples/ParagraphFormattingSamples.cs` | ParagraphProperties: justification, indentation, line/paragraph spacing, keep/widow, outline level, borders, tabs, numbering, bidi, frame |
| `Samples/TableSamples.cs` | Tables: borders, grid, cell props, margins, row height, header repeat, merge (H+V), nested, floating, three-line 三线表, zebra striping |
| `Samples/HeaderFooterSamples.cs` | Headers/footers: page numbers, "Page X of Y", first/even/odd, logo image, table layout, 公文 "-X-", per-section |
| `Samples/ImageSamples.cs` | Images: inline, floating, text wrapping, border, alt text, in header/table, replace, SVG fallback, dimension calc |
| `Samples/ListAndNumberingSamples.cs` | Numbering: bullets, multi-level decimal, custom symbols, outline→headings, legal, Chinese 一/（一）/1./(1), restart/continue |
| `Samples/FieldAndTocSamples.cs` | Fields: TOC, SimpleField vs complex field, DATE/PAGE/REF/SEQ/MERGEFIELD/IF/STYLEREF, TOC styles |
| `Samples/FootnoteAndCommentSamples.cs` | Footnotes, endnotes, comments (4-file system), bookmarks, hyperlinks (internal + external) |
| `Samples/TrackChangesSamples.cs` | Revisions: insertions (w:t), deletions (w:delText!), formatting changes, accept/reject all, move tracking |
| `Samples/AestheticRecipeSamples.cs` | 13 aesthetic recipes from authoritative sources: ModernCorporate, AcademicThesis, ExecutiveBrief, ChineseGovernment (GB/T 9704), MinimalModern, IEEE Conference, ACM sigconf, APA 7th, MLA 9th, Chicago/Turabian, Springer LNCS, Nature, HBR — each with exact values from official style guides |

Note: `Samples/` path is relative to `scripts/dotnet/MiniMaxAIDocx.Core/`.

### Markdown references (read when you need specifications or design rules)

| File | When |
|------|------|
| `references/openxml_element_order.md` | XML element ordering rules (prevents corruption) |
| `references/openxml_units.md` | Unit conversion: DXA, EMU, half-points, eighth-points |
| `references/openxml_encyclopedia_part1.md` | Detailed C# encyclopedia: document creation, styles, character & paragraph formatting |
| `references/openxml_encyclopedia_part2.md` | Detailed C# encyclopedia: page setup, tables, headers/footers, sections, doc properties |
| `references/openxml_encyclopedia_part3.md` | Detailed C# encyclopedia: TOC, footnotes, fields, track changes, comments, images, math, numbering, protection |
| `references/typography_guide.md` | Font pairing, sizes, spacing, page layout, table design, color schemes |
| `references/cjk_typography.md` | CJK fonts, 字号 sizes, RunFonts mapping, GB/T 9704 公文 standard |
| `references/cjk_university_template_guide.md` | Chinese university thesis templates: numeric styleIds (1/2/3 vs Heading1), document zone structure (cover→abstract→TOC→body→references), font expectations, common mistakes |
| `references/design_principles.md` | **Aesthetic foundations**: 6 design principles (white space, contrast/scale, proximity, alignment, repetition, hierarchy) — teaches WHY, not just WHAT |
| `references/design_good_bad_examples.md` | **Good vs Bad comparisons**: 10 categories of typography mistakes with OpenXML values, ASCII mockups, and fixes |
| `references/track_changes_guide.md` | Revision marks deep dive |
| `references/troubleshooting.md` | **Symptom-driven fixes**: 13 common problems indexed by what you SEE (headings wrong, images missing, TOC broken, etc.) — search by symptom, find the fix |
