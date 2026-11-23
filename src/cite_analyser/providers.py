"""Lookup providers: DOI resolution, Crossref, DataCite.

Ports of doiResolver.ts, crossref.ts and datacite.ts (the primary academic
cascade). OpenAlex / Semantic Scholar / arXiv / Europe PMC / URL liveness /
web sources arrive with their own milestones and plug into verifier.py's
optional provider seams.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.parse

import httpx

from .lookup_error import LookupError, reason_from_exception, reason_from_status

API_TIMEOUT = httpx.Timeout(10.0)
UA = "cite-analyser/0.1 (https://github.com/michael-borck/cite-analyser)"


# ============================================================
# Per-run cache + polite pacing (lookupCache.ts / rateLimiter.ts, simplified)
# ============================================================

_cache: dict[tuple, object] = {}
_last_request: dict[str, float] = {}
_MIN_GAP_SECONDS = 0.4  # per-service polite pacing


def clear_run_state() -> None:
    """Reset the per-run cache and pacing (called between documents)."""
    _cache.clear()
    _last_request.clear()


def _throttle(service: str) -> None:
    last = _last_request.get(service)
    wait = _MIN_GAP_SECONDS - (time.monotonic() - last) if last else 0.0
    if wait > 0:
        time.sleep(wait)
    _last_request[service] = time.monotonic()


def _cache_get(key: tuple):
    return _cache.get(key, None) if key in _cache else None


def _cache_set(key: tuple, value) -> None:
    _cache[key] = value


# ============================================================
# AcademicWork helpers
# ============================================================


def work(
    title="",
    authors=None,
    year=None,
    doi=None,
    url=None,
    journal=None,
    volume=None,
    issue=None,
    pages=None,
    source="",
    citation_count=None,
    work_type=None,
    abstract=None,
    publication_updates=None,
) -> dict:
    w = {
        "title": title,
        "authors": authors or [],
        "year": year,
        "doi": doi,
        "url": url,
        "journal": journal,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "source": source,
    }
    if citation_count is not None:
        w["citationCount"] = citation_count
    if work_type is not None:
        w["workType"] = work_type
    if abstract is not None:
        w["abstract"] = abstract
    if publication_updates is not None:
        w["publicationUpdates"] = publication_updates
    return w


# ============================================================
# SSRF guard (ssrf.ts, minimal)
# ============================================================


def is_private_url(url: str) -> bool:
    """True when the URL's host resolves to a private/loopback address."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        if host in ("localhost", "0.0.0.0", "::1") or host.endswith(".local"):
            return True
        for info in socket.getaddrinfo(host, None):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local:
                return True
        return False
    except (socket.gaierror, ValueError):
        return False


# ============================================================
# Crossref
# ============================================================


def _crossref_item_to_work(item: dict) -> dict:
    def year_of(it: dict):
        for key in ("published", "published-print", "published-online"):
            parts = (it.get(key) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                return parts[0][0]
        return None

    authors = []
    for a in item.get("author") or []:
        if a.get("name"):
            authors.append(a["name"])
        else:
            parts = [p for p in (a.get("family"), a.get("given")) if p]
            if parts:
                authors.append(", ".join(parts))

    abstract = None
    if item.get("abstract"):
        import regex as _regex

        cleaned = _regex.sub(r"<[^>]+>", " ", item["abstract"])
        cleaned = _regex.sub(r"\s+", " ", cleaned).strip()
        abstract = cleaned or None

    updates = [
        {
            "type": u.get("type") or "update",
            "doi": u.get("DOI"),
            "source": u.get("source") or "publisher",
            "date": (u.get("updated") or {}).get("date-time"),
        }
        for u in item.get("updated-by") or []
    ]

    return work(
        title=(item.get("title") or [""])[0],
        authors=authors,
        year=year_of(item),
        doi=item.get("DOI"),
        url=item.get("URL"),
        journal=(item.get("container-title") or [None])[0],
        volume=item.get("volume"),
        issue=item.get("issue"),
        pages=item.get("page"),
        source="crossref",
        citation_count=item.get("is-referenced-by-count"),
        work_type=item.get("type"),
        abstract=abstract,
        publication_updates=updates,
    )


def search_crossref(query: str, mailto: str | None = None) -> list[dict]:
    """Search Crossref for works matching a bibliographic query (top 5)."""
    key = ("crossref", query)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    _throttle("crossref")
    params = {"query.bibliographic": query, "rows": "5"}
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": UA + (f" (mailto:{mailto})" if mailto else "")}
    try:
        res = httpx.get(
            "https://api.crossref.org/works",
            params=params,
            headers=headers,
            timeout=API_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LookupError("crossref", reason_from_exception(e)) from e
    if res.status_code != 200:
        raise LookupError("crossref", reason_from_status(res.status_code))
    try:
        items = (res.json().get("message") or {}).get("items") or []
    except ValueError as e:
        raise LookupError("crossref", "unknown", "Malformed search response") from e
    works = [_crossref_item_to_work(i) for i in items]
    _cache_set(key, works)
    return works


def lookup_doi_crossref(doi: str, mailto: str | None = None) -> dict | None:
    """Resolve a Crossref-registered DOI to full metadata; None on clean miss."""
    key = ("crossref-doi", doi)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    _throttle("crossref")
    try:
        res = httpx.get(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
            headers={"User-Agent": UA + (f" (mailto:{mailto})" if mailto else "")},
            timeout=API_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LookupError("crossref", reason_from_exception(e)) from e
    if res.status_code == 404:
        _cache_set(key, None)
        return None
    if res.status_code != 200:
        raise LookupError("crossref", reason_from_status(res.status_code))
    try:
        item = res.json().get("message")
    except ValueError as e:
        raise LookupError("crossref", "unknown", "Malformed DOI response") from e
    result = _crossref_item_to_work(item) if item else None
    _cache_set(key, result)
    return result


# ============================================================
# DataCite
# ============================================================


def _datacite_to_work(a: dict) -> dict:

    titles = a.get("titles") or []
    title = next((t.get("title") for t in titles if t.get("title")), "")
    return work(
        title=title,
        authors=[c["name"] for c in (a.get("creators") or []) if c.get("name")],
        year=a.get("publicationYear"),
        doi=a.get("doi"),
        url=a.get("url"),
        source="datacite",
        work_type=(a.get("types") or {}).get("resourceTypeGeneral"),
    )


def search_datacite(title: str, mailto: str | None = None) -> list[dict]:
    """Public title search — finds deposits whose citations omit their DOI."""
    if not title.strip():
        return []
    key = ("datacite-search", title)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    _throttle("datacite")
    import regex as _regex

    literal = _regex.sub(r'["\\]', " ", title)  # quote user text literally
    params = {"query": f'titles.title:"{literal}"', "page[size]": "5"}
    if mailto:
        params["mailto"] = mailto
    try:
        res = httpx.get(
            "https://api.datacite.org/dois",
            params=params,
            headers={"User-Agent": UA + (f" (mailto:{mailto})" if mailto else "")},
            timeout=API_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LookupError("datacite", reason_from_exception(e)) from e
    if res.status_code != 200:
        raise LookupError("datacite", reason_from_status(res.status_code))
    try:
        data = res.json().get("data") or []
    except ValueError as e:
        raise LookupError("datacite", "unknown", "Malformed search response") from e
    if not isinstance(data, list):
        raise LookupError("datacite", "unknown", "Malformed search response")
    works = [_datacite_to_work(i["attributes"]) for i in data if i.get("attributes")]
    _cache_set(key, works)
    return works


def lookup_doi_datacite(doi: str, mailto: str | None = None) -> dict | None:
    """Resolve a DataCite-registered DOI (datasets, software, repository
    deposits — Zenodo/Figshare/Dryad); None on a clean miss."""
    key = ("datacite-doi", doi)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    _throttle("datacite")
    try:
        res = httpx.get(
            f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}",
            headers={"User-Agent": UA + (f" (mailto:{mailto})" if mailto else "")},
            timeout=API_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LookupError("datacite", reason_from_exception(e)) from e
    if res.status_code == 404:
        _cache_set(key, None)
        return None
    if res.status_code != 200:
        raise LookupError("datacite", reason_from_status(res.status_code))
    attributes = (res.json().get("data") or {}).get("attributes")
    result = _datacite_to_work(attributes) if attributes else None
    _cache_set(key, result)
    return result


# ============================================================
# DOI resolution (doiResolver.ts)
# ============================================================


def resolve_doi(doi: str, mailto: str | None = None) -> dict | None:
    """Resolve a DOI to full academic metadata.

    1. Crossref REST (authoritative, structured metadata)
    2. DataCite metadata (repository deposits)
    3. doi.org redirect walk — confirm registration without probing the
       publisher; a 404/410 from the registry is a clean miss, anything else
       is a lookup failure.
    """
    failure: Exception | None = None
    for lookup in (lookup_doi_crossref, lookup_doi_datacite):
        try:
            result = lookup(doi, mailto)
            if result:
                return result
        except Exception as e:  # noqa: BLE001 — cascade keeps the first failure
            failure = failure or e

    url = f"https://doi.org/{urllib.parse.quote(doi)}"
    try:
        for _hop in range(5):
            _throttle("doi.org")
            res = httpx.head(
                url,
                follow_redirects=False,
                timeout=API_TIMEOUT,
                headers={"User-Agent": UA + (f" (mailto:{mailto})" if mailto else "")},
            )
            if res.status_code in (404, 410):
                if failure:
                    raise failure
                return None
            location = res.headers.get("location")
            if res.status_code not in (301, 302, 303, 307, 308) or not location:
                raise LookupError("doi.org", reason_from_status(res.status_code))
            target = urllib.parse.urljoin(url, location)
            if is_private_url(target):
                raise LookupError("doi.org", "unknown", "DOI points to a private address")
            host = urllib.parse.urlparse(target).hostname or ""
            if host in ("doi.org", "dx.doi.org"):
                url = target
                continue
            return work(title="", authors=[], year=None, doi=doi, url=target, source="crossref")
        raise LookupError("doi.org", "unknown", "DOI redirect limit exceeded")
    except LookupError:
        raise
    except httpx.HTTPError as e:
        raise LookupError("doi.org", reason_from_exception(e)) from e
