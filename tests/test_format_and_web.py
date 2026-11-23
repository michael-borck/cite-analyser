"""Format validation, ISBN, URL liveness, web metadata and publication tests."""

import pytest

from cite_analyser import url_checker, web_sources
from cite_analyser.format import validate_format
from cite_analyser.publication_updates import check_publication_updates
from cite_analyser.web_sources import extract_isbn


def ref(**kw):
    base = {"raw": "", "authors": [], "title": "", "year": None, "detectedStyle": "apa"}
    base.update(kw)
    return base


# --- format validation ------------------------------------------------------


def test_apa_year_must_be_parenthesised():
    issues = validate_format(
        ref(raw="Smith, J. 2020. Title. *J, 1*(2), 3.", authors=["Smith, J."], year=2020),
        "apa",
    )
    assert any(i["field"] == "year" for i in issues)


def test_apa_title_case_flagged_for_sentences():
    issues = validate_format(
        ref(
            raw="Smith, J. (2020). A Great Title In Title Case. *J, 1*(2), 3.",
            authors=["Smith, J."],
            title="A Great Title In Title Case",
            year=2020,
        ),
        "apa",
    )
    assert any(i["field"] == "title" for i in issues)


def test_apa_author_format():
    issues = validate_format(
        ref(raw="Jane Smith (2020). Title.", authors=["Jane Smith"], year=2020),
        "apa",
    )
    assert any(i["field"] == "authors" for i in issues)


def test_journal_rules_only_when_periodical_marker_present():
    book = ref(
        raw="Smith, J. (2020). *A Book Title.* Penguin.",
        authors=["Smith, J."],
        title="A Book Title",
        journal="Penguin",
        year=2020,
    )
    # no volume/issue -> no DOI/Title-Case journal rules fire for a book
    assert not any(i["field"] in ("doi", "journal") for i in validate_format(book, "apa"))


def test_mla_requires_vol_no_pp_labels():
    issues = validate_format(
        ref(
            raw='Smith, John. "Title." *Journal* 12, 3, 4-5.',
            authors=["Smith, John"],
            title="Title",
            volume="12",
            issue="3",
            pages="4-5",
            year=2019,
            detectedStyle="mla",
        ),
        "mla",
    )
    fields = {i["field"] for i in issues}
    assert {"volume", "issue", "pages"} <= fields


def test_chicago_year_pattern():
    issues = validate_format(
        ref(
            raw='Smith, John. "Title." *Journal* 12 (2019).',
            authors=["Smith, John"],
            title="Title",
            journal="Journal",
            year=2019,
            detectedStyle="chicago",
        ),
        "chicago",
    )
    assert any(i["field"] == "year" for i in issues)


def test_unknown_style_falls_back_to_detected():
    issues = validate_format(
        ref(
            raw='Smith, John. "Title." *Journal* 12, 3, 4-5. 2019.',
            authors=["Smith, John"],
            title="Title",
            volume="12",
            detectedStyle="mla",
        ),
        "unknown",
    )
    assert any(i["field"] == "volume" for i in issues)


# --- ISBN -------------------------------------------------------------------


def test_isbn10_checksum():
    assert extract_isbn("ISBN 0-306-40615-2") == "0306406152"
    assert extract_isbn("ISBN 0-306-40615-3") is None  # bad checksum


def test_isbn13_checksum():
    assert extract_isbn("ISBN 978-0-306-40615-7") == "9780306406157"
    assert extract_isbn("978-0-306-40615-8") is None
    # bare (no ISBN label) still found when checksum-valid
    assert extract_isbn("see 9780306406157 for details") == "9780306406157"


# --- URL liveness -----------------------------------------------------------


class _Res:
    def __init__(self, status, url="http://x/", headers=None):
        self.status_code = status
        self.url = url
        self.headers = headers or {}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(url_checker, "_throttle", lambda s: None)


def test_url_status_mapping(monkeypatch, no_sleep):
    cases = {
        200: "live",
        302: "redirect",
        404: "dead",
        410: "dead",
        403: "blocked",
        429: "blocked",
        500: "blocked",
    }
    for code, expected in cases.items():
        monkeypatch.setattr(url_checker.httpx, "get", lambda *a, _c=code, **k: _Res(_c))
        assert url_checker.check_url("http://x/")["status"] == expected, code


def test_url_timeout_and_private_address(monkeypatch, no_sleep):
    def timeout(*a, **k):
        raise url_checker.httpx.TimeoutException("slow")

    monkeypatch.setattr(url_checker.httpx, "get", timeout)
    assert url_checker.check_url("http://x/")["status"] == "timeout"
    assert url_checker.check_url("http://127.0.0.1/x")["status"] == "error"


# --- web metadata -----------------------------------------------------------


def test_web_page_metadata_extraction(monkeypatch):
    html = """
    <html><head>
      <meta name="citation_title" content="The Real Title">
      <meta name="citation_author" content="Smith, Jane">
      <meta name="citation_author" content="Jones, Kim">
      <meta name="citation_publication_date" content="2021/05/01">
    </head><body><h1>Ignored</h1></body></html>
    """
    monkeypatch.setattr(
        web_sources,
        "safe_fetch",
        lambda url, timeout=8.0: _Res(200, headers={"content-type": "text/html"}),
    )

    # response needs .text
    class TextRes(_Res):
        text = html

    monkeypatch.setattr(
        web_sources,
        "safe_fetch",
        lambda url, timeout=8.0: TextRes(200, headers={"content-type": "text/html"}),
    )
    result = web_sources.verify_web_source(
        {
            "raw": "x",
            "title": "The Real Title",
            "authors": ["Smith, J."],
            "url": "https://example.com/article",
        }
    )
    assert result["title"] == "The Real Title"
    assert result["authors"] == ["Smith, Jane", "Jones, Kim"]
    assert result["year"] == 2021
    assert result["source"] == "web_metadata"


def test_json_ld_fallback(monkeypatch):
    html = """
    <html><head><script type="application/ld+json">
    {"@type": "Article", "headline": "LD Title",
     "author": [{"givenName": "Jane", "familyName": "Smith"}],
     "datePublished": "2020-01-02"}
    </script></head><body></body></html>
    """

    class TextRes(_Res):
        text = html

    monkeypatch.setattr(
        web_sources,
        "safe_fetch",
        lambda url, timeout=8.0: TextRes(200, headers={"content-type": "text/html"}),
    )
    result = web_sources.verify_web_source(
        {"raw": "x", "title": "LD Title", "url": "https://example.com/p"}
    )
    assert result["title"] == "LD Title"
    assert result["authors"] == ["Jane Smith"]
    assert result["year"] == 2020


def test_private_url_blocked_before_fetch(monkeypatch):
    called = {"n": 0}

    def should_not_run(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(web_sources, "safe_fetch", should_not_run)
    assert (
        web_sources.verify_web_source({"raw": "x", "title": "t", "url": "http://127.0.0.1/x"})
        is None
    )
    assert called["n"] == 0


# --- publication notices ----------------------------------------------------


def test_publication_updates_not_available_without_record():
    assert check_publication_updates({"doi": None})["status"] == "not_available"


def test_publication_updates_returns_notices(monkeypatch):
    record = {
        "doi": "10.1/x",
        "publicationStatusCheckedAt": "2026-01-01T00:00:00+00:00",
        "publicationUpdates": [{"type": "retraction"}],
    }
    monkeypatch.setattr(
        "cite_analyser.publication_updates.lookup_doi_crossref", lambda doi, mailto=None: record
    )
    result = check_publication_updates({"doi": "10.1/x"})
    assert result["status"] == "checked"
    assert result["updates"][0]["type"] == "retraction"


def test_publication_updates_unavailable_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("cite_analyser.publication_updates.lookup_doi_crossref", boom)
    assert check_publication_updates({"doi": "10.1/x"})["status"] == "unavailable"
