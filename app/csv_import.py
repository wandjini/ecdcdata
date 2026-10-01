"""Tolerant CSV parsing for supplier catalogs and product lists.

Accepts ``,``, ``;`` or tab delimiters, decimal commas (``12,50``) and common
header names in French and English (``Prix HT``, ``Qté min``, ``Colisage``...).
"""

import csv
import io
import re
import unicodedata

ALIASES = {
    "code": {"code", "product_code", "cip", "cip13", "ean", "ean13", "gtin", "sku", "reference", "ref"},
    "name": {"name", "product_name", "designation", "libelle", "label", "description", "nom"},
    "unit_price": {"unit_price", "price", "prix", "prix_unitaire", "purchase_price", "prix_achat", "pu"},
    "discount": {"discount", "remise", "discount_percent", "remise_pct"},
    "moq": {"moq", "min_qty", "minimum_order_quantity", "qte_min", "quantite_minimum", "minimum"},
    "pack_size": {"pack_size", "pack", "colisage", "conditionnement", "multiple", "lot"},
    "stock": {"stock", "available", "disponible", "qty_available"},
    "selling_price": {"selling_price", "sale_price", "prix_vente", "pv", "retail_price", "ppv"},
    "max_quantity": {"max_quantity", "max_qty", "demand", "besoin", "qte_max", "forecast", "quantity", "quantite"},
    "min_quantity": {"min_quantity", "min_qty_needed", "qte_obligatoire", "mandatory_quantity", "qte_min_besoin"},
}


# Keywords used to recognise free-form catalog headers (PDF tables, exotic CSV).
# Fields are listed by priority: "Prix public" is a selling price, not a purchase price.
KEYWORDS = {
    "selling_price": ["prix public", "prix de vente", "prix vente", "ppv", "ppc", "pv ttc", "selling price",
                      "retail price", "sale price", "public price"],
    "discount": ["remise", "discount", "rabais"],
    "unit_price": ["prix unitaire", "prix d achat", "prix achat", "prix net", "prix ht", "prix", "unit price",
                   "net price", "purchase price", "price", "tarif", "pu ht", "pu"],
    "stock": ["stock", "disponible", "dispo", "available", "availability"],
    "moq": ["moq", "qte min", "qte mini", "quantite minimum", "quantite minimale", "quantite mini", "min qty",
            "minimum order", "commande minimum", "minimum", "mini", "min"],
    "pack_size": ["colisage", "conditionnement", "pack size", "pack", "colis", "lot", "multiple", "carton"],
    "code": ["code cip", "cip13", "cip 13", "cip7", "cip", "ean13", "ean 13", "ean", "gtin", "code", "reference",
             "ref", "sku"],
    "name": ["designation", "libelle", "produit", "product", "description", "article", "nom", "name"],
}
CATALOG_FIELDS = ("code", "name", "unit_price", "discount", "moq", "pack_size", "stock")


def simplify(text: str) -> str:
    """Lowercase, strip accents and punctuation: "Qté. min" -> "qte min"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _keyword_pattern(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b")


_FIELD_PATTERNS = {field: _keyword_pattern(words) for field, words in KEYWORDS.items()}
_ANY_KEYWORD = re.compile(
    r"\b(?:" + "|".join(
        f"(?P<{field}>" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + ")"
        for field, words in KEYWORDS.items()
    ) + r")\b"
)


def classify_header(header: str) -> str | None:
    """Map a header cell to a canonical field name, or None if unknown."""
    key = header.strip().lower().replace(" ", "_").replace("-", "_")
    for canonical, names in ALIASES.items():
        if key in names:
            return canonical
    text = simplify(header)
    if not text:
        return None
    for field, pattern in _FIELD_PATTERNS.items():
        if pattern.search(text):
            return field
    return None


def header_layout(line: str) -> list[str]:
    """Fields named in a free-text header line, in the order they appear."""
    fields: list[str] = []
    for match in _ANY_KEYWORD.finditer(simplify(line)):
        field = match.lastgroup
        if field and field not in fields:
            fields.append(field)
    return fields


def _normalise_header(header: str) -> str:
    return classify_header(header) or header.strip().lower()


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = re.sub(r"(?i)\s|€|eur|%", "", value)
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
