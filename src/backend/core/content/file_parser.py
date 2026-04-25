"""File parsing utilities for chat attachments.

Supported formats:
  - PDF  : calls external file-parser API service
  - DOCX : pandoc (docx → markdown)
  - DOC / WPS : LibreOffice headless → DOCX, then pandoc
  - TXT  : UTF-8 / GBK direct decode
  - XLSX : openpyxl → markdown tables
  - XLS  : xlrd → markdown tables (fallback: LibreOffice → XLSX → openpyxl)
  - CSV  : csv module → markdown table

All public functions return a markdown/plain-text string, or raise RuntimeError
with a human-readable Chinese error message on failure.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
from typing import Optional

import requests


# ── config helpers (DB-first, env fallback) ──────────────────────────────────

def _svc_get(key: str, default: str = "") -> str:
    """Read from SystemConfigService (DB-first), fall back to env."""
    try:
        from core.config.system_config import SystemConfigService
        val = SystemConfigService.get_instance().get(key)
        if val is not None:
            return val.strip()
    except Exception:
        pass
    return (os.getenv(_ENV_MAP.get(key, ""), default) or default).strip()

# config_key → env var fallback mapping
_ENV_MAP = {
    "file_parser.api_url": "FILE_PARSER_API_URL",
    "file_parser.timeout": "FILE_PARSER_TIMEOUT",
    "file_parser.lang_list": "FILE_PARSER_LANG_LIST",
    "file_parser.backend": "FILE_PARSER_BACKEND",
    "file_parser.parse_method": "FILE_PARSER_PARSE_METHOD",
    "file_parser.formula_enable": "FILE_PARSER_FORMULA_ENABLE",
    "file_parser.table_enable": "FILE_PARSER_TABLE_ENABLE",
}


def _cfg_api_url() -> str:
    return _svc_get("file_parser.api_url")


def _cfg_timeout() -> int:
    try:
        return int(_svc_get("file_parser.timeout", "60"))
    except ValueError:
        return 60


def _cfg_parse_params() -> dict:
    return {
        "lang_list": _svc_get("file_parser.lang_list", "ch"),
        "backend": _svc_get("file_parser.backend", "pipeline"),
        "parse_method": _svc_get("file_parser.parse_method", "auto"),
        "formula_enable": _svc_get("file_parser.formula_enable", "true"),
        "table_enable": _svc_get("file_parser.table_enable", "true"),
    }


# ── PDF ───────────────────────────────────────────────────────────────────────

def parse_pdf(file_bytes: bytes, filename: str = "file.pdf") -> str:
    """Parse PDF via external file-parser API. Returns markdown text."""
    api_url = _cfg_api_url()
    if not api_url:
        raise RuntimeError("FILE_PARSER_API_URL 未配置，无法解析 PDF 文件")

    timeout = _cfg_timeout()

    try:
        resp = requests.post(
            api_url,
            files={"files": (filename, file_bytes, "application/pdf")},
            data=_cfg_parse_params(),
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(f"PDF 解析服务超时（{timeout}s），请稍后重试")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"PDF 解析服务请求失败: {e}")

    result = resp.json()
    results = result.get("results", {})
    if not results:
        raise RuntimeError("PDF 解析服务返回结果为空")

    title = next(iter(results))
    content = results[title].get("md_content", "")
    if not content:
        raise RuntimeError("PDF 解析服务返回内容为空")
    return content


# ── DOCX (via pandoc) ─────────────────────────────────────────────────────────

def _docx_bytes_to_markdown(docx_bytes: bytes) -> str:
    """Convert DOCX bytes to markdown string via pandoc."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.docx")
        with open(input_path, "wb") as f:
            f.write(docx_bytes)

        try:
            result = subprocess.run(
                ["pandoc", input_path, "-t", "markdown", "--wrap=none"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            raise RuntimeError("pandoc 未安装，无法解析 Word 文档")
        except subprocess.TimeoutExpired:
            raise RuntimeError("pandoc 转换超时")

        if result.returncode != 0:
            raise RuntimeError(f"pandoc 转换失败: {result.stderr[:300]}")

        return result.stdout


def parse_docx(file_bytes: bytes) -> str:
    """Parse DOCX bytes → markdown via pandoc."""
    return _docx_bytes_to_markdown(file_bytes)


# ── DOC / WPS (LibreOffice → DOCX → pandoc) ──────────────────────────────────

def _convert_to_docx_bytes(file_bytes: bytes, suffix: str) -> bytes:
    """Use LibreOffice headless to convert DOC/WPS bytes → DOCX bytes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, f"input{suffix}")
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        try:
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to", "docx",
                    "--outdir", tmpdir,
                    input_path,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError("LibreOffice 未安装，无法解析 DOC/WPS 文件")
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice 转换超时")

        output_path = os.path.join(tmpdir, "input.docx")
        if not os.path.exists(output_path):
            raise RuntimeError(f"LibreOffice 转换失败: {result.stderr[:300]}")

        with open(output_path, "rb") as f:
            return f.read()


def parse_doc_wps(file_bytes: bytes, suffix: str) -> str:
    """Parse DOC/WPS via LibreOffice → DOCX → pandoc markdown."""
    docx_bytes = _convert_to_docx_bytes(file_bytes, suffix)
    return _docx_bytes_to_markdown(docx_bytes)


# ── TXT ───────────────────────────────────────────────────────────────────────

def parse_txt(file_bytes: bytes) -> str:
    """Decode text file bytes, trying UTF-8 then GBK then latin-1."""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


# ── XLSX ──────────────────────────────────────────────────────────────────────

def _rows_to_markdown(header: list[str], data_rows: list[list[str]],
                      sheet_title: str | None = None) -> str:
    """Convert header + data rows into a markdown table string."""
    lines: list[str] = []
    if sheet_title:
        lines.append(f"## Sheet: {sheet_title}")
        lines.append("")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for row in data_rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def parse_xlsx(file_bytes: bytes) -> str:
    """Parse XLSX bytes → markdown tables (one per sheet)."""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl 未安装，无法解析 XLSX 文件（pip install openpyxl）")

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    sections: list[str] = []
    for sheet in wb.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(c or "") for c in rows[0]]
        data_rows = []
        for row in rows[1:]:
            if all(c is None for c in row):
                continue
            data_rows.append([str(c or "") for c in row])
        sections.append(_rows_to_markdown(header, data_rows, sheet.title))
    wb.close()

    if not sections:
        raise RuntimeError("XLSX 文件无有效数据")
    return "\n\n".join(sections)


def parse_xls(file_bytes: bytes) -> str:
    """Parse legacy XLS bytes → markdown tables.

    Tries xlrd first; falls back to LibreOffice → XLSX → openpyxl.
    """
    try:
        import xlrd
        wb = xlrd.open_workbook(file_contents=file_bytes)
        sections: list[str] = []
        for sheet in wb.sheets():
            if sheet.nrows == 0:
                continue
            header = [str(sheet.cell_value(0, c) or "") for c in range(sheet.ncols)]
            data_rows = []
            for r in range(1, sheet.nrows):
                row = [str(sheet.cell_value(r, c) or "") for c in range(sheet.ncols)]
                if all(v == "" for v in row):
                    continue
                data_rows.append(row)
            sections.append(_rows_to_markdown(header, data_rows, sheet.name))
        if not sections:
            raise RuntimeError("XLS 文件无有效数据")
        return "\n\n".join(sections)
    except ImportError:
        pass  # xlrd not installed, try LibreOffice fallback

    # Fallback: LibreOffice → XLSX → openpyxl
    xlsx_bytes = _convert_xls_to_xlsx(file_bytes)
    return parse_xlsx(xlsx_bytes)


def _convert_xls_to_xlsx(file_bytes: bytes) -> bytes:
    """Use LibreOffice headless to convert XLS → XLSX."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.xls")
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        try:
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to", "xlsx",
                    "--outdir", tmpdir,
                    input_path,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "xlrd 和 LibreOffice 均不可用，无法解析 XLS 文件"
                "（pip install xlrd 或安装 LibreOffice）"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice 转换 XLS → XLSX 超时")

        output_path = os.path.join(tmpdir, "input.xlsx")
        if not os.path.exists(output_path):
            raise RuntimeError(f"LibreOffice 转换 XLS 失败: {result.stderr[:300]}")

        with open(output_path, "rb") as f:
            return f.read()


# ── CSV ──────────────────────────────────────────────────────────────────────

def parse_csv(file_bytes: bytes) -> str:
    """Parse CSV bytes → markdown table."""
    # Decode with encoding detection
    text = parse_txt(file_bytes)

    # Sniff dialect
    try:
        sample = text[:8192]
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)

    if not rows:
        raise RuntimeError("CSV 文件无有效数据")

    header = [c.strip() for c in rows[0]]
    data_rows = []
    for row in rows[1:]:
        if all(c.strip() == "" for c in row):
            continue
        # Pad or trim to match header length
        padded = [c.strip() for c in row]
        while len(padded) < len(header):
            padded.append("")
        data_rows.append(padded[:len(header)])

    return _rows_to_markdown(header, data_rows)


# ── Dispatcher ────────────────────────────────────────────────────────────────

_EXT_MAP: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc_wps",
    ".wps": "doc_wps",
    ".txt": "txt",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".csv": "csv",
}

SUPPORTED_EXTENSIONS = list(_EXT_MAP.keys())


def parse_file(file_bytes: bytes, filename: str) -> Optional[str]:
    """
    Dispatch to the correct parser by file extension.

    Returns extracted text/markdown, or None if the format is unsupported.
    Raises RuntimeError with a Chinese message on parse failure.
    """
    suffix = os.path.splitext(filename.lower())[1]
    kind = _EXT_MAP.get(suffix)

    if kind == "pdf":
        return parse_pdf(file_bytes, filename)
    elif kind == "docx":
        return parse_docx(file_bytes)
    elif kind == "doc_wps":
        return parse_doc_wps(file_bytes, suffix)
    elif kind == "txt":
        return parse_txt(file_bytes)
    elif kind == "xlsx":
        return parse_xlsx(file_bytes)
    elif kind == "xls":
        return parse_xls(file_bytes)
    elif kind == "csv":
        return parse_csv(file_bytes)
    else:
        return None
