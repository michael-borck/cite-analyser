"""Provider parser tests for OpenAlex, Semantic Scholar, arXiv, Europe PMC —
shapes lifted from the TS engine's provider-contracts test suite."""

import pytest

from cite_analyser import providers


class _Res:
    def __init__(self, status=200, json_data=None, text="", headers=None):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """No polite-pacing sleeps in tests."""
    monkeypatch.setattr(providers, "_delay", lambda s: None)
    providers.clear_run_state()


def test_openalex_parsing_and_doi_stripping(monkeypatch):
    payload = {
        "results": [
            {
                "display_name": "To Trust or to Think",
                "doi": "https://doi.org/10.1145/1234",
                "publication_year": 2021,
                "authorships": [
                    {"author": {"display_name": "Zana Buçinca"}},
                    {"author": {"display_name": "Krzysztof Gajos"}},
                ],
                "primary_location": {"source": {"display_name": "CSCW"}},
                "cited_by_count": 42,
                "abstract_inverted_index": {"Hello": [0], "world": [1], "again": [2]},
            }
        ]
    }
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: _Res(json_data=payload))
    works = providers.search_openalex("trust think")
    wk = works[0]
    assert wk["doi"] == "10.1145/1234"
    assert wk["authors"] == ["Zana Buçinca", "Krzysztof Gajos"]
    assert wk["journal"] == "CSCW"
    assert wk["abstract"] == "Hello world again"
    assert wk["source"] == "openalex"


def test_openalex_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Res(status=429, headers={"X-RateLimit-Remaining": "5"})
        return _Res(json_data={"results": []})

    monkeypatch.setattr(providers.httpx, "get", flaky)
    assert providers.search_openalex("q") == []
    assert calls["n"] == 2


def test_openalex_daily_budget_exhausted_fails_fast(monkeypatch):
    calls = {"n": 0}

    def exhausted(*a, **k):
        calls["n"] += 1
        return _Res(status=429, headers={"X-RateLimit-Remaining": "0"})

    monkeypatch.setattr(providers.httpx, "get", exhausted)
    with pytest.raises(providers.LookupError) as exc:
        providers.search_openalex("q")
    assert exc.value.reason == "rate_limited"
    assert calls["n"] == 1  # no retries once the daily budget is gone


def test_semantic_scholar_parsing(monkeypatch):
    payload = {
        "data": [
            {
                "title": "Paper",
                "authors": [{"name": "Jane Smith"}],
                "year": 2022,
                "externalIds": {"DOI": "10.1/x"},
                "journal": {"name": "J"},
                "citationCount": 3,
                "abstract": "An abstract.",
            }
        ]
    }
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: _Res(json_data=payload))
    wk = providers.search_semantic_scholar("paper")[0]
    assert wk["doi"] == "10.1/x"
    assert wk["source"] == "semantic_scholar"
    assert wk["citationCount"] == 3


def test_semantic_scholar_sends_api_key(monkeypatch):
    captured = {}

    def capture(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        return _Res(json_data={"data": []})

    monkeypatch.setattr(providers.httpx, "get", capture)
    providers.search_semantic_scholar("q", api_key="sekrit")
    assert captured["headers"]["x-api-key"] == "sekrit"


def test_extract_arxiv_id():
    assert providers.extract_arxiv_id("arXiv:2410.01396") == "2410.01396"
    # bare ID accepted only when the string mentions arXiv (as the URL does)
    assert providers.extract_arxiv_id("arxiv.org/abs/2410.01396v2") == "2410.01396"
    assert providers.extract_arxiv_id("see arxiv for 2410.01396") == "2410.01396"
    assert providers.extract_arxiv_id("pages 2205.11 through 2205.40") is None
    assert providers.extract_arxiv_id("no id here") is None


def test_arxiv_feed_parsing_and_work_fields():
    xml = """<?xml version="1.0"?><feed>
    <entry>
      <id>http://arxiv.org/abs/2410.01396v1</id>
      <published>2024-10-02T00:00:00Z</published>
      <title>Can we delegate learning to automation?
      A comparative study</title>
      <author><name>A. Stojanov</name></author>
      <author><name>N. Gr&#39;nbak</name></author>
    </entry>
    </feed>"""
    entries = providers.parse_arxiv_feed(xml)
    assert len(entries) == 1
    assert entries[0]["year"] == 2024
    assert entries[0]["title"].startswith("Can we delegate learning to automation?")
    # the five entities the TS engine decodes are handled
    assert any("Gr'nbak" in author for author in entries[0]["authors"])
    wk = providers._arxiv_entry_to_work(entries[0])
    assert wk["doi"] == "10.48550/arXiv.2410.01396"
    assert wk["url"] == "https://arxiv.org/abs/2410.01396"
    assert wk["journal"] == "arXiv"


def test_arxiv_lookup_unknown_id_returns_none(monkeypatch):
    xml = "<feed><entry><title>Error</title><id></id></entry></feed>"
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: _Res(text=xml))
    assert providers.lookup_arxiv_id("9999.99999") is None


def test_europepmc_parsing(monkeypatch):
    payload = {
        "resultList": {
            "result": [
                {
                    "id": "123",
                    "source": "MED",
                    "title": "A biomedical paper",
                    "doi": "10.1/bio",
                    "pubYear": "2020",
                    "authorList": {
                        "author": [
                            {"lastName": "Smith", "firstName": "Jane"},
                            {"collectiveName": "WHO Group"},
                        ]
                    },
                    "journalInfo": {"journal": {"title": "Lancet"}, "volume": "5"},
                    "pageInfo": "1-9",
                    "abstractText": "<p>An <b>abstract</b>.</p>",
                }
            ]
        }
    }
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: _Res(json_data=payload))
    wk = providers.search_europe_pmc("biomedical")[0]
    assert wk["authors"] == ["Smith, Jane", "WHO Group"]
    assert wk["year"] == 2020
    assert wk["abstract"] == "An abstract ."
    assert wk["url"] == "https://europepmc.org/article/MED/123"
    assert wk["source"] == "europe_pmc"
