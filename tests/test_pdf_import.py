import pytest

from app.csv_import import classify_header, header_layout
from app.pdf_import import PdfImportError, read_pdf_rows
from tests.conftest import register
from tests.pdf_factory import blank_pdf, table_pdf, text_pdf

ROWS = [
    ["3400930000011", "Paracetamol 500mg x16", "1,15 €", "24", "12", ""],
    ["3400930000028", "Ibuprofène 400mg x20", "2,05 €", "10", "10", "500"],
    ["3400930000035", "Amoxicilline 1g x6", "3,95 €", "5", "1", "30"],
]
TEXT_CATALOG = [
    "GROSSISTE D  -  Catalogue octobre 2026",
    "",
    "Code      Designation                 Prix HT  Remise  Qte min  Colisage",
    "3400930000011 Paracetamol 500mg x16      1,20 EUR   5%    24     12",
    "3400930000042 Vitamine C 1000mg x30      4,80 EUR   0%    12      6",
    "Page 1/1",
]


@pytest.mark.parametrize(
    "header, field",
    [
        ("Code CIP", "code"), ("Désignation", "name"), ("Prix HT", "unit_price"), ("PU HT", "unit_price"),
        ("Prix public TTC", "selling_price"), ("Qté min.", "moq"), ("Colisage", "pack_size"),
        ("Stock dispo", "stock"), ("Remise %", "discount"), ("Product code", "code"), ("Unit price", "unit_price"),
        ("TVA", None),
    ],
)
def test_classify_header(header, field):
    assert classify_header(header) == field


def test_header_layout_order():
    assert header_layout("Réf.  Libellé  Colisage  Prix net  Stock") == ["code", "name", "pack_size", "unit_price", "stock"]


def test_table_pdf_spanning_pages():
    rows = read_pdf_rows(table_pdf(ROWS, rows_per_page=2))  # 2nd page has no header row
    assert [r["code"] for r in rows] == ["3400930000011", "3400930000028", "3400930000035"]
    assert rows[1] == {
        "code": "3400930000028", "name": "Ibuprofène 400mg x20", "unit_price": "2,05 €",
        "moq": "10", "pack_size": "10", "stock": "500",
    }


def test_text_pdf_uses_header_column_order():
    rows = read_pdf_rows(text_pdf(TEXT_CATALOG))
    assert rows == [
        {"code": "3400930000011", "name": "Paracetamol 500mg x16", "unit_price": "1,20", "discount": "5",
         "moq": "24", "pack_size": "12"},
        {"code": "3400930000042", "name": "Vitamine C 1000mg x30", "unit_price": "4,80", "discount": "0",
         "moq": "12", "pack_size": "6"},
    ]


def test_scanned_or_invalid_pdf_is_rejected():
    with pytest.raises(PdfImportError, match="no text"):
        read_pdf_rows(blank_pdf())
    with pytest.raises(PdfImportError, match="Unreadable"):
        read_pdf_rows(b"%PDF-1.4 garbage")


def test_pdf_catalog_import_with_preview(client):
    headers = register(client, "Pharmacie PDF", "pdf@example.com")
    sup = client.post("/api/suppliers", json={"name": "Grossiste D"}, headers=headers).json()
    url = f"/api/suppliers/{sup['id']}/catalog/import"
    pdf = text_pdf(TEXT_CATALOG)

    preview = client.post(f"{url}?dry_run=true", files={"file": ("tarifs.pdf", pdf, "application/pdf")}, headers=headers).json()
    assert preview["dry_run"] and preview["created"] == 2 and preview["errors"] == []
    para = preview["preview"][0]
    assert para["code"] == "3400930000011" and para["unit_price"] == pytest.approx(1.14)  # 1.20 - 5 %
    assert para["moq"] == 24 and para["pack_size"] == 12
    assert client.get(f"/api/suppliers/{sup['id']}/catalog", headers=headers).json() == []  # nothing saved

    report = client.post(url, files={"file": ("tarifs.pdf", pdf, "application/pdf")}, headers=headers).json()
    assert report["created"] == 2 and report["preview"] == []
    items = client.get(f"/api/suppliers/{sup['id']}/catalog", headers=headers).json()
    assert [(i["code"], i["unit_price"]) for i in items] == [("3400930000011", 1.14), ("3400930000042", 4.8)]

    # Re-importing a table PDF updates existing lines.
    report = client.post(url, files={"file": ("t.pdf", table_pdf(ROWS))}, headers=headers).json()
    assert (report["created"], report["updated"]) == (2, 1)


def test_pdf_import_errors(client):
    headers = register(client, "Pharmacie PDF2", "pdf2@example.com")
    sup = client.post("/api/suppliers", json={"name": "S"}, headers=headers).json()
    r = client.post(f"/api/suppliers/{sup['id']}/catalog/import", files={"file": ("scan.pdf", blank_pdf())}, headers=headers)
    assert r.status_code == 422 and "scanned" in r.json()["detail"]

    bad = table_pdf([["3400930000011", "Paracetamol", "abc", "1", "1", ""], ["3400930000028", "Ibuprofen", "2,00", "1", "1", ""]])
    report = client.post(f"/api/suppliers/{sup['id']}/catalog/import", files={"file": ("x.pdf", bad)}, headers=headers).json()
    assert report["created"] == 1 and report["errors"][0].startswith("row 1:")

    r = client.post("/api/products/import", files={"file": ("needs.pdf", bad)}, headers=headers)
    assert r.status_code == 415
