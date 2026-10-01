"""Extract supplier catalog lines from PDF files.

Two strategies, tried in order:

1. **Tables** – ruled or well-aligned tables detected by pdfplumber. The header
   row is recognised from its column names (``Code CIP``, ``Désignation``,
   ``Prix HT``, ``Qté min``, ``Colisage``...). Tables continuing on following
   pages without a repeated header reuse the last header seen.
2. **Text lines** – for catalogs laid out as plain text. Each line starting with
   a product code is split into code, name and the trailing numbers; the
   numbers are assigned to columns in the order given by the header line
   (default: unit price, MOQ, pack size, stock).

The output uses the same canonical keys as the CSV reader so both formats go
through the same validation. Scanned PDFs (images without a text layer) are
rejected with an explicit message.
"""

import io
import re

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError

from .csv_import import CATALOG_FIELDS, classify_header, header_layout

DEFAULT_NUMERIC_LAYOUT = ["unit_price", "moq", "pack_size", "stock"]
NUMERIC_FIELDS = {"unit_price", "discount", "moq", "pack_size", "stock", "selling_price"}

# A product code: 4+ characters, at least one digit (CIP 7/13, EAN 13, supplier references).
CODE_RE = re.compile(r"^(?=[\w.-]*\d)[A-Za-z0-9][\w.-]{3,}$")
NUMBER_RE = re.compile(r"^-?\d{1,3}(?:[ . ]\d{3})*(?:[.,]\d+)?$|^-?\d+(?:[.,]\d+)?$")
CURRENCY_RE = re.compile(r"(?i)\s*(?:€|eur\b|%)")


class PdfImportError(ValueError):
    pass


def _clean(cell: str | None) -> str:
    return re.sub(r"\s+", " ", cell or "").strip()


def _header_mapping(cells: list[str]) -> dict[int, str] | None:
    mapping: dict[int, str] = {}
    for index, cell in enumerate(cells):
        field = classify_header(cell) if cell else None
        if field in CATALOG_FIELDS and field not in mapping.values():
            mapping[index] = field
    if "code" in mapping.values() and "unit_price" in mapping.values():
        return mapping
    return None


def _rows_from_tables(tables: list[list[list[str | None]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    mapping: dict[int, str] | None = None
    width = 0
    for table in tables:
        for raw in table:
            cells = [_clean(c) for c in raw]
            header = _header_mapping(cells)
            if header:
                mapping, width = header, len(cells)
                continue
            if mapping is None or len(cells) != width:
                continue
            row = {field: cells[index] for index, field in mapping.items()}
            if CODE_RE.match(row.get("code", "").replace(" ", "")):
                row["code"] = row["code"].replace(" ", "")
                rows.append(row)
    return rows


def _rows_from_text(lines: list[str]) -> list[dict[str, str]]:
    layout = DEFAULT_NUMERIC_LAYOUT
    rows: list[dict[str, str]] = []
    for line in lines:
        text = CURRENCY_RE.sub("", line).strip()
        if not text:
            continue
        fields = header_layout(text)
        if "code" in fields and "unit_price" in fields:
            layout = [f for f in fields if f in NUMERIC_FIELDS and f != "selling_price"] or DEFAULT_NUMERIC_LAYOUT
            continue
        tokens = text.split()
        if len(tokens) < 3 or not CODE_RE.match(tokens[0]):
            continue
        # Trailing numeric tokens, at most one per expected column.
        numbers: list[str] = []
        while len(tokens) > 2 and len(numbers) < len(layout) and NUMBER_RE.match(tokens[-1]):
            numbers.insert(0, tokens.pop())
        if not numbers:
            continue
        row = {"code": tokens[0], "name": " ".join(tokens[1:])}
        row.update(zip(layout, numbers))
        rows.append(row)
    return rows


def read_pdf_rows(content: bytes) -> list[dict[str, str]]:
    try:
        pdf = pdfplumber.open(io.BytesIO(content))
    except (PDFSyntaxError, Exception) as exc:  # pdfminer raises various errors on corrupt files
        raise PdfImportError(f"Unreadable PDF file: {exc}") from exc
    tables: list = []
    lines: list[str] = []
    with pdf:
        for page in pdf.pages:
            tables.extend(page.extract_tables())
            lines.extend((page.extract_text() or "").splitlines())
    if not any(line.strip() for line in lines):
        raise PdfImportError(
            "The PDF contains no text (scanned document?). Please ask the supplier for a text PDF, CSV or Excel export."
        )
    rows = _rows_from_tables(tables)
    if not rows:
        rows = _rows_from_text(lines)
    if not rows:
        raise PdfImportError(
            "No catalog line found in the PDF. Expected a table with at least a product code and a price column."
        )
    return rows
