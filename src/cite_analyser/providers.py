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
import regex

from .lookup_error import LookupError, reason_from_exception, reason_from_status

API_TIMEOUT = httpx.Timeout(10.0)
UA = "cite-analyser/0.1 (https://github.com/michael-borck/cite-analyser)"


# ============================================================
# Per-run cache + polite pacing (lookupCache.ts / rateLimiter.ts, simplified)
# ============================================================

_cache: dict[tuple, object] = {}
_last_request: dict[str, float] = {}
_MIN_GAP_SECONDS = 0.4  # per-service polite pacing
# Per-service overrides (arXiv's Terms of Use: one request every 3 seconds).
_SERVICE_INTERVALS = {"arxiv": 3.1}
# Retry back-off before each attempt (transient failures only).
RETRY_DELAYS = (0.7, 1.8)


def _delay(seconds: float) -> None:
    """Sleep seam — tests patch this to keep retry-path tests fast."""
    time.sleep(seconds)


def clear_run_state() -> None:
    """Reset the per-run cache and pacing (called between documents)."""
    _cache.clear()
    _last_request.clear()


def _throttle(service: str) -> None:
    gap = _SERVICE_INTERVALS.get(service, _MIN_GAP_SECONDS)
    last = _last_request.get(service)
    wait = gap - (time.monotonic() - last) if last else 0.0
    if wait > 0:
        _delay(wait)
    _last_request[service] = time.monotonic()


def _get_with_retries(
    service: str,
    url: str,
    headers: dict | None = None,
    timeout: float = 10.0,
    params: dict | None = None,
):
    """GET with the TS engine's retry policy: 429 and 5xx (and network errors)
    are transient — retry per RETRY_DELAYS; anything else fails immediately.
    Raises LookupError carrying the most recent failure reason."""
    reason = "unknown"
    for attempt in range(len(RETRY_DELAYS) + 1):
        if attempt > 0:
            _delay(RETRY_DELAYS[attempt - 1])
        _throttle(service)
        try:
            res = httpx.get(
                url,
                params=params,
                headers=headers or {},
                timeout=httpx.Timeout(timeout),
            )
        except httpx.HTTPError as e:
            reason = reason_from_exception(e)
            continue
        if res.status_code == 200:
            return res
        reason = reason_from_status(res.status_code)
        # Exhausting a daily budget will not recover after a short retry.
        if res.status_code == 429 and res.headers.get("X-RateLimit-Remaining") == "0":
            break
        if res.status_code != 429 and res.status_code < 500:
            break
    raise LookupError(service, reason)


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
    ) | {
        "publicationStatusCheckedAt": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat()
    }


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


# ============================================================
# OpenAlex (openAlex.ts)
# ============================================================


def abstract_from_inverted_index(index: dict | None) -> str | None:
    """OpenAlex ships abstracts as word -> positions; rebuild the reading order."""
    if not index:
        return None
    positions = [p for idxs in index.values() for p in idxs]
    if not positions:
        return None
    words: list[str | None] = [None] * (max(positions) + 1)
    for word, idxs in index.items():
        for position in idxs:
            words[position] = word
    text = " ".join(w for w in words if w).strip()
    return text or None


def _openalex_to_work(item: dict) -> dict:
    raw_doi = item.get("doi") or ""
    doi = (
        raw_doi[len("https://doi.org/") :]
        if raw_doi.startswith("https://doi.org/")
        else (raw_doi or None)
    )
    journal = ((item.get("primary_location") or {}).get("source") or {}).get("display_name") or (
        (item.get("best_oa_location") or {}).get("source") or {}
    ).get("display_name")
    return work(
        title=item.get("display_name") or item.get("title") or "",
        authors=[
            a["author"]["display_name"]
            for a in item.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        year=item.get("publication_year"),
        doi=doi,
        journal=journal,
        source="openalex",
        citation_count=item.get("cited_by_count"),
        abstract=abstract_from_inverted_index(item.get("abstract_inverted_index")),
    )


def search_openalex(
    query: str, mailto: str | None = None, api_key: str | None = None
) -> list[dict]:
    """Search OpenAlex (top 5). Retries transient failures; raises LookupError
    with the reason on persistent failure."""
    key = ("openalex", query)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    params = {"search": query, "per_page": "5"}
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": UA + (f" (mailto:{mailto})" if mailto else "")}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # OpenAlex can be slow under load — generous timeout.
    res = _get_with_retries(
        "openalex",
        "https://api.openalex.org/works",
        headers=headers,
        params=params,
        timeout=15.0,
    )
    try:
        results = res.json().get("results") or []
    except ValueError as e:
        raise LookupError("openalex", "unknown", "Malformed search response") from e
    works = [_openalex_to_work(i) for i in results]
    _cache_set(key, works)
    return works


# ============================================================
# Semantic Scholar (semanticScholar.ts)
# ============================================================

_S2_FIELDS = "title,authors,year,externalIds,journal,citationCount,abstract"


def _s2_to_work(paper: dict) -> dict:
    return work(
        title=paper.get("title") or "",
        authors=[a["name"] for a in paper.get("authors") or [] if a.get("name")],
        year=paper.get("year"),
        doi=(paper.get("externalIds") or {}).get("DOI"),
        journal=(paper.get("journal") or {}).get("name"),
        source="semantic_scholar",
        citation_count=paper.get("citationCount"),
        abstract=paper.get("abstract") or None,
    )


def search_semantic_scholar(query: str, api_key: str | None = None) -> list[dict]:
    """Search Semantic Scholar (top 5); the keyless pool 429s aggressively, so
    retries matter here. An API key is sent as x-api-key when present."""
    key = ("s2", query)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    params = {"query": query, "limit": "5", "fields": _S2_FIELDS}
    headers = {"User-Agent": UA}
    if api_key:
        headers["x-api-key"] = api_key
    res = _get_with_retries(
        "semantic_scholar",  # service token matches the TS engine's flags
        "https://api.semanticscholar.org/graph/v1/paper/search",
        headers=headers,
        params=params,
    )
    try:
        data = res.json().get("data") or []
    except ValueError as e:
        raise LookupError("semantic_scholar", "unknown", "Malformed search response") from e
    works = [_s2_to_work(p) for p in data]
    _cache_set(key, works)
    return works


# ============================================================
# arXiv (arxiv.ts)
# ============================================================

ARXIV_ID_PREFIXED_RE = regex.compile(
    r"\barxiv[:\s/]*((?:\d{4})\.\d{4,5})(?:v\d+)?\b", regex.IGNORECASE
)
ARXIV_ID_BARE_RE = regex.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")


def extract_arxiv_id(raw: str) -> str | None:
    """New-style arXiv ID from a raw reference, or None. A bare ID is accepted
    only when the string mentions arXiv somewhere, so page ranges like
    "2205.11" cannot masquerade as identifiers."""
    prefixed = ARXIV_ID_PREFIXED_RE.search(raw)
    if prefixed:
        return prefixed.group(1)
    if regex.search(r"arxiv", raw, regex.IGNORECASE):
        bare = ARXIV_ID_BARE_RE.search(raw)
        if bare:
            return bare.group(1)
    return None


def _decode_entities(s: str) -> str:
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )


def parse_arxiv_feed(xml: str) -> list[dict]:
    """Parse an arXiv Atom feed's <entry> blocks (regex-level: the feed is
    machine-generated and flat, avoiding an XML dependency)."""
    entries: list[dict] = []
    for block_m in regex.finditer(r"<entry>([\s\S]*?)</entry>", xml):
        block = block_m.group(1)
        title_m = regex.search(r"<title>([\s\S]*?)</title>", block)
        if not title_m:
            continue
        published_m = regex.search(r"<published>(\d{4})-", block)
        id_m = regex.search(r"<id>https?://arxiv\.org/abs/([^<v]+)(?:v\d+)?</id>", block)
        authors = [
            _decode_entities(a.group(1).strip())
            for a in regex.finditer(r"<name>([\s\S]*?)</name>", block)
        ]
        entries.append(
            {
                "title": _decode_entities(regex.sub(r"\s+", " ", title_m.group(1)).strip()),
                "authors": authors,
                "year": int(published_m.group(1)) if published_m else None,
                "id": id_m.group(1).strip() if id_m else "",
            }
        )
    return entries


def _arxiv_entry_to_work(entry: dict) -> dict:
    arxiv_id = entry.get("id") or ""
    return work(
        title=entry.get("title") or "",
        authors=entry.get("authors") or [],
        year=entry.get("year"),
        # arXiv DOIs are DataCite-registered as 10.48550/arXiv.<id>.
        doi=f"10.48550/arXiv.{arxiv_id}" if arxiv_id else None,
        url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
        journal="arXiv",
        source="arxiv",
    )


def _fetch_arxiv_feed(params: dict) -> list[dict]:
    res = _get_with_retries("arxiv", "https://export.arxiv.org/api/query", params=params)
    return parse_arxiv_feed(res.text)


def lookup_arxiv_id(arxiv_id: str) -> dict | None:
    """Look up one work by its arXiv ID — authoritative for preprints the way
    DOI resolution is for published works."""
    key = ("arxiv-id", arxiv_id)
    cached = _cache_get(key)
    if cached is not None or key in _cache:
        return cached
    entries = _fetch_arxiv_feed({"id_list": arxiv_id})
    # An unknown ID yields a feed whose single entry has an error title/no id.
    entry = next((e for e in entries if e["id"] and e["authors"]), None)
    result = _arxiv_entry_to_work(entry) if entry else None
    _cache_set(key, result)
    return result


def search_arxiv(query: str) -> list[dict]:
    """Search arXiv by title words (top 5), empty on no match."""
    key = ("arxiv", query)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    cleaned = regex.sub(r"[^\p{L}\p{N}\s]", " ", query)
    cleaned = regex.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    entries = _fetch_arxiv_feed({"search_query": f'ti:"{cleaned}"', "max_results": "5"})
    works = [_arxiv_entry_to_work(e) for e in entries if e["id"]]
    _cache_set(key, works)
    return works


# ============================================================
# Europe PMC (europePmc.ts)
# ============================================================


def _europepmc_to_work(item: dict) -> dict:
    authors = []
    for a in (item.get("authorList") or {}).get("author") or []:
        if a.get("collectiveName"):
            authors.append(a["collectiveName"])
        elif a.get("lastName"):
            authors.append(", ".join(p for p in (a.get("lastName"), a.get("firstName")) if p))
        elif a.get("fullName"):
            authors.append(a["fullName"])
    year = item.get("pubYear") or ""
    abstract = item.get("abstractText")
    if abstract:
        import regex as _regex

        abstract = _regex.sub(r"<[^>]+>", " ", abstract)
        abstract = _regex.sub(r"\s+", " ", abstract).strip() or None
    journal_info = item.get("journalInfo") or {}
    url = None
    if item.get("id") and item.get("source"):
        url = (
            f"https://europepmc.org/article/{urllib.parse.quote(item['source'])}/"
            f"{urllib.parse.quote(item['id'])}"
        )
    return work(
        title=item.get("title") or "",
        authors=authors,
        year=int(year) if regex.match(r"^\d{4}$", year) else None,
        doi=item.get("doi"),
        source="europe_pmc",
        journal=(journal_info.get("journal") or {}).get("title"),
        volume=journal_info.get("volume"),
        issue=journal_info.get("issue"),
        pages=item.get("pageInfo"),
        work_type="; ".join((item.get("pubTypeList") or {}).get("pubType") or []) or None,
        abstract=abstract,
        url=url,
    )


def search_europe_pmc(title: str) -> list[dict]:
    """Europe PMC's public API needs no key. Send only the reference title."""
    if not title.strip():
        return []
    key = ("europe-pmc", title)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    literal = regex.sub(r'["\\]', " ", title)
    params = {
        "query": f'TITLE:"{literal}"',
        "format": "json",
        "resultType": "core",
        "pageSize": "5",
    }
    res = _get_with_retries(
        "europe_pmc",
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        headers={"User-Agent": UA},
        params=params,
    )
    try:
        results = (res.json().get("resultList") or {}).get("result")
    except ValueError as e:
        raise LookupError("europe_pmc", "unknown", "Malformed search response") from e
    if not isinstance(results, list):
        raise LookupError("europe_pmc", "unknown", "Malformed search response")
    works = [_europepmc_to_work(i) for i in results]
    _cache_set(key, works)
    return works
