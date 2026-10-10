"""What the fetch loop searches for: the BMW lines and their competitors, with
names, collision-safe search terms and site slugs (all from the database, never
hard-coded)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Subject:
    kind: str  # vehicle | competitor
    id: str  # vehicle code or competitor uuid
    brand: str
    model: str
    bmw_vehicle: str  # the BMW line the evidence belongs to
    chassis_code: str | None
    vehicle_role: str  # current | competitor
    markets: list[str]
    names: dict = field(default_factory=dict)
    terms: list[dict] = field(default_factory=list)
    slugs: dict[str, list[str]] = field(default_factory=dict)
    wikipedia_page: str | None = None
    priority: int = 0  # open cases first

    def safe_terms(self, language: str | None = None, single_brand: bool = False) -> list[str]:
        out = []
        for t in self.terms:
            if language and t["language"] != language:
                continue
            if t["scope"] == "single_brand_source" and not single_brand:
                continue
            if not t["collision_safe"] and not single_brand:
                continue
            out.append(t["term"])
        return list(dict.fromkeys(out))

    @property
    def label(self) -> str:
        return f"{self.brand} {self.model}"


WIKIPEDIA_PAGES = {"F70": "BMW 1 Series (F70)", "G60": "BMW 5 Series (G60)", "G70": "BMW 7 Series (G70)"}


def load(conn, vehicles: list[str] | None = None) -> list[Subject]:
    """Current BMW lines (optionally filtered) and their confirmed competitors."""
    open_cases = {r["vehicle"]: r["n"] for r in conn.execute(
        "select vehicle, count(*) n from case_vehicle where state in ('open', 'confirmed') group by vehicle").fetchall()}
    rows = conn.execute("select code, brand, series, markets_sold::text[] as markets from vehicle where is_current"
                        + (" and code = any(%s)" if vehicles else ""), (vehicles,) if vehicles else ()).fetchall()
    subjects = []
    for v in rows:
        subjects.append(Subject(
            kind="vehicle", id=v["code"], brand=v["brand"], model=v["series"], bmw_vehicle=v["code"],
            chassis_code=v["code"], vehicle_role="current", markets=v["markets"],
            wikipedia_page=WIKIPEDIA_PAGES.get(v["code"]), priority=open_cases.get(v["code"], 0) + 1000,
        ))
        for c in conn.execute("select id, brand, model, names, wikipedia_page, markets::text[] as markets"
                              " from competitor where bmw_vehicle = %s and confirmed and status <> 'ended'",
                              (v["code"],)).fetchall():
            subjects.append(Subject(
                kind="competitor", id=str(c["id"]), brand=c["brand"], model=c["model"], bmw_vehicle=v["code"],
                chassis_code=None, vehicle_role="competitor", markets=c["markets"], names=c["names"] or {},
                wikipedia_page=c["wikipedia_page"], priority=open_cases.get(v["code"], 0),
            ))
    for s in subjects:
        s.terms = [dict(t) for t in conn.execute(
            "select term, language, collision_safe, scope from search_term where subject_type = %s and subject_id = %s",
            (s.kind, s.id)).fetchall()]
        for r in conn.execute("select source_id, slug from site_slug where subject_type = %s and subject_id = %s",
                              (s.kind, s.id)).fetchall():
            s.slugs.setdefault(r["source_id"], []).append(r["slug"])
    return sorted(subjects, key=lambda s: -s.priority)


def author_hash(source_id: str, author: str | None) -> str | None:
    if not author:
        return None
    return hashlib.sha256(f"{source_id}|{author.strip().lower()}".encode()).hexdigest()[:24]
