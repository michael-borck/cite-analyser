"""The analysis pipeline: extract → cross-reference → duplicates → (optional)
verification — the same order as cite-sight's pipeline.ts."""

from __future__ import annotations

from .cross_reference import cross_reference_check
from .duplicates import find_duplicate_references
from .extractor import extract_references
from .verifier import VerifyOptions, verify_references


def analyze(
    text: str,
    verify: bool = False,
    mailto: str | None = None,
    check_urls: bool = True,
    check_doi: bool = True,
    semantic_scholar_api_key: str | None = None,
    openalex_api_key: str | None = None,
) -> dict:
    """Run the analysis stages over document text. Verification (external
    academic lookups) is opt-in."""
    extracted = extract_references(text)
    references = extracted["references"]
    in_text = extracted["inTextCitations"]

    # Source-list heuristic (pipeline.ts): a document with a bibliography but
    # zero in-text citations is likely a collated reference list — unmatched
    # alone is not evidence of anything, so skip the cross-check.
    source_list_likely = len(references) >= 3 and not in_text
    if source_list_likely:
        cross = {"unmatchedBibliography": [], "unmatchedInText": []}
    else:
        cross = cross_reference_check(references, in_text)

    result = {
        "references": references,
        "inTextCitations": in_text,
        "crossReference": cross,
        "duplicates": sorted(find_duplicate_references(references)),
        "sourceListLikely": source_list_likely,
    }
    if verify:
        result["verifications"] = verify_references(
            references,
            VerifyOptions(
                mailto=mailto,
                check_urls=check_urls,
                check_doi=check_doi,
                semantic_scholar_api_key=semantic_scholar_api_key,
                openalex_api_key=openalex_api_key,
            ),
        )
    return result
