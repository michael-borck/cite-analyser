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
