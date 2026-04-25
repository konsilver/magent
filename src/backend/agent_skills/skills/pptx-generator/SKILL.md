---
name: pptx-generator
display_name: PPT演示文稿生成
description: "当用户需要创建、编辑或读取PowerPoint演示文稿时使用，如\"做一份PPT\"\"帮我改这个演示文稿\"\"生成一组汇报幻灯片\"\"提取PPT里的文字\"。支持从零创建（封面、目录、内容、总结等版式）、编辑已有PPTX、提取文本内容。"
license: MIT
metadata:
  version: "1.0"
  category: productivity
  sources:
    - https://gitbrent.github.io/PptxGenJS/
    - https://github.com/microsoft/markitdown
---

# PPTX Generator & Editor

## Overview

This skill handles all PowerPoint tasks: reading/analyzing existing presentations, editing template-based decks via XML manipulation, and creating presentations from scratch using PptxGenJS. It includes a complete design system (color palettes, fonts, style recipes) and detailed guidance for every slide type.

## Quick Reference

| Task | Approach |
|------|----------|
| Read/analyze content | `run_skill_script` → `scripts/extract_text.sh` |
| Edit or create from template | See [Editing Presentations](references/editing.md) |
| **Create from scratch** | **`run_skill_script` → `scripts/build_presentation.js`** (primary) |

| Item | Value |
|------|-------|
| **Dimensions** | 10" x 5.625" (LAYOUT_16x9) |
| **Colors** | 6-char hex without # (e.g., `"FF0000"`) |
| **English font** | Arial (default), or approved alternatives |
| **Chinese font** | Microsoft YaHei |
| **Page badge position** | x: 9.3", y: 5.1" |
| **Theme keys** | `primary`, `secondary`, `accent`, `light`, `bg` |
| **Shapes** | RECTANGLE, OVAL, LINE, ROUNDED_RECTANGLE |
| **Charts** | BAR, LINE, PIE, DOUGHNUT, SCATTER, BUBBLE, RADAR |

## Reference Files

| File | Contents |
|------|----------|
| [slide-types.md](references/slide-types.md) | 5 slide page types (Cover, TOC, Section Divider, Content, Summary) + additional layout patterns |
| [design-system.md](references/design-system.md) | Color palettes, font reference, style recipes (Sharp/Soft/Rounded/Pill), typography & spacing |
| [editing.md](references/editing.md) | Template-based editing workflow, XML manipulation, formatting rules, common pitfalls |
| [pitfalls.md](references/pitfalls.md) | QA process, common mistakes, critical PptxGenJS pitfalls |
| [pptxgenjs.md](references/pptxgenjs.md) | Complete PptxGenJS API reference |

---

## Reading Content

Use `run_skill_script` with `extract_text.sh`:

```
run_skill_script(
  skill_id="pptx-generator",
  script_name="scripts/extract_text.sh",
  input_files='{"presentation.pptx": "artifact:<file_id>"}'
)
```

---

## Creating from Scratch — Workflow

**Use `run_skill_script` with `build_presentation.js`. This is the ONLY supported approach.**

### Step 1: Research & Requirements

Understand user requirements — topic, audience, purpose, tone, content depth.

### Step 2: Select Color Palette & Fonts

Use the [Color Palette Reference](references/design-system.md#color-palette-reference) to select a palette. Use the [Font Reference](references/design-system.md#font-reference) to choose a font pairing.

### Step 3: Select Design Style

Use the [Style Recipes](references/design-system.md#style-recipes) to choose a visual style (Sharp, Soft, Rounded, or Pill).

### Step 4: Plan Slide Outline

Classify **every slide** as exactly one of the [5 page types](references/slide-types.md). Plan content and layout. Ensure visual variety — do NOT repeat the same layout.

### Step 5: Build JSON Payload and Call run_skill_script

Construct a JSON params object with all slides, then call the script directly:

```
run_skill_script(
  skill_id="pptx-generator",
  script_name="scripts/build_presentation.js",
  params='<JSON string>'
)
```

**JSON payload structure:**

```json
{
  "title": "演示文稿标题",
  "author": "作者姓名",
  "subject": "主题描述",
  "theme": {
    "primary": "22223b",
    "secondary": "4a4e69",
    "accent": "9a8c98",
    "light": "c9ada7",
    "bg": "f2e9e4"
  },
  "slides": [
    {
      "type": "cover",
      "title": "封面大标题",
      "subtitle": "副标题",
      "body": "部门 / 日期"
    },
    {
      "type": "toc",
      "title": "目录",
      "items": ["第一章 背景", "第二章 分析", "第三章 结论"]
    },
    {
      "type": "section",
      "title": "第一章 背景",
      "subtitle": "章节副标题（可选）"
    },
    {
      "type": "content",
      "title": "单列内容页",
      "bullets": ["要点一", "要点二", "要点三"],
      "body": "底部补充说明（可选）",
      "highlights": ["核心数据1", "核心数据2", "核心数据3"]
    },
    {
      "type": "content",
      "title": "双列对比页",
      "leftTitle": "优势",
      "rightTitle": "劣势",
      "leftBullets": ["优势1", "优势2"],
      "rightBullets": ["劣势1", "劣势2"]
    },
    {
      "type": "summary",
      "title": "总结",
      "bullets": ["结论一", "结论二", "结论三"],
      "body": "结尾语（可选）"
    }
  ]
}
```

**Slide type → accepted fields:**

| Type | Required | Optional |
|------|----------|----------|
| `cover` | `title` | `subtitle`, `body` (author/date line) |
| `toc` | `title`, `items` | — |
| `section` | `title` | `subtitle` |
| `content` | `title` | `bullets` (single-col) OR `leftTitle`+`rightTitle`+`leftBullets`+`rightBullets` (two-col), `body`, `highlights` (≤3 items) |
| `summary` | `title` | `bullets`, `body` |

### Step 6: QA (Required)

See [QA Process](references/pitfalls.md#qa-process). Use `run_skill_script` with `extract_text.sh` to verify text content of the generated file:

```
run_skill_script(
  skill_id="pptx-generator",
  script_name="scripts/extract_text.sh",
  params='{"_args": ["<file_path>"]}'
)
```

---

## Theme Object Contract

The `theme` field in the JSON payload accepts these **exact keys**:

| Key | Purpose | Example |
|-----|---------|---------|
| `primary` | Darkest color, titles | `"22223b"` |
| `secondary` | Dark accent, body text | `"4a4e69"` |
| `accent` | Mid-tone accent | `"9a8c98"` |
| `light` | Light accent | `"c9ada7"` |
| `bg` | Background color | `"f2e9e4"` |

**NEVER use other key names** like `background`, `text`, `muted`, `darkest`, `lightest`.

**Colors are 6-char hex WITHOUT `#`** — using `#` corrupts the output file.

---

## Page Number Badge

`build_presentation.js` **automatically adds a page number badge** (pill style, bottom-right) to all slides except the Cover. No manual action needed.
