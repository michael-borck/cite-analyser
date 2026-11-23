"""Unit tests for the extractor port (hand cases; conformance lives separately)."""

from cite_analyser.extractor import detect_style, extract_references, majority_style

APA_ESSAY = """# Essay

Prior work argues dialogue matters (Vygotsky, 1978). Laurillard's (2002)
framework agrees, as do Mollick and Mollick (2023).

## References

Borck, M. (2026). *Conversation, Not Delegation.* KDP.

Laurillard, D. (2002). *Rethinking University Teaching* (2nd ed.). RoutledgeFalmer.

Vygotsky, L. S. (1978). *Mind in Society.* Harvard University Press.
"""


def test_detect_style_signatures():
    assert detect_style("Smith, J. (2020). Thinking. *Journal, 1*(2), 3-4.") == "apa"
    assert detect_style('Smith, John. "Title." *Journal* 12 (2023): 102274.') == "chicago"
    assert detect_style('Smith, John. "Title." *Journal*, vol. 12, 2020.') == "mla"
    assert detect_style("some unstructured fragment") == "unknown"


def test_extracts_references_and_in_text():
    result = extract_references(APA_ESSAY)
    assert [r["authors"][0] for r in result["references"]] == [
        "Borck, M.",
        "Laurillard, D.",
        "Vygotsky, L. S.",
    ]
    assert majority_style(result["references"]) == "apa"
    # all three in-text shapes: parenthetical, narrative, and-ed pair
    raws = [c["raw"] for c in result["inTextCitations"]]
    assert "(Vygotsky, 1978)" in raws
    assert "Laurillard's (2002)" in raws or "Laurillard (2002)" in raws
    assert any("Mollick" in r for r in raws)


def test_no_references_section_yields_empty():
    result = extract_references("Just prose, no bibliography at all.")
    assert result["references"] == []
    assert result["inTextCitations"] == []


def test_parses_doi_and_year_suffix():
    result = extract_references(
        "## References\n\n"
        "Author, A. (2023a). Title. *Journal, 4*, 1-2. "
        "https://doi.org/10.1234/abcde\n"
    )
    ref = result["references"][0]
    assert ref["doi"] == "10.1234/abcde"
    assert ref["year"] == 2023
    assert ref["yearSuffix"] == "a"


def test_analyze_mirrors_reference_analysis_result():
    """The analyse() block mirrors cite-sight's ReferenceAnalysisResult so the
    frozen server can delegate without an adapter guessing field names."""
    from cite_analyser.analysis import analyze

    result = analyze(APA_ESSAY)
    assert result["totalReferences"] == 3
    assert result["detectedStyle"] == "apa"
    assert result["verifications"] == []  # verification is opt-in
    for key in (
        "verifiedCount",
        "suspiciousCount",
        "notFoundCount",
        "unverifiedCount",
        "brokenUrlCount",
    ):
        assert result[key] == 0
    assert result["inTextCheckSkipped"] is False
    assert result["sourceListLikely"] is False


def test_analyze_skips_cross_reference_when_asked():
    from cite_analyser.analysis import analyze

    result = analyze(APA_ESSAY, check_in_text=False)
    assert result["inTextCheckSkipped"] is True
    assert result["crossReference"] == {
        "unmatchedBibliography": [],
        "unmatchedInText": [],
    }


def test_reference_list_document_type_skips_in_text_check():
    from cite_analyser.analysis import analyze

    result = analyze(APA_ESSAY, document_type="reference-list")
    assert result["inTextCheckSkipped"] is True
