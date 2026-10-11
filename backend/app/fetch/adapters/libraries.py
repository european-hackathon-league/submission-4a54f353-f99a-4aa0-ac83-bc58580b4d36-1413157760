"""Sources reached through a library instead of plain HTTP (fetching-sources 3.5,
3.7, 3.8): Google Play reviews (step 4), Google Trends (step 9), YouTube
transcripts (step 7).

The libraries talk to the sites directly, so their raw responses are stored by
this module (content-addressed, like the polite client) and every call counts
against the request budget. A missing library or key raises NotConfigured; the
worker reports it and moves on.
"""

from __future__ import annotations

import hashlib
import json
import time

from app import storage
from app.config import get_settings
from app.fetch.adapters.api import youtube_channels
from app.fetch.adapters.base import FetchContext, FetchResult, NotConfigured, adapter, cursor_key
from app.fetch.http import BudgetExhausted, Response
from app.fetch.records import data_record, text_record
from app.fetch.subjects import Subject, author_hash

# Google Play packages from fetching-sources.md 3.7 (brand level): (brand, package, countries).
PLAY_APPS = [
    ("BMW", "de.bmw.connected.mobile20.na", ["us"]),
    ("BMW", "de.bmw.connected.mobile20.row", ["gb", "de"]),
    ("Mercedes-Benz", "com.daimler.ris.mercedesme.ece.android", ["de", "gb"]),
    ("Audi", "de.myaudi.mobile.assistant", ["us", "de"]),
    ("Tesla", "com.teslamotors.tesla", ["us", "gb", "de"]),
]
TRENDS_GEOS = {"US": "USA", "DE": "Europe", "GB": "Europe"}


def _store(ctx: FetchContext, source_id: str, url: str, payload) -> Response:
    """Count the call and keep the library's raw answer, as the polite client does."""
    if ctx.client.used >= ctx.client.budget:
        raise BudgetExhausted(f"daily request budget of {ctx.client.budget} used")
    ctx.client.used += 1
    content = json.dumps(payload, default=str, ensure_ascii=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    raw_ref = f"{source_id}/{time.strftime('%Y-%m-%d', time.gmtime())}/{digest[:2]}/{digest}"
    if not storage.exists(raw_ref):
        storage.put(raw_ref, content, "application/json")
    return Response(url, 200, content, "application/json", raw_ref, time.time())


def fetch_google_play(ctx: FetchContext, source: dict, subjects: list[Subject], cursor: dict) -> FetchResult:
    try:
        from google_play_scraper import Sort, reviews
    except ImportError as exc:
        raise NotConfigured("google-play needs the google-play-scraper library") from exc
    out = FetchResult()
    for brand, package, countries in PLAY_APPS:
        targets = [s for s in subjects if s.brand == brand]
        if not targets:
            continue
        for country in countries:
            key = f"{package}-{country}"
            newest = cursor.get(key)
            token, top = None, None
            for _ in range(10 if ctx.backfill else 1):
                batch, token = reviews(package, lang="en" if country in ("us", "gb") else country, country=country,
                                       sort=Sort.NEWEST, count=100, continuation_token=token)
                url = f"https://play.google.com/store/apps/details?id={package}&gl={country}"
                r = _store(ctx, "google-play", url, batch)
                out.requests += 1
                stop = False
                for rev in batch:
                    rid = rev["reviewId"]
                    if newest and rid == newest and not ctx.backfill:
                        stop = True
                        break
                    top = top or rid
                    for s in targets if brand == "BMW" else targets[:1]:
                        out.records.append(text_record(
                            source=source, subject=s, record_id=f"google-play-{rid}-{s.id[:8]}", response=r,
                            text=rev.get("content") or "", url=url, category="H", content_type="owner",
                            level="brand", published_at=rev.get("at"), search_term=package,
                            author_hash=author_hash("google-play", rev.get("userName")), country=country.upper(),
                            rating_value=float(rev["score"]) if rev.get("score") else None, rating_scale=5,
                            engagement={"likes": rev.get("thumbsUpCount")},
                            extra={"package": package, "app_version": rev.get("reviewCreatedVersion")},
                            run_id=ctx.run_id))
                if stop or not token or not batch:
                    break
            if top:
                out.cursor[key] = top
    return out


def fetch_google_trends(ctx: FetchContext, source: dict, subjects: list[Subject], cursor: dict) -> FetchResult:
    """Monthly search interest (0..100) per BMW line and its competitors, per country.
    Trend context only: data records, market level."""
    try:
        from pytrends.request import TrendReq
    except ImportError as exc:
        raise NotConfigured("google-trends needs the pytrends library") from exc
    out = FetchResult()
    trends = TrendReq(hl="en-US", tz=0, timeout=(10, 30), retries=2, backoff_factor=2)
    for line in [s for s in subjects if s.kind == "vehicle"]:
        group = [line] + [s for s in subjects if s.kind == "competitor" and s.bmw_vehicle == line.id][:4]
        terms = [(s, (s.safe_terms("en") or [s.label])[0]) for s in group]
        for geo in TRENDS_GEOS:
            trends.build_payload([t for _, t in terms], timeframe="today 5-y", geo=geo)
            frame = trends.interest_over_time()
            url = f"https://trends.google.com/trends/explore?geo={geo}&q=" + ",".join(t for _, t in terms)
            r = _store(ctx, "google-trends", url, frame.reset_index().to_dict(orient="records") if len(frame) else [])
            out.requests += 1
            time.sleep(max(ctx.client.default_delay, 5))  # Trends rate-limits plain clients (HTTP 429)
            if frame.empty:
                continue
            for (s, term) in terms:
                for stamp, value in frame[term].items():
                    period = stamp.strftime("%Y-%m")
                    out.data_records.append(data_record(
                        source=source, subject=s, record_id=f"google-trends-{geo}-{s.id[:8]}-{period}", response=r,
                        metric="search interest", value=float(value), unit="index 0-100 (relative within the query)",
                        period=period, country=geo, category="F", source_location=f"query: {term}", url=url,
                        run_id=ctx.run_id))
    return out


@adapter("youtube-transcripts")
def youtube_transcripts(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    key = get_settings().youtube_api_key
    if not key:
        raise NotConfigured("youtube-transcripts needs YOUTUBE_API_KEY (video search)")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise NotConfigured("youtube-transcripts needs the youtube-transcript-api library") from exc
    out = FetchResult()
    term = (subject.safe_terms("en") or [subject.label])[0]
    seen = set(cursor.get(cursor_key(subject), []))
    api = YouTubeTranscriptApi()
    for channel in youtube_channels():
        r = ctx.client.get("https://www.googleapis.com/youtube/v3/search", source_id="youtube-transcripts", params={
            "part": "snippet", "channelId": channel, "q": term, "type": "video", "maxResults": 3, "key": key})
        out.requests += 1
        for item in r.json().get("items", []):
            vid = item["id"]["videoId"]
            if vid in seen:
                continue
            seen.add(vid)
            try:
                parts = api.fetch(vid, languages=["en", "de"]).to_raw_data()
            except Exception as exc:  # no transcript for this video
                out.notes.append(f"{vid}: no transcript ({type(exc).__name__})")
                continue
            url = f"https://www.youtube.com/watch?v={vid}"
            tr = _store(ctx, "youtube-transcripts", url, parts)
            text = " ".join(p["text"] for p in parts)
            out.records.append(text_record(
                source=source, subject=subject, record_id=f"youtube-transcripts-{vid}", response=tr, text=text, url=url,
                category="D", content_type="expert", title=item["snippet"].get("title"),
                published_at=item["snippet"].get("publishedAt"), search_term=term, thread_id=vid,
                extra={"channel_id": channel}, run_id=ctx.run_id))
    out.cursor[cursor_key(subject)] = sorted(seen)[-5000:]
    return out


SHARED = {"google-play": fetch_google_play, "google-trends": fetch_google_trends}
