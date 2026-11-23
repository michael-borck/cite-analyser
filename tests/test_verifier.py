"""Verifier tests: verdict matrix, matching corroboration, provider parsing,
and the degraded-lookup protection — cases lifted from the TS engine's
verification-verdicts / provider-contracts / matching test suites."""

import pytest

from cite_analyser import verifier
from cite_analyser.lookup_error import LookupError, reason_from_status
from cite_analyser.matching import (
    author_corroboration,
    character_similarity,
    repaired_title_query,
    subtitle_variant,
    title_token_conflict,
    year_corroboration,
)
from cite_analyser.providers import _crossref_item_to_work
from cite_analyser.verifier import VerifyOptions, title_similarity, verify_single_reference


def ref(**kw):
    base = {"raw": "x", "authors": ["Smith, J."], "title": "A Study of Things", "year": 2020}
    base.update(kw)
    return base


def work(title="A Study of Things", authors=None, year=2020, **kw):
    from cite_analyser.providers import work as mk

    return mk(title=title, authors=authors or ["Smith, John"], year=year, source="crossref", **kw)


# --- matching ---------------------------------------------------------------


def test_author_corroboration_rules():
    assert author_corroboration(["Smith, J."], ["Smith, John"]) == "match"
    assert author_corroboration(["Jones, K."], ["Smith, John"]) == "mismatch"
    # a cited surname missing from the recorded set is a mismatch, even when
    # one surname coincides (["Smith","Jones"] vs ["Smith"])
    assert author_corroboration(["Smith, J.", "Jones, K."], ["Smith, John"]) == "mismatch"
    assert author_corroboration([], ["Smith, John"]) == "unknown"
    # initials conflict: cited J., recorded William
    assert author_corroboration(["Smith, J."], ["Smith, William"]) == "mismatch"


def test_subtitle_variant():
    assert subtitle_variant(
        "To Trust or to Think: Cognitive Forcing Functions", "To Trust or to Think"
    )
    assert not subtitle_variant("One Title", "Another Title")


def test_title_token_conflict_critical_words_and_numbers():
    assert title_token_conflict("A Study of 3 Things", "A Study of 4 Things")
    assert not title_token_conflict("A Study of Three Things", "A Study of Three Things")
    assert title_token_conflict("Not the Real Paper", "The Real Paper")


def test_year_corroboration():
    assert year_corroboration(2020, 2020) == "match"
    assert year_corroboration(2020, 2021) == "match"  # edition tolerance
    assert year_corroboration(2020, 2023) == "mismatch"
    assert year_corroboration(None, 2020) == "unknown"


def test_character_similarity_ratio_guard():
    assert character_similarity("same title here", "same title here") == 1.0
    assert character_similarity("short", "a much longer different title entirely") == 0.0


def test_repaired_title_query_keeps_distinctive_words():
    assert repaired_title_query("A Study of Thnigs") == "study thnigs"


# --- title similarity -------------------------------------------------------


def test_title_similarity_exact_and_rescue():
    assert title_similarity("A Study of Things", "A Study of Things") == 1.0
    # one typo in a long title, same tokens otherwise -> rescued by character similarity
    assert (
        title_similarity(
            "Conversation, Not Delegation: Student AI Use",
            "Conversation, Not Delegations: Student AI Use",
        )
        >= 0.9
    )


# --- verdict matrix ---------------------------------------------------------


_PROVIDER_SEAMS = (
    "resolve_doi",
    "lookup_arxiv_id",
    "search_crossref",
    "search_openalex",
    "search_semantic_scholar",
    "search_arxiv",
    "search_datacite",
    "search_europe_pmc",
    "verify_web_source",
    "check_url",
    "check_publication_updates",
)


def _silence_web(monkeypatch):
    """URL checks, web-source lookups and notice checks answered locally."""
    monkeypatch.setattr(verifier, "verify_web_source", lambda *a, **k: None)
    monkeypatch.setattr(
        verifier,
        "check_url",
        lambda url: {"url": url, "status": "blocked", "statusCode": 403},
    )
    monkeypatch.setattr(
        verifier,
        "check_publication_updates",
        lambda *a, **k: {"status": "not_available", "updates": []},
    )


@pytest.fixture
def no_network(monkeypatch):
    """Every provider call raises a rate-limit — the degraded-lookup world."""

    def limited(*args, **kwargs):
        raise LookupError("crossref", "rate_limited")

    for seam in _PROVIDER_SEAMS[:8]:
        monkeypatch.setattr(verifier, seam, limited)
    _silence_web(monkeypatch)


def _verify(reference, monkeypatch=None, search_results=None, doi_result=None):
    """Verify with only the crossref/DOI seams populated; every other provider
    answers empty so no test ever touches the network."""
    monkeypatch.setattr(verifier, "resolve_doi", lambda *a, **k: None)
    monkeypatch.setattr(verifier, "lookup_arxiv_id", lambda *a, **k: None)
    for seam in (
        "search_openalex",
        "search_semantic_scholar",
        "search_arxiv",
        "search_datacite",
        "search_europe_pmc",
    ):
        monkeypatch.setattr(verifier, seam, lambda *a, **k: [])
    monkeypatch.setattr(verifier, "search_crossref", lambda *a, **k: [])
    _silence_web(monkeypatch)
    if search_results is not None:
        monkeypatch.setattr(verifier, "search_crossref", lambda *a, **k: search_results)
    if doi_result is not None:
        monkeypatch.setattr(verifier, "resolve_doi", lambda *a, **k: doi_result)
    return verify_single_reference(reference, VerifyOptions())


def test_verified_on_strong_match(monkeypatch):
    v = _verify(ref(), monkeypatch, search_results=[work()])
    assert v["status"] == "verified"
    assert v["confidenceScore"] == 0.92
    assert v["matchCategory"] == "exact"
    assert v["evidence"]["existence"] == "found"


def test_likely_valid_on_title_variant(monkeypatch):
    v = _verify(
        ref(title="A Study of Things: Extended Edition"),
        monkeypatch,
        search_results=[work()],
    )
    assert v["status"] == "likely_valid"
    assert "title_variant" in v["flags"]
    assert v["matchCategory"] == "metadata_drift"


def test_suspicious_on_weak_match_with_right_author(monkeypatch):
    # the "real author, fake title" hallucination signature: partial title
    # overlap (similarity in [0.3, 0.6)) with a corroborating author
    v = _verify(
        ref(title="A Study"),
        monkeypatch,
        search_results=[work(title="A Study of Things")],
    )
    assert v["status"] == "suspicious"
    assert "weak_match" in v["flags"]
    assert v["matchCategory"] == "match_dubious"


def test_doi_resolved_with_metadata_verifies(monkeypatch):
    v = _verify(ref(doi="10.1/x"), monkeypatch, doi_result=work())
    assert v["status"] == "verified"
    assert v["confidenceScore"] == 0.97


def test_doi_title_mismatch_is_suspicious(monkeypatch):
    v = _verify(ref(doi="10.1/x"), monkeypatch, doi_result=work(title="A Totally Different Work"))
    assert v["status"] == "suspicious"
    assert "doi_title_mismatch" in v["flags"]
    assert v["matchCategory"] == "conflict"


def test_doi_without_metadata_is_likely_valid_unconfirmed(monkeypatch):
    v = _verify(ref(doi="10.1/x"), monkeypatch, doi_result=work(title=""))
    assert v["status"] == "likely_valid"
    assert "doi_unconfirmed" in v["flags"]


def test_degraded_lookup_never_accuses(monkeypatch):
    # one provider offers only an uncorroborated weak candidate (would read
    # 'suspicious'); another is rate-limited — an accusation must not rest on
    # a partial lookup, so the verdict becomes 'unverified' (retry).
    def weak_crossref(*args, **kwargs):
        return [work(title="A Study of Things", authors=["Other, A."], year=1999)]

    def limited(*args, **kwargs):
        raise LookupError("datacite", "rate_limited")

    monkeypatch.setattr(verifier, "resolve_doi", lambda *a, **k: None)
    monkeypatch.setattr(verifier, "lookup_arxiv_id", lambda *a, **k: None)
    monkeypatch.setattr(verifier, "search_crossref", weak_crossref)
    monkeypatch.setattr(verifier, "search_openalex", lambda *a, **k: [])
    monkeypatch.setattr(verifier, "search_semantic_scholar", lambda *a, **k: [])
    monkeypatch.setattr(verifier, "search_arxiv", lambda *a, **k: [])
    monkeypatch.setattr(verifier, "search_datacite", limited)
    monkeypatch.setattr(verifier, "search_europe_pmc", lambda *a, **k: [])
    _silence_web(monkeypatch)
    v = verify_single_reference(ref(title="A Study", authors=["Unmatchable, U."]), VerifyOptions())
    assert v["status"] == "unverified"
    assert "degraded_lookup" in v["flags"]


def test_all_providers_rate_limited_no_match_is_unverified(no_network):
    v = verify_single_reference(ref(title="Anything"), VerifyOptions())
    assert v["status"] == "unverified"
    assert "verification_unavailable" in v["flags"]
    assert "rate_limited:crossref" in v["flags"]
    assert v["evidence"]["existence"] == "unknown"


@pytest.fixture
def clean_empty_lookups(monkeypatch):
    """Every provider answers cleanly with no results — a genuine miss."""

    for seam in ("resolve_doi", "lookup_arxiv_id"):
        monkeypatch.setattr(verifier, seam, lambda *a, **k: None)
    for seam in (
        "search_crossref",
        "search_openalex",
        "search_semantic_scholar",
        "search_arxiv",
        "search_datacite",
        "search_europe_pmc",
    ):
        monkeypatch.setattr(verifier, seam, lambda *a, **k: [])
    _silence_web(monkeypatch)


def test_grey_literature_not_found_without_accusation(clean_empty_lookups):
    grey = {
        "raw": "Thoughtworks. https://example.com/report",
        "authors": ["Thoughtworks"],
        "title": "",
        "year": None,
        "url": "https://example.com/report",
    }
    v = verify_single_reference(grey, VerifyOptions())
    assert v["status"] == "not_found"
    assert "grey_literature" in v["flags"]
    assert v["matchCategory"] == "not_indexed_expected"


def test_offline_is_format_only():
    v = verify_single_reference(ref(), VerifyOptions(offline=True))
    assert v["status"] == "format_only"
    assert "offline" in v["flags"]


def test_intrinsic_flags(no_network):
    v = verify_single_reference(
        ref(year=2100, authors=["Nobody, N."], title="zzz"), VerifyOptions()
    )
    assert "future_date" in v["flags"]
    assert "no_doi" in v["flags"]


# --- provider parsing -------------------------------------------------------


def test_reason_from_status():
    assert reason_from_status(429) == "rate_limited"
    assert reason_from_status(503) == "server_error"
    assert reason_from_status(404) == "unknown"


def test_crossref_item_parsing():
    item = {
        "title": ["A Paper"],
        "author": [{"family": "Smith", "given": "Jane"}, {"name": "OECD"}],
        "published": {"date-parts": [[2021, 3]]},
        "DOI": "10.1/x",
        "URL": "https://doi.org/10.1/x",
        "container-title": ["Journal"],
        "volume": "1",
        "issue": "2",
        "page": "3-4",
        "is-referenced-by-count": 7,
        "type": "journal-article",
        "updated-by": [{"DOI": "10.1/retraction", "type": "retraction"}],
    }
    wk = _crossref_item_to_work(item)
    assert wk["authors"] == ["Smith, Jane", "OECD"]
    assert wk["year"] == 2021
    assert wk["citationCount"] == 7
    assert wk["publicationUpdates"][0]["type"] == "retraction"
