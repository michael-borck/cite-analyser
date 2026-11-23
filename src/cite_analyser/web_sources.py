"""Non-academic source verification — port of webSourceVerifier.ts.

Cascade: ISBN → Open Library (books, regardless of URL); then, by URL hostname,
YouTube/Vimeo oEmbed; then Open Library title search or generic web-page
metadata (citation_* tags, JSON-LD, visible bylines). Every fetch is SSRF-
guarded, per-host paced, and retried on transient failures.
"""

from __future__ import annotations

import datetime
import json
import urllib.parse

import httpx
import regex

from .lookup_error import LookupError, reason_from_exception, reason_from_status
from .matching import surname_of
from .providers import _delay, _throttle, is_private_url, work

RETRY_DELAYS = (0.7, 1.8)
MAX_RETRY_AFTER = 5.0  # seconds; one page cannot stall a whole run

_CODE_POINT_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}


# ============================================================
# ISBN
# ============================================================


def extract_isbn(raw: str) -> str | None:
    """Extract a checksum-valid ISBN-10/13 from reference text."""
    match = (
        regex.search(
            r"\b(?:ISBN[-:]?\s*)(97[89][-\s]?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,7}[-\s]?\d)",
            raw,
            regex.IGNORECASE,
        )
        or regex.search(
            r"\b(?:ISBN[-:]?\s*)(\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,7}[-\s]?[\dXx])",
            raw,
            regex.IGNORECASE,
        )
        or regex.search(r"\b(97[89]\d{10})\b", raw)
        or regex.search(r"\b(\d{9}[\dXx])\b", raw)
    )
    if not match:
        return None
    isbn = regex.sub(r"[-\s]", "", match.group(1)).upper()
    if regex.match(r"^\d{13}$", isbn):
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(isbn))
        valid = total % 10 == 0
    else:
        valid = bool(regex.match(r"^\d{9}[\dX]$", isbn)) and (
            sum((10 if d == "X" else int(d)) * (10 - i) for i, d in enumerate(isbn)) % 11 == 0
        )
    return isbn if valid else None


# ============================================================
# Fetch plumbing
# ============================================================


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return min(max(seconds, 0.0), MAX_RETRY_AFTER)
    except ValueError:
        pass
    try:
        when = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.UTC)
        return min(max(0.0, (when - now).total_seconds()), MAX_RETRY_AFTER)
    except ValueError:
        return None


def safe_fetch(url: str, timeout: float = 8.0):
    """GET with timeout, per-host pacing, and retry/back-off on transient
    failures (429/5xx/timeout/network). Returns None on 404/410; raises
    LookupError on persistent failure."""
    service = urllib.parse.urlparse(url).hostname or "web"
    reason = "unknown"
    wait = 0.0
    for attempt in range(len(RETRY_DELAYS) + 1):
        if attempt > 0:
            _delay(wait)
        _throttle(service)
        try:
            res = httpx.get(
                url,
                follow_redirects=True,
                timeout=httpx.Timeout(timeout),
                headers={"User-Agent": "cite-analyser/0.1 (academic-reference-checker)"},
            )
        except httpx.HTTPError as e:
            reason = reason_from_exception(e)
            wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            continue
        if res.status_code in (404, 410):
            return None
        if 200 <= res.status_code < 300:
            return res
        reason = reason_from_status(res.status_code)
        retryable = res.status_code == 429 or res.status_code >= 500
        wait = (
            _retry_after_seconds(res.headers.get("retry-after"))
            or RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
        )
        if not retryable:
            break
    raise LookupError(service, reason)


# ============================================================
# HTML helpers
# ============================================================


def _decode_entities(value: str) -> str:
    def codepoint(code: int) -> str:
        try:
            return chr(code)
        except (ValueError, OverflowError):
            return ""

    value = regex.sub(
        r"&#x([0-9a-f]+);",
        lambda m: codepoint(int(m.group(1), 16)),
        value,
        flags=regex.IGNORECASE,
    )
    value = regex.sub(r"&#(\d+);", lambda m: codepoint(int(m.group(1))), value)
    return regex.sub(
        r"&([a-z]+);",
        lambda m: _CODE_POINT_ENTITIES.get(m.group(1).lower(), m.group(0)),
        value,
        flags=regex.IGNORECASE,
    )


def _strip_html(html: str) -> str:
    stripped = regex.sub(
        r"<(script|style|noscript|template)\b[\s\S]*?</\1>", " ", html, flags=regex.IGNORECASE
    )
    stripped = regex.sub(r"<br\s*/?>", " ", stripped, flags=regex.IGNORECASE)
    stripped = regex.sub(r"<[^>]+>", " ", stripped)
    stripped = _decode_entities(stripped)
    return regex.sub(r"\s+", " ", stripped).strip()


def _extract_meta_contents(html: str, names: list[str]) -> list[str]:
    values: list[str] = []
    for name in names:
        pattern = (
            rf"<meta\s+(?:[^>]*?)(?:name|property)=[\"']{name}[\"'][^>]*?content=[\"']([^\"']+)[\"']"
            rf"|<meta\s+(?:[^>]*?)content=[\"']([^\"']+)[\"'][^>]*?(?:name|property)=[\"']{name}[\"']"
        )
        for m in regex.finditer(pattern, html, flags=regex.IGNORECASE):
            value = m.group(1) or m.group(2)
            if value:
                values.append(value)
    return values


def _extract_meta_content(html: str, names: list[str]) -> str | None:
    values = _extract_meta_contents(html, names)
    return values[0] if values else None


def _extract_html_title(html: str) -> str | None:
    m = regex.search(r"<title[^>]*>([\s\S]*?)</title>", html, flags=regex.IGNORECASE)
    if not m:
        return None
    title = _strip_html(m.group(1))
    return title or None


def _extract_h1(html: str) -> str | None:
    m = regex.search(r"<h1\b[^>]*>([\s\S]*?)</h1>", html, flags=regex.IGNORECASE)
    if not m:
        return None
    title = _strip_html(m.group(1))
    return title if len(title) >= 3 else None


def _clean_author(value: str) -> str:
    value = regex.sub(r"\s+", " ", value)
    value = regex.sub(r"\s+et\s+al\.?$", "", value, flags=regex.IGNORECASE)
    return regex.sub(r"^[\s,;:.\u2013\u2014-]+|[\s,;:.\u2013\u2014-]+$", "", value).strip()


def _split_author_names(value: str) -> list[str]:
    parts = regex.split(
        r"\s*(?:;|&|\band\b)\s*|,\s*(?=\p{Lu}[\p{L}.'\u2019-]+\s+\p{Lu}[\p{L}.'\u2019-]+)",
        value,
    )
    return [n for n in (_clean_author(p) for p in parts) if n]


def _unique_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for name in names:
        key = name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _year_of(value: str | None) -> int | None:
    if not value:
        return None
    m = regex.search(r"(?:19|20)\d{2}", value)
    return int(m.group(0)) if m else None


# --- JSON-LD -------------------------------------------------------------


def _flatten_json_ld(value, out=None, depth=0):
    if out is None:
        out = []
    if depth > 6 or value is None:
        return out
    if isinstance(value, list):
        for item in value:
            _flatten_json_ld(item, out, depth + 1)
        return out
    if not isinstance(value, dict):
        return out
    out.append(value)
    for key in ("@graph", "mainEntity", "mainEntityOfPage", "hasPart"):
        if key in value:
            _flatten_json_ld(value[key], out, depth + 1)
    return out


def _json_string(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _json_ld_authors(value, out=None) -> list[str]:
    if out is None:
        out = []
    if isinstance(value, list):
        for item in value:
            _json_ld_authors(item, out)
        return out
    if isinstance(value, dict):
        parts = [
            p
            for p in (_json_string(value.get("givenName")), _json_string(value.get("familyName")))
            if p
        ]
        name = _json_string(value.get("name")) or (" ".join(parts) if parts else None)
        if name:
            out.extend(_split_author_names(name))
        return out
    raw = _json_string(value)
    if raw:
        out.extend(_split_author_names(raw))
    return out


_ARTICLE_TYPE_RE = regex.compile(
    r"Article|BlogPosting|Scholarly|Report|Book|Thesis|Review|CreativeWork"
)


def _json_ld_metadata(html: str) -> dict:
    title = None
    authors: list[str] = []
    year = None
    for m in regex.finditer(
        r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>",
        html,
        flags=regex.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except (ValueError, TypeError):
            continue
        for node in _flatten_json_ld(data):
            raw_type = node.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            article_like = (
                any(isinstance(t, str) and _ARTICLE_TYPE_RE.search(t) for t in types)
                or "headline" in node
                or "datePublished" in node
            )
            if not article_like:
                continue
            if title is None:
                title = (
                    _json_string(node.get("headline"))
                    or _json_string(node.get("name"))
                    or _json_string(node.get("title"))
                )
            authors.extend(_json_ld_authors(node.get("author") or node.get("creator")))
            if year is None:
                year = _year_of(
                    _json_string(node.get("datePublished")) or _json_string(node.get("dateCreated"))
                )
    return {"title": title, "authors": _unique_names(authors), "year": year}


# --- Visible-text bylines --------------------------------------------------


def _looks_like_person_name(value: str) -> bool:
    name = _clean_author(value)
    if len(name) < 3 or len(name) > 80:
        return False
    if regex.search(r"\d|https?:|@|/", name):
        return False
    return bool(regex.match(r"^\p{Lu}[\p{L}.'\u2019-]+(?:\s+\p{Lu}[\p{L}.'\u2019-]+){1,3}$", name))


def _visible_author_names(html: str) -> list[str]:
    names: list[str] = []
    for pattern in (
        r"<a\b[^>]*\brel=[\"'][^\"']*\bauthor\b[^\"']*[\"'][^>]*>([\s\S]*?)</a>",
        r"<(?:span|div|p|li|address)\b[^>]*\bitemprop=[\"']author[\"'][^>]*>([\s\S]*?)</(?:span|div|p|li|address)>",
    ):
        for m in regex.finditer(pattern, html, flags=regex.IGNORECASE):
            names.extend(
                n
                for n in _split_author_names(_strip_html(m.group(1)))
                if _looks_like_person_name(n)
            )
    if names:
        return _unique_names(names)

    text = _strip_html(html)[:8000]
    byline = regex.compile(
        r"\b(?:[Bb]y|[Aa]uthors?\s*[:-])\s+"
        r"((?:\p{Lu}[\p{L}.'\u2019-]+(?:\s+\p{Lu}[\p{L}.'\u2019-]+){0,2})"
        r"(?:\s*(?:,|&|;|\band\b)\s*\p{Lu}[\p{L}.'\u2019-]+(?:\s+\p{Lu}[\p{L}.'\u2019-]+){0,2})*)"
    )
    for m in byline.finditer(text):
        names.extend(n for n in _split_author_names(m.group(1)) if _looks_like_person_name(n))
    return _unique_names(names)


# ============================================================
# Source-specific lookups
# ============================================================


def _detect_source_type(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return "web_metadata"
    host = host.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "vimeo.com" in host:
        return "vimeo"
    if "openlibrary.org" in host or "books.google.com" in host:
        return "open_library"
    return "web_metadata"


def _verify_oembed(url: str, oembed_url: str, source: str) -> dict | None:
    res = safe_fetch(oembed_url)
    if not res:
        return None
    data = res.json()
    title = data.get("title")
    if not title:
        return None
    author = data.get("author_name")
    return work(
        title=title,
        authors=[author] if author else [],
        year=None,
        source=source,
        url=url,
    )


def _verify_youtube(url: str) -> dict | None:
    return _verify_oembed(
        url,
        f"https://www.youtube.com/oembed?url={urllib.parse.quote(url, safe='')}&format=json",
        "youtube",
    )


def _verify_vimeo(url: str) -> dict | None:
    return _verify_oembed(
        url, f"https://vimeo.com/api/oembed.json?url={urllib.parse.quote(url, safe='')}", "vimeo"
    )


def _verify_book_by_isbn(isbn: str) -> dict | None:
    res = safe_fetch(
        f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    )
    if not res:
        return None
    data = res.json()
    entry = data.get(f"ISBN:{isbn}")
    if not entry or not entry.get("title"):
        return None
    return work(
        title=entry["title"],
        authors=[a["name"] for a in entry.get("authors") or [] if a.get("name")],
        year=_year_of(entry.get("publish_date")),
        source="open_library",
        url=entry.get("url") or f"https://openlibrary.org/isbn/{isbn}",
    )


def _verify_book_by_search(title: str, author: str | None = None) -> dict | None:
    # Open Library's free-text q is poisoned by APA-style "Last, F." —
    # query the title with the author's bare surname only.
    surname = surname_of(author or "")
    params = {"title": title, "limit": "5"}
    if surname:
        params["author"] = surname
    res = safe_fetch(f"https://openlibrary.org/search.json?{urllib.parse.urlencode(params)}")
    if not res:
        return None
    docs = res.json().get("docs") or []
    if not docs:
        return None
    by_author = next(
        (
            d
            for d in docs
            if surname and any(surname_of(a) == surname for a in d.get("author_name") or [])
        ),
        None,
    )
    doc = by_author or next((d for d in docs if d.get("title")), None)
    if not doc or not doc.get("title"):
        return None
    isbn = (doc.get("isbn") or [None])[0]
    return work(
        title=doc["title"],
        authors=doc.get("author_name") or [],
        year=doc.get("first_publish_year"),
        source="open_library",
        url=f"https://openlibrary.org/isbn/{isbn}" if isbn else None,
    )


def _verify_web_page(url: str) -> dict | None:
    if is_private_url(url):
        return None
    res = safe_fetch(url)
    if not res:
        return None
    content_type = res.headers.get("content-type") or ""
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        return None

    html = res.text
    json_ld = _json_ld_metadata(html)
    title = (
        _extract_meta_content(html, ["citation_title", "dc.title", "og:title", "twitter:title"])
        or json_ld["title"]
        or _extract_h1(html)
        or _extract_html_title(html)
    )
    if not title:
        return None

    authors = _unique_names(
        [
            n
            for author in _extract_meta_contents(
                html, ["citation_author", "dc.creator", "article:author", "author"]
            )
            for n in _split_author_names(author)
        ]
        + json_ld["authors"]
    )
    if not authors:
        authors = _visible_author_names(html)

    date_str = _extract_meta_content(
        html, ["citation_publication_date", "article:published_time", "date", "dc.date"]
    )
    return work(
        title=title,
        authors=authors,
        year=_year_of(date_str) or json_ld["year"],
        source="web_metadata",
        url=url,
    )


# ============================================================
# Public API
# ============================================================


def verify_web_source(ref: dict, isbn_only: bool = False) -> dict | None:
    """Verify a non-academic reference via web sources (cascade).

    ISBN → Open Library always tries first; then source type by URL hostname,
    or an Open Library title search when there is no URL but the reference
    looks like a book."""
    isbn = extract_isbn(ref.get("raw") or "")
    if isbn:
        book = _verify_book_by_isbn(isbn)
        if book:
            return book
    if isbn_only:
        return None

    url = ref.get("url")
    if not url:
        title = ref.get("title") or ""
        if title and (isbn or len(title) > 10):
            return _verify_book_by_search(title, (ref.get("authors") or [None])[0])
        return None

    # The URL comes from the uploaded document: block private/internal
    # addresses and non-http(s) schemes before any handler fetches it.
    if is_private_url(url):
        return None

    source_type = _detect_source_type(url)
    if source_type == "youtube":
        return _verify_youtube(url)
    if source_type == "vimeo":
        return _verify_vimeo(url)
    if source_type == "open_library":
        return _verify_book_by_search(ref.get("title") or "", (ref.get("authors") or [None])[0])
    return _verify_web_page(url)
