"""Claim–evidence checking tests — extraction, retrieval, validation,
orchestration, and the student-facing copy contract."""

import base64
import json

import cite_analyser.api as api_mod
from cite_analyser.claims import (
    DISCLAIMER,
    MODE_QUESTION,
    NO_SOURCE_REASON,
    STATUS_LABELS,
    SourceDocument,
    check_claims,
    claim_prompt,
    claim_spans,
    retrieve_passages,
    source_passages,
    split_sentences,
    validate_claim_response,
)

DOC = """# Essay

Blue light increased seed germination in the trial (Vygotsky, 1978).

Leaf size was unaffected by the treatment (Thaler, 2008).

## References

Vygotsky, L. S. (1978). *Mind in Society.* Harvard University Press.

Thaler, R. H. (2008). *Nudge.* Yale University Press.
"""

SOURCE_VYGOTSKY = (
    "In the controlled trial, blue light increased seed germination after seven days. "
    "The measurement window was short and the sample was small."
)

SOURCE_THALER = "Leaf size showed no measurable change under the treatment condition."


def canonical_json(status, quotes, reason="Because the passage says so."):
    return json.dumps(
        {
            "status": status,
            "reason": reason,
            "evidence": [{"passageId": pid, "quote": quote} for pid, quote in quotes],
        }
    )


# ---------------------------------------------------------------- copy


def test_student_facing_question_and_labels():
    assert MODE_QUESTION == "Does your supplied source support this sentence?"
    assert STATUS_LABELS["insufficient_evidence"] == "Your source doesn't say"
    assert "best-effort" in DISCLAIMER and "yourself" in DISCLAIMER


# ---------------------------------------------------------------- sentences


def test_split_sentences_ignores_abbreviations_and_initials():
    spans = split_sentences(
        "As shown by Vygotsky, L. S. (1978), learning is social. That claim is testable. See e.g. Thaler for more."
    )
    texts = [text for _, text in spans]
    assert texts[0].startswith("As shown by Vygotsky")
    assert len(texts) == 3


def test_claim_spans_pair_citations_with_sentences():
    from cite_analyser.extractor import extract_references

    extracted = extract_references(DOC)
    claims = claim_spans(DOC, extracted["inTextCitations"])
    texts = [c["claim"] for c in claims]
    assert any("Blue light increased seed germination" in t for t in texts)
    assert any("Leaf size was unaffected" in t for t in texts)
    assert len(claims) == 2


# ---------------------------------------------------------------- retrieval


def test_source_passages_and_retrieval():
    passages = source_passages(SOURCE_VYGOTSKY)
    assert passages[0]["start"] == 0 and passages[0]["page"] is None
    hits = retrieve_passages("Blue light increased seed germination", passages)
    assert hits and "germination" in hits[0]["text"]


def test_retrieval_miss_is_empty_not_contradiction():
    passages = source_passages(SOURCE_VYGOTSKY)
    assert retrieve_passages("Quantum tunnelling in semiconductors", passages) == []


# ---------------------------------------------------------------- validation


def _passages():
    return source_passages(SOURCE_VYGOTSKY)


def test_validation_accepts_verbatim_evidence():
    parsed = _passages()
    quote = "blue light increased seed germination after seven days"
    verdict = validate_claim_response(canonical_json("supported", [("p0", quote)]), parsed)
    assert verdict["status"] == "supported"
    assert verdict["evidence"][0]["quote"] == quote
    assert verdict["evidence"][0]["start"] >= 0


def test_validation_rejects_non_verbatim_quote():
    parsed = _passages()
    verdict = validate_claim_response(
        canonical_json("supported", [("p0", "Blue light caused plants to grow taller.")]),
        parsed,
    )
    assert verdict["status"] == "unavailable"


def test_validation_rejects_unknown_passage_id():
    parsed = _passages()
    verdict = validate_claim_response(
        canonical_json("supported", [("p99", "blue light increased seed germination")]),
        parsed,
    )
    assert verdict["status"] == "unavailable"


def test_validation_rejects_bad_status_and_heavy_evidence():
    parsed = _passages()
    quote = "blue light increased seed germination after seven days"
    assert (
        validate_claim_response(canonical_json("maybe", [("p0", quote)]), parsed)["status"]
        == "unavailable"
    )
    heavy = json.dumps(
        {
            "status": "supported",
            "reason": "x",
            "evidence": [{"passageId": "p0", "quote": quote}] * 5,
        }
    )
    assert validate_claim_response(heavy, parsed)["status"] == "unavailable"


def test_validation_allows_insufficient_without_evidence():
    parsed = _passages()
    verdict = validate_claim_response(canonical_json("insufficient_evidence", []), parsed)
    assert verdict["status"] == "insufficient_evidence"


def test_validation_rejects_malformed_json():
    assert validate_claim_response("not json at all", _passages())["status"] == "unavailable"


def test_prompt_contains_framing_and_untrusted_data_marker():
    prompt = claim_prompt("A statement.", _passages())
    assert "untrusted statement/source text" in prompt
    assert "insufficient_evidence" in prompt
    assert '"statement": "A statement."' in prompt


# ---------------------------------------------------------------- orchestration


def _run(chat_response, sources=None, runs=1):
    from cite_analyser.extractor import extract_references

    extracted = extract_references(DOC)
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return chat_response if isinstance(chat_response, str) else chat_response(len(calls))

    if sources is None:
        sources = [
            SourceDocument(1, "vygotsky.pdf", SOURCE_VYGOTSKY),
            SourceDocument(2, "thaler.pdf", SOURCE_THALER),
        ]
    result = check_claims(DOC, extracted["references"], extracted["inTextCitations"], sources, chat)
    return result, calls


def test_check_claims_supported_and_counts():
    quote = "blue light increased seed germination after seven days"

    def responder(n):
        # first claim supported, second insufficient
        if n == 1:
            return canonical_json("supported", [("p0", quote)])
        return canonical_json("insufficient_evidence", [])

    result, calls = _run(responder)
    assert result["question"] == MODE_QUESTION
    assert result["disclaimer"] == DISCLAIMER
    assert len(result["findings"]) == 2
    assert result["findings"][0]["status"] == "supported"
    assert result["findings"][0]["evidence"][0]["quote"] == quote
    assert result["counts"]["supported"] == 1
    assert result["counts"]["insufficient_evidence"] == 1
    assert len(calls) == 2


def test_missing_source_is_honest_not_a_guess():
    result, calls = _run(canonical_json("contradicted", []), sources=[])
    assert all(f["status"] == "insufficient_evidence" for f in result["findings"])
    assert all(f["reason"] == NO_SOURCE_REASON for f in result["findings"])
    assert calls == []  # nothing was sent to the model


def test_model_cannot_invent_evidence():
    result, _ = _run(canonical_json("supported", [("p0", "a totally invented quotation")]))
    assert all(f["status"] == "unavailable" for f in result["findings"])
    assert all(f["evidence"] == [] for f in result["findings"])


def test_contradiction_requires_verbatim_opposite():
    # exact-case substring of the supplied Thaler source
    quote = "Leaf size showed no measurable change"
    result, _ = _run(canonical_json("contradicted", [("p0", quote)]))
    statuses = {f["status"] for f in result["findings"]}
    assert "contradicted" in statuses
    # the Vygotsky claim cannot match that quote, so it stays honest
    assert "unavailable" in statuses or "insufficient_evidence" in statuses


# ---------------------------------------------------------------- API


def test_api_claims_shape(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        api_mod,
        "chat",
        lambda base_url, model, prompt, api_key="": canonical_json(
            "supported", [("p0", "blue light increased seed germination after seven days")]
        ),
    )
    client = TestClient(api_mod.app)
    resp = client.post(
        "/claims",
        json={
            "document": {
                "name": "essay.md",
                "data_base64": base64.b64encode(DOC.encode()).decode(),
            },
            "sources": [
                {
                    "reference": 1,
                    "name": "vygotsky.txt",
                    "data_base64": base64.b64encode(SOURCE_VYGOTSKY.encode()).decode(),
                }
            ],
            "provider": {"model": "qwen3:4b"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == MODE_QUESTION
    assert body["counts"]["supported"] >= 1


def test_api_claims_llm_failure_is_502(monkeypatch):
    from fastapi.testclient import TestClient

    from cite_analyser.llm import LLMError

    monkeypatch.setattr(
        api_mod,
        "chat",
        lambda *a, **k: (_ for _ in ()).throw(LLMError("endpoint down")),
    )
    client = TestClient(api_mod.app)
    resp = client.post(
        "/claims",
        json={
            "document": {
                "name": "essay.md",
                "data_base64": base64.b64encode(DOC.encode()).decode(),
            },
            "sources": [
                {
                    "reference": 1,
                    "name": "vygotsky.txt",
                    "data_base64": base64.b64encode(SOURCE_VYGOTSKY.encode()).decode(),
                }
            ],
            "provider": {"model": "qwen3:4b"},
        },
    )
    assert resp.status_code == 502
