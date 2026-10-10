"""Readers for the provided Excel files (read-only, never modified).

Each reader returns plain dicts with the exact source location so every seeded
value can point back to its sheet, row and cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from app.config import RAW_DATA_DIR

FEEDBACK_FILES = {
    "F70": "customer-feedback_F70_1-series.xlsx",
    "G60": "customer-feedback_G60_5-series.xlsx",
    "G70": "customer-feedback_G70_7-series.xlsx",
}
STUDY_FILE = "customer-study_2025_all-models.xlsx"
SALES_FILE = "sales-volumes_2024-2030.xlsx"

US_LEVELS = ["I Hate It", "A Failure", "Unsatisfactory", "Satisfactory", "Excellent", "Delightful", "I Love It"]


def _rows(path: Path, sheet: str | int = 0):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    ws = wb.worksheets[sheet] if isinstance(sheet, int) else wb[sheet]
    return list(ws.iter_rows(values_only=True))


@dataclass
class FeedbackRow:
    vehicle: str
    excel_row: int  # 1-based row number in the sheet
    verbatim_id: str
    source: str
    country: str | None
    chassis: str
    engine: str | None
    feedback_type: str | None
    text: str
    topic_l23: str | None
    topic_l2: str | None
    topic_l3: str | None


def read_feedback(vehicle: str, raw_dir: Path = RAW_DATA_DIR) -> list[FeedbackRow]:
    rows = _rows(raw_dir / FEEDBACK_FILES[vehicle])
    header = rows[0]
    assert header[0] == "ID" and header[8] == "Customer Feedback", header
    out = []
    for i, r in enumerate(rows[1:], start=2):
        if r[0] is None:
            continue
        out.append(FeedbackRow(
            vehicle=vehicle, excel_row=i, verbatim_id=str(r[0]).strip(), source=str(r[1]).strip(),
            country=(str(r[4]).strip() if r[4] else None), chassis=str(r[5]).strip(),
            engine=(str(r[6]).strip() if r[6] else None), feedback_type=(str(r[7]).strip() if r[7] else None),
            text=str(r[8]), topic_l23=(str(r[9]).strip() if r[9] else None),
            topic_l2=(str(r[10]).strip() if r[10] else None), topic_l3=(str(r[11]).strip() if r[11] else None),
        ))
    return out


# Source C answers are sometimes prefixed with the survey question ("...?\n: ...").
_QUESTION = re.compile(r"^(?P<q>[^\n]{10,400}\?)\s*\n:\s*(?P<a>.*)$", re.S)


def split_question(text: str) -> tuple[str | None, str]:
    """Return (question prefix, answer). The answer is an exact substring of text."""
    m = _QUESTION.match(text)
    if not m:
        return None, text
    answer = m.group("a").strip()
    if len(answer) >= 2 and answer[0] == '"' and answer[-1] == '"':
        answer = answer[1:-1].strip()
    assert answer in text
    return m.group("q").strip(), answer


@dataclass
class StudyRow:
    sheet: str
    vehicle: str  # BMW line (G68 belongs to the G60 line)
    model_label: str
    market: str
    attribute: str
    method: str
    raw: dict
    normalized: float
    dissatisfied_share: float | None
    source_location: str


# Column labels -> (vehicle line, market) for CN_EU_2025; row 2 = market, row 3 = model.
_CN_EU_MARKETS = {"China": "China", "EU": "Europe"}
_LINE = {"F70": "F70", "G60": "G60", "G68": "G60", "G70": "G70"}


def _line_of(label: str) -> str:
    code = re.search(r"\b([FG]\d{2})\b", label)
    if not code:
        raise ValueError(f"no chassis code in {label!r}")
    return _LINE[code.group(1)]


def read_study(raw_dir: Path = RAW_DATA_DIR) -> list[StudyRow]:
    out: list[StudyRow] = []
    path = raw_dir / STUDY_FILE

    rows = _rows(path, "CN_EU_2025")
    markets_row, models_row = rows[1], rows[2]
    columns = []
    market = None
    for col in range(1, len(models_row)):
        if markets_row[col]:
            market = _CN_EU_MARKETS[str(markets_row[col]).strip()]
        if models_row[col]:
            columns.append((col, str(models_row[col]).strip(), market))
    attribute = str(models_row[0]).strip()  # first block name sits on the model row
    for i, r in enumerate(rows[3:], start=4):
        label = str(r[0]).strip() if r[0] else None
        if label and label != "Mean":
            attribute = label
        elif label == "Mean":
            for col, model, mkt in columns:
                value = r[col]
                if value is None:
                    continue
                out.append(StudyRow(
                    sheet="CN_EU_2025", vehicle=_line_of(model), model_label=model, market=mkt,
                    attribute=attribute, method="mean_10pt", raw={"mean": float(value), "model": model},
                    normalized=round(float(value) / 10.0, 4), dissatisfied_share=None,
                    source_location=f"CN_EU_2025!{openpyxl.utils.get_column_letter(col + 1)}{i}",
                ))

    rows = _rows(path, "US_2025")
    us_models: list[tuple[int, str]] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        label = str(r[0]).strip() if r[0] else None
        is_level = r[1] is not None and "of column" in str(r[1])
        if label and not is_level:
            models = [(c, str(r[c]).strip()) for c in (2, 3) if len(r) > c and r[c]]
            us_models = models or us_models  # model names are printed on the first block only
            models = us_models
            block = []
            j = i + 1
            while j < len(rows) and rows[j][1] is not None and "of column" in str(rows[j][1]):
                block.append((j, rows[j]))
                j += 1
            levels = {str(br[0]).strip(): (bj, br) for bj, br in block}
            if all(l in levels for l in US_LEVELS) and models:
                for c, model in models:
                    shares = [float(levels[l][1][c] or 0.0) for l in US_LEVELS]
                    total = sum(shares)
                    if total <= 0:
                        continue
                    mean = sum((k + 1) * s for k, s in enumerate(shares)) / total
                    first = levels[US_LEVELS[0]][0] + 1
                    last = levels[US_LEVELS[-1]][0] + 1
                    col = openpyxl.utils.get_column_letter(c + 1)
                    out.append(StudyRow(
                        sheet="US_2025", vehicle=_line_of(model), model_label=model, market="USA",
                        attribute=label, method="dist_7level",
                        raw={"model": model, "shares": dict(zip(US_LEVELS, shares))},
                        normalized=round((mean - 1) / 6, 4),
                        dissatisfied_share=round(sum(shares[:3]) / total, 4),
                        source_location=f"US_2025!{col}{first}:{col}{last}",
                    ))
            i = j
        else:
            i += 1
    return out


@dataclass
class SalesRow:
    vehicle: str
    sheet: str
    market: str
    year: int
    units: float | None
    raw: object
    cell: str
    suspect: bool
    note: str | None


_SALES_MARKET = {"EU": "Europe", "CN": "China", "US": "USA", "RoW": "Rest of World"}


def read_sales(raw_dir: Path = RAW_DATA_DIR) -> list[SalesRow]:
    wb = openpyxl.load_workbook(raw_dir / SALES_FILE, read_only=True, data_only=False)
    values: dict[tuple, SalesRow] = {}
    out = []
    for ws in wb.worksheets:
        vehicle = _LINE[ws.title.split("_")[0]]
        rows = list(ws.iter_rows(values_only=True))
        header = rows[0]
        for i, r in enumerate(rows[1:], start=2):
            market = _SALES_MARKET[str(r[1]).strip()]
            for c, name in enumerate(header):
                m = re.match(r"volume_(\d{4})", str(name))
                if not m:
                    continue
                raw = r[c]
                note = None
                units: float | None
                if isinstance(raw, (int, float)):
                    units = float(raw)
                else:
                    units, note = None, f"cell holds {raw!r}, not a number"
                cell = f"{ws.title}!{openpyxl.utils.get_column_letter(c + 1)}{i}"
                row = SalesRow(vehicle, ws.title, market, int(m.group(1)), units, raw, cell, units is None, note)
                out.append(row)
                values[(vehicle, market, row.year)] = row
    # Identical Rest of World series for F70 and G60_G68 look like a placeholder (data README).
    f70 = [values.get(("F70", "Rest of World", y)) for y in (2024, 2025, 2030)]
    g60 = [values.get(("G60", "Rest of World", y)) for y in (2024, 2025, 2030)]
    if all(f70) and all(g60) and [x.units for x in f70] == [x.units for x in g60]:
        for x in f70 + g60:
            x.suspect = True
            x.note = "Rest of World values identical for F70 and G60_G68 (likely placeholder)"
    return out
