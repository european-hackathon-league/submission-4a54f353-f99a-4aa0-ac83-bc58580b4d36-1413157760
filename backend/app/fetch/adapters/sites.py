"""Scraped sources of fetch steps 1 to 9 that the first adapters did not cover.

- Forums (step 5): ``motor-talk`` (German, one board per BMW line) and
  ``pistonheads`` (BMW General board, threads kept when the title names the line).
  One record per post.
- Complaints (step 6): ``carcomplaints``: variant page -> model years -> problem
  pages -> one record per owner complaint.
- Model pages (steps 1, 6, 7, 9): ``ams``, ``autobild``, ``adac-autotest``,
  ``autoscout24``, ``auto-data``, ``ev-database``, ``goodcarbadcar``: the page of
  the subject's slug, plus (for the magazines) the articles it links to that name
  the model.
- Newsrooms and market pages (steps 3, 7, 9), shared by all subjects: the start
  page of the site; linked articles are kept only when their link text or URL
  names a subject (collision-safe terms), market pages are one record each.

Every page goes through the polite client (robots.txt, delay, budget, raw
response stored first). Page text is data for the agents, never instructions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from app.fetch.adapters.base import FetchContext, FetchResult, adapter, cursor_key
from app.fetch.adapters.scrape import article_text
from app.fetch.records import text_record
from app.fetch.subjects import Subject, author_hash

_WS = re.compile(r"\s+")
MAX_ARTICLES = 10  # per site and run (incremental); a backfill takes up to ctx.max_pages
INCREMENTAL_THREADS = 10  # forum threads per board and daily run
GERMAN_MONTHS = {m: i for i, m in enumerate(["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
                                              "september", "oktober", "november", "dezember"], start=1)}
ENGLISH_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                              "nov", "dec"], start=1)}


def _tree(text: str) -> LexborHTMLParser:
    return LexborHTMLParser(text)


def _text(node) -> str:
    return _WS.sub(" ", node.text(separator=" ")).strip() if node else ""


def _slugify(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def match_terms(subject: Subject, single_brand: bool = False) -> list[str]:
    """Lower-case collision-safe terms plus their URL forms (``bmw-5-series``)."""
    terms = subject.safe_terms(single_brand=single_brand) or [subject.label]
    out = []
    for t in terms:
        out += [t.lower(), _slugify(t)]
    return [t for t in dict.fromkeys(out) if len(t) >= 3]


def names_subject(text: str, terms: list[str]) -> str | None:
    lowered = text.lower()
    for t in terms:
        if re.search(r"(?<![\w])" + re.escape(t) + r"(?![\w])", lowered):
            return t
    return None


def _record_id(source_id: str, url: str) -> str:
    return f"{source_id}-{hashlib.sha256(url.encode()).hexdigest()[:16]}"


# ---- dates ---------------------------------------------------------------------

def german_date(text: str, today: dt.date | None = None) -> dt.datetime | None:
    """motor-talk: 'am 10. Oktober 2026 um 10:55 Uhr', 'heute um 09:12 Uhr', 'gestern um ...'."""
    today = today or dt.date.today()
    t = text.lower()
    time = re.search(r"(\d{1,2}):(\d{2})", t)
    hh, mm = (int(time.group(1)), int(time.group(2))) if time else (0, 0)
    if "heute" in t or "gestern" in t:
        d = today - dt.timedelta(days=1 if "gestern" in t else 0)
    else:
        m = re.search(r"(\d{1,2})\.\s*([a-zäöü]+)\s+(\d{4})", t)
        if not m or m.group(2) not in GERMAN_MONTHS:
            return None
        d = dt.date(int(m.group(3)), GERMAN_MONTHS[m.group(2)], int(m.group(1)))
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=dt.timezone.utc)


def english_date(text: str, today: dt.date | None = None) -> dt.datetime | None:
    """pistonheads 'Monday 14th April 2025', 'Monday 5th October' (this year), 'Yesterday (22:15)';
    carcomplaints 'Sep 20 2016'."""
    today = today or dt.date.today()
    t = text.lower()
    if t.startswith("today") or t.startswith("yesterday"):
        d = today - dt.timedelta(days=1 if t.startswith("yesterday") else 0)
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?", t)
    if m and m.group(2)[:3] in ENGLISH_MONTHS:
        day, month, year = int(m.group(1)), ENGLISH_MONTHS[m.group(2)[:3]], int(m.group(3) or today.year)
    else:
        m = re.search(r"([a-z]{3})[a-z]*\s+(\d{1,2})\s*(\d{4})", t)
        if not m or m.group(1) not in ENGLISH_MONTHS:
            return None
        day, month, year = int(m.group(2)), ENGLISH_MONTHS[m.group(1)], int(m.group(3))
    try:
        return dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ---- motor-talk ----------------------------------------------------------------

def parse_motortalk_list(html: str, base: str) -> list[dict]:
    seen, threads = set(), []
    for a in _tree(html).css("a"):
        href = a.attributes.get("href") or ""
        m = re.search(r"^(?:https://www\.motor-talk\.de)?/forum/[^/]+-t(\d+)\.html$", href)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            threads.append({"id": m.group(1), "url": urljoin(base, href), "title": _text(a)})
    return threads


def parse_motortalk_thread(html: str) -> tuple[str | None, list[dict], int]:
    tree = _tree(html)
    title = _text(tree.css_first("h1")) or None
    posts = []
    for sec in tree.css("section.Post"):
        body = sec.css_first(".PostBody")
        for quote in (body.css("blockquote") if body else []):
            quote.decompose()
        text = _text(body)
        if not text:
            continue
        nick = sec.css_first(".PostHeader-nickName")
        meta = sec.css_first(".PostHeader-meta .Typography")
        posts.append({"id": (sec.attributes.get("id") or "").removeprefix("post"), "author": _text(nick) or None,
                      "text": text, "published": german_date(_text(meta)) if meta else None})
    pages = [int(p) for p in re.findall(r"\.html\?page=(\d+)", html)]
    return title, posts, max(pages or [1])


@adapter("motor-talk")
def motor_talk(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    out = FetchResult()
    base = "https://www.motor-talk.de"
    for board in subject.slugs.get("motor-talk", []):
        list_pages = ctx.max_pages if ctx.backfill else 1
        for page in range(1, list_pages + 1):
            url = f"{base}/forum/{board}.html" + (f"?page={page}" if page > 1 else "")
            r = ctx.client.get(url, source_id="motor-talk")
            out.requests += 1
            threads = parse_motortalk_list(r.text, base) if r.status == 200 else []
            if not threads:
                break
            # The board lists threads by latest activity: a daily run reads the 10 most active
            # threads, first page and (if longer) the last page where new posts land.
            for t in threads if ctx.backfill else threads[:INCREMENTAL_THREADS]:
                turl, tpage, last = t["url"], 1, 1
                while tpage <= last and tpage <= ctx.max_pages:
                    tr = ctx.client.get(turl + (f"?page={tpage}" if tpage > 1 else ""), source_id="motor-talk")
                    out.requests += 1
                    title, posts, last = parse_motortalk_thread(tr.text)
                    if not ctx.backfill and tpage == 1 and last > 2:
                        tpage = last - 1  # jump to the last page (incremented below)
                    for p in posts:
                        out.records.append(text_record(
                            source=source, subject=subject, record_id=f"motor-talk-post-{p['id']}", response=tr,
                            text=p["text"], url=f"{turl}#post{p['id']}", category="C", content_type="owner",
                            title=title or t["title"], published_at=p["published"], search_term=board,
                            thread_id=t["id"], author_hash=author_hash("motor-talk", p["author"]), country="DE",
                            language="de", run_id=ctx.run_id))
                    tpage += 1
    return out


# ---- pistonheads ---------------------------------------------------------------

def parse_pistonheads_list(html: str, base: str) -> list[dict]:
    threads = []
    for li in _tree(html).css("ul.topic-list li.topic"):
        links = [a for a in li.css("h3.title a") if _text(a)]
        if not links:
            continue
        a = links[-1]
        m = re.search(r"t=(\d+)", a.attributes.get("href") or "")
        if m:
            threads.append({"id": m.group(1), "title": _text(a),
                            "url": urljoin(base, f"topic.asp?h=0&f=72&t={m.group(1)}"),
                            "latest": english_date(_text(li.css_first(".date")))})
    return threads


def parse_pistonheads_topic(html: str) -> list[dict]:
    posts = []
    for msg in _tree(html).css("div.topic-reply"):
        body = msg.css_first(".msg-body")
        for quote in (body.css("blockquote, .quote") if body else []):
            quote.decompose()
        text = _text(body)
        anchor = msg.css_first(".msg-header a[name]")
        if text and anchor:
            posts.append({"id": anchor.attributes["name"], "author": _text(msg.css_first(".author")) or None,
                          "text": text, "published": english_date(_text(msg.css_first(".timestamp")))})
    return posts


@adapter("pistonheads")
def pistonheads(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    """The BMW General board is single-brand: bare model names count (fetching-sources 2.4)."""
    out = FetchResult()
    if subject.kind != "vehicle" or not subject.slugs.get("pistonheads"):
        return out
    base = "https://www.pistonheads.com/gassing/"
    terms = match_terms(subject, single_brand=True)
    for board in subject.slugs["pistonheads"]:
        for page in range(ctx.max_pages if ctx.backfill else 2):
            r = ctx.client.get(f"{base}forum.asp?h=0&f={board}&i={page * 20}", source_id="pistonheads")
            out.requests += 1
            threads = parse_pistonheads_list(r.text, base) if r.status == 200 else []
            if not threads:
                break
            for t in threads:
                term = names_subject(t["title"], terms)
                if not term:
                    continue
                for offset in range(0, 20 * (ctx.max_pages if ctx.backfill else 1), 20):
                    tr = ctx.client.get(f"{t['url']}&i={offset}", source_id="pistonheads")
                    out.requests += 1
                    posts = parse_pistonheads_topic(tr.text)
                    for p in posts:
                        out.records.append(text_record(
                            source=source, subject=subject, record_id=f"pistonheads-post-{p['id']}", response=tr,
                            text=p["text"], url=f"{t['url']}#{p['id']}", category="C", content_type="owner",
                            title=t["title"], published_at=p["published"], search_term=term, thread_id=t["id"],
                            author_hash=author_hash("pistonheads", p["author"]), country="GB", run_id=ctx.run_id))
                    if len(posts) < 20:
                        break
    return out


# ---- carcomplaints -------------------------------------------------------------

def parse_cc_links(html: str, prefix: str) -> list[str]:
    return sorted(set(re.findall(r'href="(' + re.escape(prefix) + r'[^"#]+)"', html)))


def parse_cc_problem(html: str) -> tuple[str | None, list[dict]]:
    tree = _tree(html)
    title = _text(tree.css_first("h1")) or None
    out = []
    for c in tree.css("div.complaint"):
        comments = c.css_first(".comments")
        if comments is None:
            continue
        for bad in comments.css(".ad, script, .userinfo"):
            bad.decompose()
        text = _text(comments)
        if not text:
            continue
        user = c.css_first(".userinfo strong")
        place = c.css_first(".userinfo span")
        header = c.css_first(".cheader ul")
        out.append({"id": (c.attributes.get("id") or "").removeprefix("p"), "text": text,
                    "author": _text(user) or None, "place": _text(place) or None,
                    "published": english_date(_text(c.css_first(".pdate"))),
                    "details": _text(header) or None})
    return title, out


@adapter("carcomplaints")
def carcomplaints(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    """Only model years of the current generation for BMW lines (the variant pages
    span generations); the last four model years for competitors."""
    out = FetchResult()
    first_year = int(cursor.get(f"first_year-{subject.id}", 0)) or (dt.date.today().year - 3)
    if subject.kind == "vehicle" and subject.on_sale_since:
        first_year = subject.on_sale_since.year
    seen = set(cursor.get(cursor_key(subject), []))
    for slug in subject.slugs.get("carcomplaints", []):
        root = f"/{slug.strip('/')}/"
        r = ctx.client.get(f"https://www.carcomplaints.com{root}", source_id="carcomplaints")
        out.requests += 1
        years = sorted({int(m) for m in re.findall(re.escape(root) + r"(\d{4})/", r.text)})
        for year in [y for y in years if y >= first_year]:
            yr = ctx.client.get(f"https://www.carcomplaints.com{root}{year}/", source_id="carcomplaints")
            out.requests += 1
            problems = [p for p in parse_cc_links(yr.text, f"{root}{year}/") if p.endswith(".shtml")]
            for path in problems:
                if path in seen and not ctx.backfill:
                    continue
                pr = ctx.client.get(f"https://www.carcomplaints.com{path}", source_id="carcomplaints")
                out.requests += 1
                title, complaints = parse_cc_problem(pr.text)
                category = path.split("/")[-2]
                for c in complaints:
                    out.records.append(text_record(
                        source=source, subject=subject, record_id=f"carcomplaints-{path.strip('/')}-{c['id']}",
                        response=pr, text=c["text"], url=f"https://www.carcomplaints.com{path}#{int(c['id']) + 1}"
                        if c["id"].isdigit() else f"https://www.carcomplaints.com{path}", category="A",
                        content_type="owner", title=title, published_at=c["published"], search_term=slug,
                        author_hash=author_hash("carcomplaints", c["author"]), country="US", model_year=str(year),
                        extra={"problem_category": category, "details": c["details"], "place": c["place"]},
                        run_id=ctx.run_id))
                seen.add(path)
    out.cursor[cursor_key(subject)] = sorted(seen)[-2000:]
    return out


# ---- model pages ---------------------------------------------------------------

MODEL_PAGES = {
    # source: (url pattern, country, language, category, content type, follow linked articles)
    "ams": ("https://www.auto-motor-und-sport.de/marken-modelle/{slug}/", "DE", "de", "D", "expert", True),
    "autobild": ("https://www.autobild.de/marken-modelle/{slug}/", "DE", "de", "D", "expert", True),
    "adac-autotest": ("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/{slug}/", "DE", "de", "D",
                      "expert", True),
    "autoscout24": ("https://www.autoscout24.de/auto/{slug}/", "DE", "de", "B", "owner", True),
    "auto-data": ("https://www.auto-data.net/en/{slug}", None, "en", "J", "expert", False),
    "ev-database": ("https://ev-database.org/car/{slug}", None, "en", "J", "expert", False),
    "goodcarbadcar": ("https://www.goodcarbadcar.net/{slug}-sales-figures/", "US", "en", "F", "news", False),
}


def article_links(html: str, page_url: str, terms: list[str], path_hint: str | None = None) -> list[str]:
    """Same-host links whose text or URL names the subject (or contains the model
    slug), without fragments, the page itself or obvious navigation."""
    host = urlsplit(page_url).netloc
    out = []
    for a in _tree(html).css("a"):
        href = (a.attributes.get("href") or "").split("#")[0]
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        url = urljoin(page_url, href)
        parts = urlsplit(url)
        if parts.netloc != host or url.rstrip("/") == page_url.rstrip("/") or parts.path.count("/") < 2:
            continue
        label = f"{_text(a)} {parts.path.replace('-', ' ')} {parts.path}"
        if names_subject(label, terms) or (path_hint and path_hint in parts.path.lower()
                                           and parts.path.rstrip("/").lower() != path_hint):
            out.append(url)
    return list(dict.fromkeys(out))


def _model_pages(ctx: FetchContext, source: dict, subject: Subject, cursor: dict) -> FetchResult:
    sid = source["source_id"]
    pattern, country, language, category, content_type, follow = MODEL_PAGES[sid]
    out = FetchResult()
    seen = set(cursor.get(cursor_key(subject), []))
    terms = match_terms(subject)
    for slug in subject.slugs.get(sid, []):
        url = pattern.format(slug=slug)
        r = ctx.client.get(url, source_id=sid)
        out.requests += 1
        if r.status != 200:
            out.notes.append(f"{url}: HTTP {r.status}")
            continue
        title, text = article_text(r.text)
        day = dt.date.today().isoformat()
        if len(text) >= 200:  # the page itself, versioned by day (figures and ratings change)
            out.records.append(text_record(
                source=source, subject=subject, record_id=f"{_record_id(sid, url)}-{day}", response=r, text=text,
                url=url, category=category, content_type=content_type, title=title, published_at=None,
                search_term=slug, country=country, language=language, run_id=ctx.run_id))
        if not follow:
            continue
        links = [u for u in article_links(r.text, url, terms, "/" + slug.split("/")[-1].lower())
                 if u not in seen][: ctx.max_pages if ctx.backfill else MAX_ARTICLES]
        for link in links:
            ar = ctx.client.get(link, source_id=sid)
            out.requests += 1
            seen.add(link)
            atitle, atext = article_text(ar.text)
            if ar.status != 200 or len(atext) < 400:
                continue
            out.records.append(text_record(
                source=source, subject=subject, record_id=_record_id(sid, link), response=ar, text=atext, url=link,
                category="D", content_type="expert", title=atitle, published_at=_published(ar.text),
                search_term=slug, country=country, language=language, run_id=ctx.run_id))
    out.cursor[cursor_key(subject)] = sorted(seen)[-2000:]
    return out


adapter(*MODEL_PAGES)(_model_pages)


def _published(html: str) -> str | None:
    m = re.search(r'(?:article:published_time|datePublished)["\']?\s*(?:content=|:)\s*["\']([^"\']+)', html)
    return m.group(1) if m else None


# ---- newsrooms and market pages (shared) ---------------------------------------

NEWSROOMS = {
    # source: (start pages, country, language, category, content type)
    "press-mercedes": (["https://media.mercedes-benz.com"], None, "en", "J", "press"),
    "press-audi": (["https://www.audi.com/en/press-releases"], None, "en", "J", "press"),
    "press-genesis": (["https://www.genesisnewsusa.com"], "US", "en", "J", "press"),
    "press-lexus": (["https://pressroom.lexus.com"], "US", "en", "J", "press"),
    "press-porsche": (["https://newsroom.porsche.com/en.html"], None, "en", "J", "press"),
    "autocar-new-cars": (["https://www.autocar.co.uk/car-news/new-cars"], "GB", "en", "J", "news"),
    "caranddriver-future": (["https://www.caranddriver.com/future-cars/"], "US", "en", "J", "news"),
    "autoexpress-news": (["https://www.autoexpress.co.uk/news"], "GB", "en", "J", "news"),
    "automobile-magazine": (["https://www.automobile-magazine.fr/"], "FR", "fr", "D", "expert"),
    "teknikens-varld": (["https://teknikensvarld.expressen.se/", "https://teknikensvarld.expressen.se/test-provkorningar/"],
                        "SE", "sv", "D", "expert"),
    "byd-site": (["https://www.byd.com/eu/news-list"], None, "en", "J", "press"),
    "nio-site": (["https://www.nio.com/news"], None, "en", "J", "press"),
    "xiaomi-ev-site": (["https://www.xiaomiev.com"], "CN", "zh", "J", "press"),
    "zeekr-site": (["https://www.zeekrlife.com"], "CN", "zh", "J", "press"),
    "autohome-new": (["https://www.autohome.com.cn/newbrand/"], "CN", "zh", "J", "news"),
    "dongchedi-new": (["https://www.dongchedi.com/newcar"], "CN", "zh", "J", "news"),
    "iaa": (["https://www.iaa-mobility.com/en"], "DE", "en", "I", "news"),
    "ces": (["https://www.ces.tech"], "US", "en", "I", "news"),
    "auto-china": (["https://www.autochinashow.org"], "CN", "en", "I", "news"),
}
MARKET_PAGES = {
    # source: (page, country, language, category); one record per page and day
    "smmt": ("https://www.smmt.co.uk/vehicle-data/car-registrations/", "GB", "en", "F"),
    "carfigures": ("https://carfigures.com", None, "en", "F"),
    "cox-automotive": ("https://www.coxautoinc.com", "US", "en", "F"),
    "pfa": ("https://pfa-auto.fr", "FR", "fr", "F"),
    "unrae": ("https://unrae.it", "IT", "it", "F"),
    "anfac": ("https://anfac.com", "ES", "es", "F"),
    "ofv": ("https://ofv.no", "NO", "no", "F"),
    "mobility-sweden": ("https://mobilitysweden.se", "SE", "sv", "F"),
    "rai-bovag": ("https://raivereniging.nl", "NL", "nl", "F"),
    "ey": ("https://www.ey.com/en_gl/industries/automotive", None, "en", "I"),
    "adac-breakdown": ("https://www.adac.de/rund-ums-fahrzeug/unfall-schaden-panne/adac-pannenstatistik/", "DE", "de",
                       "G"),
    "tuv-report": ("https://www.tuv.com/germany/de/", "DE", "de", "G"),
}


def fetch_newsroom(ctx: FetchContext, source: dict, subjects: list[Subject], cursor: dict) -> FetchResult:
    """One fetch of the start page(s); an article is kept for every subject it names."""
    sid = source["source_id"]
    pages, country, language, category, content_type = NEWSROOMS[sid]
    out = FetchResult()
    seen = set(cursor.get("seen", []))
    by_term = [(s, match_terms(s)) for s in subjects]
    candidates: dict[str, list[tuple[Subject, str]]] = {}
    for page in pages:
        r = ctx.client.get(page, source_id=sid)
        out.requests += 1
        if r.status != 200:
            out.notes.append(f"{page}: HTTP {r.status}")
            continue
        for a in _tree(r.text).css("a"):
            href = (a.attributes.get("href") or "").split("#")[0]
            if not href:
                continue
            url = urljoin(page, href)
            if urlsplit(url).netloc != urlsplit(page).netloc or url in seen or url.rstrip("/") == page.rstrip("/"):
                continue
            label = f"{_text(a)} {urlsplit(url).path.replace('-', ' ')}"
            for s, terms in by_term:
                term = names_subject(label, terms)
                if term:
                    candidates.setdefault(url, []).append((s, term))
    for url, hits in list(candidates.items())[: ctx.max_pages if ctx.backfill else MAX_ARTICLES]:
        ar = ctx.client.get(url, source_id=sid)
        out.requests += 1
        seen.add(url)
        title, text = article_text(ar.text)
        if ar.status != 200 or len(text) < 300:
            continue
        for s, term in hits:
            out.records.append(text_record(
                source=source, subject=s, record_id=f"{_record_id(sid, url)}-{s.id[:8]}", response=ar, text=text,
                url=url, category=category, content_type=content_type, title=title, published_at=_published(ar.text),
                search_term=term, country=country, language=language, run_id=ctx.run_id))
    out.cursor["seen"] = sorted(seen)[-3000:]
    return out


def fetch_market_page(ctx: FetchContext, source: dict, subjects: list[Subject], cursor: dict) -> FetchResult:
    """Market statistics and studies: the page itself, market level, one record per day."""
    sid = source["source_id"]
    url, country, language, category = MARKET_PAGES[sid]
    out = FetchResult()
    r = ctx.client.get(url, source_id=sid)
    out.requests += 1
    title, text = article_text(r.text)
    if r.status != 200 or len(text) < 200:
        out.notes.append(f"{url}: HTTP {r.status}, {len(text)} characters of text")
        return out
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    if cursor.get("last_hash") == digest and not ctx.backfill:
        return out  # unchanged since the last run
    out.records.append(text_record(
        source=source, subject=None, record_id=f"{sid}-{dt.date.today().isoformat()}", response=r, text=text,
        url=url, category=category, content_type="news", level="market", title=title,
        published_at=_published(r.text), country=country, language=language, run_id=ctx.run_id))
    out.cursor["last_hash"] = digest
    return out


SHARED = {sid: fetch_newsroom for sid in NEWSROOMS} | {sid: fetch_market_page for sid in MARKET_PAGES}
