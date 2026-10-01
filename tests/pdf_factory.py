"""Build small supplier-catalog PDFs for the tests (requires fpdf2)."""

from fpdf import FPDF as _FPDF


class FPDF(_FPDF):
    def __init__(self):
        super().__init__()
        self.core_fonts_encoding = "windows-1252"  # supports "€" with core fonts

HEADER = ["Code CIP", "Désignation", "Prix HT", "Qté min.", "Colisage", "Stock"]


def table_pdf(rows: list[list[str]], header: list[str] = HEADER, rows_per_page: int = 1000) -> bytes:
    """Ruled table; the header is only printed on the first page."""
    pdf = FPDF()
    pdf.set_font("helvetica", size=9)
    for start in range(0, len(rows), rows_per_page):
        pdf.add_page()
        if start == 0:
            pdf.set_font("helvetica", "B", 14)
            pdf.cell(0, 10, "Grossiste C - Tarifs 2026", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("helvetica", size=9)
        with pdf.table(first_row_as_headings=start == 0) as table:
            if start == 0:
                table.row(header)
            for r in rows[start:start + rows_per_page]:
                table.row(r)
    return bytes(pdf.output())


def text_pdf(lines: list[str]) -> bytes:
    """Plain text lines, no table ruling."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("courier", size=9)
    for line in lines:
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def blank_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.rect(20, 20, 50, 50)
    return bytes(pdf.output())
