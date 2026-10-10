"""Option-list PDFs (German market): equipment x variant grid via word positions.

The PDFs are owner-encrypted (empty password). Plain text extraction loses the
grid, so markers are mapped to variant columns by x position. Column headers are
rotated text above the grid: SA-Nr. (option code), price columns, then one
column per variant.

Markers: ■ standard, □ optional, empty cell = not available for that variant.
Bullet lines ("– ...") under an item are its contents (package contents or
components of a standard item) and inherit the item's availability.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdf

STANDARD, OPTIONAL = "■", "□"
MARKERS = {STANDARD, OPTIONAL}
PRICE = re.compile(r"^(\d{1,3}(?:\.\d{3})*|–),–$")
CODE = re.compile(r"^[0-9A-Z]{3,4}$")
BULLET = ("–", "-", "•")
PRICE_HEADER = ("EUR", "19 %", "MwSt", "[EUR")


@dataclass
class Column:
    x: float
    label: str


@dataclass
class Item:
    page: int
    section: str
    name: str
    sa_code: str | None = None
    base_price: float | None = None  # None = no surcharge printed
    availability: dict[str, str] = field(default_factory=dict)  # variant -> standard/optional
    prices: dict[str, float] = field(default_factory=dict)  # variant -> price override
    description: list[str] = field(default_factory=list)
    package: str | None = None  # parent item for contents

    def price_for(self, variant: str) -> float | None:
        return self.prices.get(variant, self.base_price)


def open_pdf(path: Path) -> pdfplumber.PDF:
    reader = pypdf.PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return pdfplumber.open(buffer)


def parse_price(text: str) -> float | None:
    m = PRICE.match(text)
    if not m:
        return None
    return 0.0 if m.group(1) == "–" else float(m.group(1).replace(".", ""))


def header_columns(page) -> list[Column]:
    """Rotated header strings grouped by x position, left to right."""
    groups: dict[int, list] = defaultdict(list)
    for c in page.chars:
        if not c["upright"] and c["top"] < 160:
            groups[round(c["x0"])].append(c)
    cols = []
    for x in sorted(groups):
        text = "".join(c["text"] for c in sorted(groups[x], key=lambda c: -c["top"])).strip()
        if text:
            cols.append(Column(x=x, label=re.sub(r"\s+", " ", text)))
    return cols


def split_columns(cols: list[Column]) -> tuple[Column | None, float | None, list[Column]]:
    sa = next((c for c in cols if c.label.startswith("SA-Nr")), None)
    price_x = [c.x for c in cols if c.label.startswith(PRICE_HEADER)]
    last_meta = max([c.x for c in cols if c is sa or c.label.startswith(PRICE_HEADER)], default=-1)
    variants = [c for c in cols if c.x > last_meta and not c.label.startswith(PRICE_HEADER) and c is not sa]
    return sa, (min(price_x) if price_x else None), variants


def page_lines(page) -> list[list[dict]]:
    words = [w for w in page.extract_words(extra_attrs=["upright"]) if w["upright"]]
    lines: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        key = round(w["top"] / 2.5)
        lines[key].append(w)
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]


def is_heading(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) >= 4 and all(ch.isupper() for ch in letters) and not text.endswith(".")


def extract(path: Path) -> tuple[list[Item], dict[int, str], list[str]]:
    """Return (items, page texts, variant names in column order)."""
    items: list[Item] = []
    texts: dict[int, str] = {}
    all_variants: list[str] = []
    with open_pdf(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            texts[index] = page.extract_text() or ""
            cols = header_columns(page)
            sa_col, price_x, variants = split_columns(cols)
            if len(variants) < 2:
                continue
            for v in variants:
                if v.label not in all_variants:
                    all_variants.append(v.label)
            first_grid_x = sa_col.x - 8 if sa_col else (price_x - 8 if price_x else variants[0].x - 8)
            section = ""
            current: Item | None = None
            parent: Item | None = None
            for line in page_lines(page):
                markers = [w for w in line if w["text"] in MARKERS]
                name_words = [w for w in line if w["x1"] < first_grid_x and w["text"] not in MARKERS]
                name = " ".join(w["text"] for w in name_words).strip()
                code = None
                if sa_col:
                    code = next((w["text"] for w in line if abs(w["x0"] - sa_col.x) < 12 and CODE.match(w["text"])), None)
                price = None
                if price_x is not None:
                    for w in line:
                        if w["x0"] >= price_x - 12 and w["x1"] < variants[0].x - 1:
                            price = parse_price(w["text"]) if price is None else price
                cells = {}
                for m in markers:
                    col = min(variants, key=lambda c: abs(c.x - m["x0"]))
                    if abs(col.x - m["x0"]) < 8:
                        cells[col.label] = "standard" if m["text"] == STANDARD else "optional"

                if not markers:
                    if name and is_heading(name) and code is None:
                        section, current, parent = name, None, None
                    elif current is not None and name:
                        owner = parent or current
                        if name.startswith(BULLET) or (_is_package(owner) and _is_content_line(name)):
                            items.append(_content(owner, name, index, section))
                        else:
                            current.description.append(name)
                    continue

                bullet = name.startswith(BULLET)
                if bullet and current is not None:
                    owner = parent or current
                    _apply_price(owner, cells, price)
                    items.append(_content(owner, name, index, section))
                elif code or (name and sa_col is None) or (name and current is None):
                    current = Item(page=index, section=section, name=name, sa_code=code, base_price=price,
                                   availability=cells)
                    parent = current
                    items.append(current)
                elif current is not None:
                    if name:
                        # Conditional price ("In Verbindung mit M Sportpaket 1.200,– □"): noted, the
                        # base price stays the reference price.
                        note = name + (f" ({price:.0f} EUR)" if price else "")
                        current.description.append(note)
                        for variant, avail in cells.items():
                            current.availability.setdefault(variant, avail)
                    else:
                        _apply_price(current, cells, price)
    items = [i for i in items if i.availability or i.sa_code]
    return items, texts, all_variants


NOTE_PREFIXES = ("Nur ", "In Verbindung", "Nicht ", "Hinweis", "(", "Ausstattungsalternative", "Im Umfang",
                 "Ausstattung ", "inkl.", "bestehend", "Entfällt", "Erhältlich", "Siehe", "siehe")


def _is_package(item: Item) -> bool:
    return "PAKET" in item.name.upper() and item.package is None


def _is_content_line(text: str) -> bool:
    lowered = text.lower()
    return (text[:1].isupper() and not text.startswith(NOTE_PREFIXES)
            and not any(k in lowered for k in ("siehe", "seite", "erhältlich", "informationen")))


def _content(owner: Item, text: str, page: int, section: str) -> Item:
    return Item(page=page, section=section, name=text.lstrip("–-• ").strip(),
                availability=dict(owner.availability), package=owner.name)


def _apply_price(item: Item, cells: dict[str, str], price: float | None) -> None:
    for variant, avail in cells.items():
        item.availability.setdefault(variant, avail)
        if price is not None and price != item.base_price:
            item.prices.setdefault(variant, price)


def rows_for_review(vehicle: str, items: list[Item], variants: list[str]) -> list[dict]:
    """One row per item x variant, for the team review CSV."""
    rows = []
    for item in items:
        for variant in variants:
            rows.append({
                "vehicle": vehicle,
                "page": item.page,
                "section": item.section,
                "item_name": item.name,
                "package": item.package or "",
                "sa_code": item.sa_code or "",
                "variant": variant,
                "availability": item.availability.get(variant, "absent"),
                "price_eur": "" if item.availability.get(variant) != "optional" or item.price_for(variant) is None
                else item.price_for(variant),
                "description": " ".join(item.description)[:300],
            })
    return rows
