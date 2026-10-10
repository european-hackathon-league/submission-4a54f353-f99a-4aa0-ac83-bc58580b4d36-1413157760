"""Record builders for fetching-sources.md section 7 (text records 7.2, data
records 7.3) and the country -> market mapping of 7.4."""

from __future__ import annotations

import datetime as dt
import hashlib
import re

from app.fetch.http import Response
from app.fetch.subjects import Subject
from app.seed.loaders import country_to_market

_SPACE = re.compile(r"\s+")


def content_hash(source_id: str, text: str) -> str:
    """Duplicate-detection hash (not unique: identical wording by different
    authors are different records)."""
    return hashlib.sha256(f"{source_id}|{_SPACE.sub(' ', text).strip().lower()}".encode()).hexdigest()


def parse_date(value) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y",
                "%Y%m%d", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            d = dt.datetime.strptime(text, fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    try:
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def text_record(*, source: dict, subject: Subject | None, record_id: str, response: Response, text: str,
                url: str, category: str, content_type: str, level: str = "model", title: str | None = None,
                published_at=None, search_term: str | None = None, thread_id: str | None = None,
                author_hash: str | None = None, country: str | None = None, language: str = "en",
                rating_value=None, rating_scale=None, engagement: dict | None = None, extra: dict | None = None,
                model_year: str | None = None, powertrain: str = "unknown", run_id=None) -> dict:
    return {
        "record_id": record_id[:200], "source_id": source["source_id"], "category": category,
        "content_type": content_type, "level": level, "url": url, "raw_ref": response.raw_ref,
        "fetched_at": dt.datetime.fromtimestamp(response.fetched_at, dt.timezone.utc),
        "published_at": parse_date(published_at), "search_term": search_term, "thread_id": thread_id,
        "author_hash": author_hash,
        "brand": subject.brand if subject and level == "model" else (subject.brand if subject else None),
        "model": subject.model if subject and level == "model" else None,
        "chassis_code": subject.chassis_code if subject else None, "model_year": model_year,
        "vehicle_role": subject.vehicle_role if subject else "unknown", "powertrain": powertrain,
        "country": country, "market": country_to_market(country), "language": language, "title": title,
        "text_original": text, "text_en": None, "rating_value": rating_value, "rating_scale": rating_scale,
        "engagement": engagement or {}, "extra": {**(extra or {}), **({"bmw_vehicle": subject.bmw_vehicle,
                                                                      "subject_id": subject.id} if subject else {})},
        "content_hash": content_hash(source["source_id"], text), "run_id": run_id,
    }


def data_record(*, source: dict, subject: Subject | None, record_id: str, response: Response, metric: str,
                value=None, value_text: str | None = None, unit: str | None = None, period: str | None = None,
                country: str | None = None, category: str = "J", source_location: str | None = None,
                url: str | None = None, run_id=None) -> dict:
    return {
        "record_id": record_id[:200], "source_id": source["source_id"], "category": category,
        "url": url or response.url, "raw_ref": response.raw_ref,
        "fetched_at": dt.datetime.fromtimestamp(response.fetched_at, dt.timezone.utc),
        "brand": subject.brand if subject else None, "model": subject.model if subject else None,
        "chassis_code": subject.chassis_code if subject else None,
        "competitor_id": subject.id if subject and subject.kind == "competitor" else None,
        "country": country, "market": country_to_market(country), "period": period, "metric": metric,
        "value": value, "value_text": value_text if value is None else (value_text or None), "unit": unit,
        "source_location": source_location, "run_id": run_id,
    }
