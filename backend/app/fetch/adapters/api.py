"""API sources: wikipedia, wikidata, nhtsa-vpic, fueleconomy, nhtsa-complaints,
nhtsa-recalls (keyless) and reddit, youtube-comments (credentials needed)."""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from app.config import get_settings
from app.fetch.adapters.base import FetchContext, FetchResult, NotConfigured, adapter, cursor_key
from app.fetch.records import data_record, parse_date, text_record
from app.fetch.subjects import Subject, author_hash

THIS_YEAR = dt.date.today().year


def _model_years(subject: Subject) -> list[int]:
    return list(range(THIS_YEAR - 3, THIS_YEAR + 1))


@adapter("wikipedia")
def wikipedia(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    if not subject.wikipedia_page:
        return out
    r = ctx.client.get("https://en.wikipedia.org/w/api.php", source_id="wikipedia", params={
        "action": "query", "prop": "extracts|revisions", "rvprop": "ids|timestamp", "explaintext": 1,
        "format": "json", "titles": subject.wikipedia_page})
    out.requests += 1
    for page in r.json().get("query", {}).get("pages", {}).values():
        if "missing" in page or not page.get("extract"):
            continue
        rev = (page.get("revisions") or [{}])[0]
        rev_id = str(rev.get("revid", "0"))
        if cursor.get(cursor_key(subject)) == rev_id and not ctx.backfill:
            continue
        out.records.append(text_record(
            source=source, subject=subject, record_id=f"wikipedia-{page['pageid']}-r{rev_id}", response=r,
            text=page["extract"], url=f"https://en.wikipedia.org/wiki/{quote(page['title'].replace(' ', '_'))}",
            category="J", content_type="reference", level="model", title=page["title"],
            published_at=rev.get("timestamp"), search_term=subject.wikipedia_page, run_id=ctx.run_id))
        out.cursor[cursor_key(subject)] = rev_id
    return out


@adapter("wikidata")
def wikidata(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    r = ctx.client.get("https://www.wikidata.org/w/api.php", source_id="wikidata", params={
        "action": "wbsearchentities", "search": subject.label, "language": "en", "format": "json", "limit": 3})
    out.requests += 1
    for hit in r.json().get("search", [])[:1]:
        out.data_records.append(data_record(
            source=source, subject=subject, record_id=f"wikidata-{hit['id']}", response=r, metric="wikidata entity",
            value_text=f"{hit['id']}: {hit.get('label', '')} - {hit.get('description', '')}", category="J",
            source_location="wbsearchentities", run_id=ctx.run_id))
    return out


@adapter("nhtsa-vpic")
def vpic(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    if "USA" not in subject.markets:
        return out
    for year in _model_years(subject):
        r = ctx.client.get(f"https://vpic.nhtsa.dot.gov/api/vehicles/GetModelsForMakeYear/make/"
                           f"{quote(subject.brand)}/modelyear/{year}", source_id="nhtsa-vpic", params={"format": "json"})
        out.requests += 1
        for m in r.json().get("Results", []):
            out.data_records.append(data_record(
                source=source, subject=subject, record_id=f"nhtsa-vpic-{subject.brand}-{year}-{m['Model_ID']}",
                response=r, metric="US model name", value_text=m["Model_Name"], period=str(year), country="US",
                category="J", source_location="GetModelsForMakeYear", run_id=ctx.run_id))
    return out


@adapter("fueleconomy")
def fueleconomy(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    if "USA" not in subject.markets:
        return out
    for year in _model_years(subject):
        r = ctx.client.get("https://www.fueleconomy.gov/ws/rest/vehicle/menu/model", source_id="fueleconomy",
                           params={"year": year, "make": subject.brand}, headers={"Accept": "application/json"})
        out.requests += 1
        items = (r.json() or {}).get("menuItem", []) if r.status == 200 else []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            out.data_records.append(data_record(
                source=source, subject=subject, record_id=f"fueleconomy-{subject.brand}-{year}-{item['value']}",
                response=r, metric="US variant", value_text=item["text"], period=str(year), country="US",
                category="J", source_location="vehicle/menu/model", run_id=ctx.run_id))
    return out


def nhtsa_names(ctx: FetchContext, subject: Subject, year: int) -> list[str]:
    """NHTSA's own model names change by year; they are looked up before each fetch."""
    r = ctx.client.get("https://api.nhtsa.gov/products/vehicle/models", source_id="nhtsa-complaints",
                       params={"modelYear": year, "make": subject.brand, "issueType": "c"})
    names = [m["model"] for m in r.json().get("results", [])]
    keys = [subject.model.upper().replace(" SERIES", " SERIES"), *(t.upper().replace("BMW ", "")
                                                                    for t in subject.safe_terms("en"))]
    return [n for n in names if any(k and (n.startswith(k.split(" ")[0]) or k in n) for k in keys)]


@adapter("nhtsa-complaints")
def nhtsa_complaints(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    if "USA" not in subject.markets:
        return out
    seen = set(cursor.get(cursor_key(subject), []))
    for year in _model_years(subject):
        for name in nhtsa_names(ctx, subject, year):
            r = ctx.client.get("https://api.nhtsa.gov/complaints/complaintsByVehicle", source_id="nhtsa-complaints",
                               params={"make": subject.brand, "model": name, "modelYear": year})
            out.requests += 2
            for c in r.json().get("results", []):
                odi = str(c["odiNumber"])
                if odi in seen and not ctx.backfill:
                    continue
                seen.add(odi)
                out.records.append(text_record(
                    source=source, subject=subject, record_id=f"nhtsa-complaints-{odi}", response=r,
                    text=c.get("summary") or "", url=f"https://www.nhtsa.gov/vehicle/{year}/{subject.brand}/{name}",
                    category="A", content_type="owner", title=c.get("components"), model_year=str(year),
                    published_at=parse_date(c.get("dateComplaintFiled")), search_term=name, country="US",
                    extra={"components": c.get("components"), "crash": c.get("crash"), "fire": c.get("fire"),
                           "dateOfIncident": c.get("dateOfIncident"), "nhtsa_model": name},
                    run_id=ctx.run_id))
    out.cursor[cursor_key(subject)] = sorted(seen)
    out.records = [r for r in out.records if r["text_original"]]
    return out


@adapter("nhtsa-recalls")
def nhtsa_recalls(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    if "USA" not in subject.markets:
        return out
    for year in _model_years(subject):
        for name in nhtsa_names(ctx, subject, year):
            r = ctx.client.get("https://api.nhtsa.gov/recalls/recallsByVehicle", source_id="nhtsa-recalls",
                               params={"make": subject.brand, "model": name, "modelYear": year})
            out.requests += 2
            for c in r.json().get("results", []):
                text = " ".join(x for x in (c.get("Summary"), c.get("Consequence"), c.get("Remedy")) if x)
                if not text:
                    continue
                out.records.append(text_record(
                    source=source, subject=subject, record_id=f"nhtsa-recalls-{c['NHTSACampaignNumber']}-{name}",
                    response=r, text=text, url=f"https://www.nhtsa.gov/recalls?nhtsaId={c['NHTSACampaignNumber']}",
                    category="A", content_type="press", title=c.get("Component"), model_year=str(year),
                    published_at=parse_date(c.get("ReportReceivedDate", "")[:10].replace("/", "-") or None),
                    search_term=name, country="US",
                    extra={"over_the_air": c.get("overTheAirUpdate"), "component": c.get("Component")},
                    run_id=ctx.run_id))
    return out


@adapter("reddit")
def reddit(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    s = get_settings()
    if not (s.reddit_client_id and s.reddit_client_secret):
        raise NotConfigured("reddit needs REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (OAuth app)")
    import httpx

    token = httpx.post("https://www.reddit.com/api/v1/access_token", auth=(s.reddit_client_id, s.reddit_client_secret),
                       data={"grant_type": "client_credentials"},
                       headers={"User-Agent": s.fetch_user_agent}, timeout=30).json()["access_token"]
    headers = {"Authorization": f"bearer {token}"}
    out = FetchResult()
    newest = cursor.get(cursor_key(subject), 0)
    top = newest
    subreddits = ["BMW", "cars", "electricvehicles", "whatcarshouldIbuy", "mercedes_benz", "Audi", "TeslaLounge",
                  "CarTalkUK"]
    for sub in subreddits:
        for term in subject.safe_terms("en")[:2]:
            after = None
            for _ in range(ctx.max_pages if ctx.backfill else 2):
                r = ctx.client.get(f"https://oauth.reddit.com/r/{sub}/search", source_id="reddit", headers=headers,
                                   params={"q": term, "restrict_sr": 1, "sort": "new", "limit": 100, "after": after})
                out.requests += 1
                data = r.json().get("data", {})
                stop = False
                for child in data.get("children", []):
                    p = child["data"]
                    if p["created_utc"] <= newest and not ctx.backfill:
                        stop = True
                        break
                    top = max(top, p["created_utc"])
                    text = "\n\n".join(x for x in (p.get("title"), p.get("selftext")) if x)
                    out.records.append(text_record(
                        source=source, subject=subject, record_id=f"reddit-{p['id']}", response=r, text=text,
                        url=f"https://www.reddit.com{p['permalink']}", category="C", content_type="owner",
                        title=p.get("title"), published_at=dt.datetime.fromtimestamp(p["created_utc"], dt.timezone.utc),
                        search_term=term, thread_id=p["id"], author_hash=author_hash("reddit", p.get("author")),
                        engagement={"upvotes": p.get("ups"), "replies": p.get("num_comments")},
                        extra={"subreddit": sub}, run_id=ctx.run_id))
                after = data.get("after")
                if stop or not after:
                    break
    out.cursor[cursor_key(subject)] = top
    return out


def youtube_channels() -> list[str]:
    """Channel IDs of fetching-sources.md 3.5 (read from each channel page on 2026-10-10)."""
    return ["UCcftUjNsimynCrKGFolLRKQ", "UCG72WbiCvdB6JKU-3YRP8Kg", "UCyXiDU5qjfOPxgOPeFWGwKw",
            "UCgUvk6jVaf-1uKOqG8XNcaQ", "UCyIzTVZNIAMY9JhzKc-rriQ", "UCoNMyBdrXe8ueYEXhusMyIw",
            "UC9QHoTXvd3xwgUK991aP4RA", "UCZTkJpxZkNEuTnDqU12-kiQ", "UCjWs7BxyjO5SLqevxSmp4vQ",
            "UC-4H50_RIqgXol2GlEdhxQQ"]


@adapter("youtube-comments")
def youtube_comments(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    key = get_settings().youtube_api_key
    if not key:
        raise NotConfigured("youtube-comments needs YOUTUBE_API_KEY (YouTube Data API v3)")
    out = FetchResult()
    channels = youtube_channels()
    term = (subject.safe_terms("en") or [subject.label])[0]
    seen = set(cursor.get(cursor_key(subject), []))
    for channel in channels:
        r = ctx.client.get("https://www.googleapis.com/youtube/v3/search", source_id="youtube-comments", params={
            "part": "snippet", "channelId": channel, "q": term, "type": "video", "maxResults": 5, "key": key})
        out.requests += 1
        for item in r.json().get("items", []):
            vid = item["id"]["videoId"]
            page = None
            for _ in range(ctx.max_pages if ctx.backfill else 1):
                c = ctx.client.get("https://www.googleapis.com/youtube/v3/commentThreads", source_id="youtube-comments",
                                   params={"part": "snippet", "videoId": vid, "maxResults": 100, "key": key,
                                           "order": "time", "pageToken": page})
                out.requests += 1
                body = c.json()
                for t in body.get("items", []):
                    top = t["snippet"]["topLevelComment"]
                    if top["id"] in seen:
                        continue
                    sn = top["snippet"]
                    seen.add(top["id"])
                    out.records.append(text_record(
                        source=source, subject=subject, record_id=f"youtube-comments-{top['id']}", response=c,
                        text=sn.get("textOriginal") or "", url=f"https://www.youtube.com/watch?v={vid}&lc={top['id']}",
                        category="C", content_type="owner", title=item["snippet"].get("title"),
                        published_at=sn.get("publishedAt"), search_term=term, thread_id=vid,
                        author_hash=author_hash("youtube", sn.get("authorChannelId", {}).get("value")),
                        engagement={"likes": sn.get("likeCount"), "replies": t["snippet"].get("totalReplyCount")},
                        extra={"channel_id": channel}, run_id=ctx.run_id))
                page = body.get("nextPageToken")
                if not page:
                    break
    out.cursor[cursor_key(subject)] = sorted(seen)[-5000:]
    return out
