"""Tolerant CSV parsing for supplier catalogs and product lists.

Accepts ``,``, ``;`` or tab delimiters, decimal commas (``12,50``) and a few
common header aliases (French and English).
"""

import csv
import io

ALIASES = {
    "code": {"code", "product_code", "cip", "cip13", "ean", "ean13", "gtin", "sku", "reference", "ref"},
    "name": {"name", "product_name", "designation", "libelle", "label", "description", "nom"},
    "unit_price": {"unit_price", "price", "prix", "prix_unitaire", "purchase_price", "prix_achat", "pu"},
    "moq": {"moq", "min_qty", "minimum_order_quantity", "qte_min", "quantite_minimum", "minimum"},
    "pack_size": {"pack_size", "pack", "colisage", "conditionnement", "multiple", "lot"},
    "stock": {"stock", "available", "disponible", "qty_available"},
    "selling_price": {"selling_price", "sale_price", "prix_vente", "pv", "retail_price", "ppv"},
    "max_quantity": {"max_quantity", "max_qty", "demand", "besoin", "qte_max", "forecast", "quantity", "quantite"},
    "min_quantity": {"min_quantity", "min_qty_needed", "qte_obligatoire", "mandatory_quantity", "qte_min_besoin"},
}


def _normalise_header(header: str) -> str:
    key = header.strip().lower().replace(" ", "_").replace("-", "_")
    for canonical, names in ALIASES.items():
        if key in names:
            return canonical
    return key


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(" ", "").replace(" ", "").replace("€", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    return float(text)


def parse_int(value: str | None) -> int | None:
    number = parse_number(value)
    if number is None:
        return None
    if number != int(number):
        raise ValueError(f"expected an integer, got {value!r}")
    return int(number)


def read_rows(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return []
    headers = [_normalise_header(h) for h in rows[0]]
    return [
        {h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)}
        for row in rows[1:]
        if any(cell.strip() for cell in row)
    ]
