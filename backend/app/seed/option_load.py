"""Option lists -> review CSV -> source_record (one per page), vehicle_feature, data_record.

The extraction is reviewed with the team (phase 2 checkpoint): ``write_review``
writes ``backend/seed/review/option_list_<vehicle>.csv`` (item x variant) and
``option_features.csv`` (item -> proposed feature). The load reads the review
CSVs, so corrections made there are what lands in the database.
"""

from __future__ import annotations

import csv
import hashlib
import re

from app import audit
from app.config import RAW_DATA_DIR
from app.seed import option_lists
from app.seed.catalog_proposal import PROPOSED_BY, REVIEW_DIR
from app.seed.loaders import store_raw_file

FILES = {
    "F70": "option-list_F70_1-series_2026-07.pdf",
    "G60": "option-list_G60_5-series_2026-07.pdf",
    "G70": "option-list_G70_7-series_2026-07.pdf",
}

# German option-list wording -> feature of the catalog (first match wins).
ITEM_FEATURES: list[tuple[str, str]] = [
    (r"sitzbelüftung|sitzheizung", "Seat heating and ventilation"),
    (r"massage", "Seat massage"),
    (r"sitzverstellung|memory|lordosen", "Seat adjustment and memory"),
    (r"sitze\b|sportsitze|komfortsitze|multifunktionssitze", "Seat comfort"),
    (r"lenkradheizung", "Heated steering wheel"),
    (r"standheizung|standklima|standlüftung", "Auxiliary heating"),
    (r"klimaautomatik|klima", "Climate control"),
    (r"head-up", "Head-Up Display"),
    (r"harman|bowers|lautsprecher|sound system|hifi", "Audio system"),
    (r"dab-tuner|radio", "Radio"),
    (r"wireless charging|kabellos", "Wireless phone charging"),
    (r"esim", "Personal eSIM"),
    (r"wlan|hotspot", "WiFi hotspot"),
    (r"digital key", "Digital Key"),
    (r"komfortzugang", "Comfort Access"),
    (r"heckklappe", "Trunk lid operation"),
    (r"autobahn|highway", "Highway Assistant"),
    (r"driving assistant professional|aktive geschwindigkeitsregelung|tempomat", "Adaptive cruise control"),
    (r"spurwechsel", "Lane change assistant"),
    (r"spurverlassen", "Lane departure warning"),
    (r"frontkollision|heckkollision", "Front collision warning"),
    (r"querverkehr|linksabbiege", "Cross traffic warning"),
    (r"speed limit", "Speed limit assistant"),
    (r"remote 3d", "Remote 3D View"),
    (r"surround view|panoramaansicht|3d view", "Surround view camera"),
    (r"rückfahrkamera", "Rear view camera"),
    (r"park distance|einparkhilfe|parken-anfahr", "Park Distance Control"),
    (r"parking assistant|parkassistent|rangierassistent|reversing assistant", "Parking assistant"),
    (r"driving assistant", "Driver assistance overall"),
    (r"drive recorder|anti-diebstahl-rekorder", "Drive recorder"),
    (r"innenraumkamera", "Interior camera"),
    (r"glasdach|schiebedach|sky lounge", "Panorama glass roof"),
    (r"fernlicht", "High-beam assistant"),
    (r"scheinwerfer|laserlicht|nebel|lichtpaket|rückleuchten", "Exterior lighting"),
    (r"ambient|iconic glow|lichtkontur|welcome", "Ambient lighting"),
    (r"reifenreparatur|reifenpannen", "Tire repair kit"),
    (r"reifendruck", "Tire pressure monitoring"),
    (r"leichtmetallräder|aerodynamikräder|räder|reifen|felgen", "Wheels and tires"),
    (r"fahrwerk|luftfederung|dämpf", "Suspension and damping"),
    (r"lenkung", "Steering"),
    (r"getriebe|steptronic", "Transmission"),
    (r"ladekabel", "Charging cable"),
    (r"ac-laden|laden|ladeleistung", "Charging the high-voltage battery"),
    (r"iconicsounds|sound electric|abgasanlage", "Engine sound"),
    (r"anhänger", "Towing"),
    (r"alarmanlage", "Anti-theft alarm"),
    (r"durchlade|gepäckraum|kofferraum", "Luggage space"),
    (r"sonnenschutz|rollo", "Sun protection"),
    (r"ablage", "Storage compartments"),
    (r"lenkrad", "Steering wheel"),
    (r"leder|alcantara|polster|veganza|merino|applikation|interieurleiste|dachhimmel|clarity", "Upholstery and interior materials"),
    (r"außenspiegel", "Exterior mirrors"),
    (r"theatre|fond.*entertainment|fondbildschirm", "Rear seat entertainment"),
    (r"automatische türbetätigung|automatic doors|türen", "Automatic doors"),
    (r"soft.?close", "Door opening and closing"),
    (r"akustik", "Interior quietness"),
    (r"gestik", "Gesture control"),
    (r"night vision", "Night vision"),
    (r"garagentor", "Interior mirror and garage door opener"),
    (r"live cockpit|curved display|navigation", "Infotainment system overall"),
    (r"lackierung|metallic|uni |exterieurpaket|spoiler|carbon|shadow line|schriftzug|kidney|niere", "Exterior design"),
    (r"travel & comfort", "Interior comfort"),
]

# Customer-facing tech data (CLAUDE.md scope: range and charging as experienced, space, performance).
TECH_METRICS = [
    (r"^Elektrische Reichweite, WLTP in km", "electric range WLTP", "km"),
    (r"^Maximale Ladeleistung DC in kW", "max DC charging power", "kW"),
    (r"^Maximale Ladeleistung AC in kW", "max AC charging power", "kW"),
    (r"^Minimale Ladedauer DC 10–80 %", "DC charging time 10-80 %", "min"),
    (r"^High-Power-Laden \(HPC\) in km", "range added in 10 min DC", "km"),
    (r"^Beschleunigung 0–100 km/h in s", "acceleration 0-100 km/h", "s"),
    (r"^Höchstgeschwindigkeit", "top speed", "km/h"),
    (r"^Gepäckraumvolumen in l", "luggage volume", "l"),
    (r"^Energieverbrauch, kombiniert WLTP in kWh/100 km", "energy consumption WLTP", "kWh/100 km"),
    (r"^Kraftstoffverbrauch, kombiniert WLTP in l/100 km", "fuel consumption WLTP", "l/100 km"),
]
_VALUE = re.compile(r"\d[\d.]*(?:,\d+)?(?:\s*–\s*\d[\d.]*(?:,\d+)?)?")


def item_feature(name: str) -> str:
    lowered = name.lower()
    for pattern, feature in ITEM_FEATURES:
        if re.search(pattern, lowered):
            return feature
    return ""


def _de_number(text: str) -> float:
    first = text.split("–")[0].strip()
    return float(first.replace(".", "").replace(",", "."))


def tech_data(vehicle: str, texts: dict[int, str], variants: list[str]) -> list[dict]:
    """Rows of the technical data pages: one per metric x variant."""
    rows = []
    known = sorted(set(variants), key=len, reverse=True)
    for page, text in texts.items():
        if not text.startswith("TECHNISCHE WERTE."):
            continue
        lines = text.splitlines()
        header = lines[1] if len(lines) > 1 else ""
        found = []
        rest = header
        for v in known:
            idx = rest.find(v)
            if idx >= 0:
                found.append((header.find(v), v))
                rest = rest.replace(v, " " * len(v), 1)
        page_variants = [v for _, v in sorted(found)]
        if not page_variants:
            m = re.match(r"BMW \S+(?: \S+)? (.*)$", header)
            page_variants = m.group(1).split() if m else []
        for line in lines[2:]:
            for pattern, metric, unit in TECH_METRICS:
                if not re.match(pattern, line):
                    continue
                label_end = re.match(pattern, line).end()
                values = _VALUE.findall(re.sub(r"^\D*", "", line[label_end:]).strip())
                # Footnote digits glued to the label ("in km2 513 – 627") are dropped by the regex anchor.
                if len(values) > len(page_variants):
                    values = values[-len(page_variants):]
                if len(values) != len(page_variants):
                    continue
                for variant, value in zip(page_variants, values):
                    rows.append({"vehicle": vehicle, "page": page, "variant": variant, "metric": metric,
                                 "value": _de_number(value), "value_text": value, "unit": unit, "line": line})
    return rows


def write_review(vehicles=("F70", "G60", "G70")) -> dict[str, int]:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    counts = {}
    names: dict[str, str] = {}
    for vehicle in vehicles:
        path = REVIEW_DIR / f"option_list_{vehicle}.csv"
        items, texts, variants = option_lists.extract(RAW_DATA_DIR / FILES[vehicle])
        rows = option_lists.rows_for_review(vehicle, items, variants)
        for it in items:
            names.setdefault(it.name, item_feature(it.name))
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) + ["confirmed"])
                w.writeheader()
                for r in rows:
                    w.writerow({**r, "confirmed": ""})
        tech = tech_data(vehicle, texts, variants)
        tpath = REVIEW_DIR / f"tech_data_{vehicle}.csv"
        if not tpath.exists():
            with open(tpath, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(tech[0].keys()) + ["confirmed"] if tech else ["confirmed"])
                w.writeheader()
                for r in tech:
                    w.writerow({**r, "confirmed": ""})
        counts[vehicle] = {"items": len(items), "rows": len(rows), "variants": len(variants), "tech": len(tech)}
    fpath = REVIEW_DIR / "option_features.csv"
    if not fpath.exists():
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["item_name", "proposed_feature", "proposed_by", "confirmed"])
            for name, feature in sorted(names.items()):
                w.writerow([name, feature, PROPOSED_BY, ""])
    counts["distinct_items"] = len(names)
    counts["mapped_items"] = sum(1 for f in names.values() if f)
    return counts


def _read_csv(name: str) -> list[dict]:
    with open(REVIEW_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load(conn, run_id, feature_ids: dict[str, str], vehicles=("F70", "G60", "G70")) -> dict[str, int]:
    item_feature_map = {r["item_name"]: r["proposed_feature"] for r in _read_csv("option_features.csv")}
    counts = {"pages": 0, "vehicle_feature": 0, "tech": 0, "option_codes": 0}
    for vehicle in vehicles:
        raw_ref = store_raw_file(FILES[vehicle])
        _, texts, _ = option_lists.extract(RAW_DATA_DIR / FILES[vehicle])
        pages = [{
            "record_id": f"option_list-{vehicle}-p{page}", "source_id": "option_list", "category": "E",
            "content_type": "reference", "level": "model", "url": f"{FILES[vehicle]}#page={page}", "raw_ref": raw_ref,
            "brand": "BMW", "chassis_code": vehicle, "vehicle_role": "current", "country": "DE", "market": "Europe",
            "language": "de", "title": f"Option list {vehicle}, page {page}", "text_original": text,
            "extra": {"edition": "July 2026", "page": page},
            "content_hash": hashlib.sha256(f"option_list|{vehicle}|{page}|{text}".encode()).hexdigest(),
            "run_id": run_id,
        } for page, text in texts.items() if text.strip()]
        counts["pages"] += audit.insert_many(conn, "source_record", pages, actor="seed:option_list",
                                             key="record_id", run_id=run_id,
                                             rationale="option-list page text (provided PDF)")
        rows = []
        codes = set()
        for r in _read_csv(f"option_list_{vehicle}.csv"):
            feature = item_feature_map.get(r["item_name"]) or ""
            rows.append({
                "vehicle": vehicle, "feature_id": feature_ids.get(feature), "variant": r["variant"], "market": "DE",
                "availability": r["availability"], "sa_code": r["sa_code"] or None, "item_name": r["item_name"],
                "price_eur": float(r["price_eur"]) if r["price_eur"] else None, "package": r["package"] or None,
                "record_id": f"option_list-{vehicle}-p{r['page']}",
            })
            if r["sa_code"] and feature:
                codes.add((feature, r["sa_code"]))
        counts["vehicle_feature"] += audit.insert_many(conn, "vehicle_feature", rows, actor="seed:option_list",
                                                       run_id=run_id, rationale="option-list grid (review CSV)")
        counts["option_codes"] += audit.insert_many(conn, "feature_mapping", [
            {"feature_id": feature_ids[f], "kind": "option_code", "value": code, "vehicle": vehicle}
            for f, code in sorted(codes) if f in feature_ids
        ], actor="seed:option_list", on_conflict_do_nothing=True, rationale="option code of a mapped item")
        tech = [{
            "record_id": f"option_list-{vehicle}-{r['variant'].replace(' ', '_')}-{r['metric'].replace(' ', '_')}",
            "source_id": "option_list", "category": "E", "url": f"{FILES[vehicle]}#page={r['page']}",
            "raw_ref": raw_ref, "brand": "BMW", "model": r["variant"], "chassis_code": vehicle, "country": "DE",
            "market": "Europe", "period": "2026-07", "metric": r["metric"], "value": float(r["value"]),
            "value_text": r["value_text"], "unit": r["unit"], "source_location": f"page {r['page']}: {r['line']}",
            "run_id": run_id,
        } for r in _read_csv(f"tech_data_{vehicle}.csv")]
        counts["tech"] += audit.insert_many(conn, "data_record", tech, actor="seed:option_list", key="record_id",
                                            run_id=run_id, on_conflict_do_nothing=True,
                                            rationale="customer-facing technical data (option list)")
    return counts
