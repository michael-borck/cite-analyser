"""The analysis pipeline: extract → cross-reference → duplicates → (optional)
verification — the same order as cite-sight's pipeline.ts."""

from __future__ import annotations

from .cross_reference import cross_reference_check
from .duplicates import find_duplicate_references
from .extractor import extract_references, majority_style
from .verifier import VerifyOptions, verify_references


def analyze(
    text: str,
    verify: bool = False,
    mailto: str | None = None,
    check_urls: bool = True,
    check_doi: bool = True,
    semantic_scholar_api_key: str | None = None,
    openalex_api_key: str | None = None,
    citation_style: str = "auto",
    check_in_text: bool = True,
    document_type: str | None = None,
) -> dict:
    """Run the analysis stages over document text.

    The output block mirrors cite-sight's ``ReferenceAnalysisResult`` so the
    frozen engine's server can delegate here without an adapter guessing at
    field names. Verification (external lookups) is opt-in."""
    extracted = extract_references(text)
    references = extracted["references"]
    in_text = extracted["inTextCitations"]
    detected_style = majority_style(references)

    # Source-list heuristic (pipeline.ts): a bibliography with zero in-text
    # citations is likely a collated reference list — the cross-check is
    # meaningless there (and a bare list cannot have orphans).
    source_list_likely = len(references) >= 3 and not in_text
    in_text_check_skipped = (
        not check_in_text or document_type == "reference-list" or source_list_likely
    )
    if in_text_check_skipped:
        cross = {"unmatchedBibliography": [], "unmatchedInText": []}
    else:
        cross = cross_reference_check(references, in_text)

    verifications = (
        verify_references(
            references,
            VerifyOptions(
                mailto=mailto,
                check_urls=check_urls,
                check_doi=check_doi,
                semantic_scholar_api_key=semantic_scholar_api_key,
                openalex_api_key=openalex_api_key,
                citation_style=citation_style,
            ),
        )
        if verify
        else []
    )

    def count(statuses: tuple[str, ...]) -> int:
        return sum(1 for v in verifications if v["status"] in statuses)

    return {
        "references": references,
        "inTextCitations": in_text,
        "crossReference": cross,
        "duplicates": sorted(find_duplicate_references(references)),
        "verifications": verifications,
        "detectedStyle": detected_style,
        "totalReferences": len(references),
        "verifiedCount": count(("verified", "likely_valid")),
        "suspiciousCount": count(("suspicious",)),
        "notFoundCount": count(("not_found",)),
        "unverifiedCount": count(("unverified",)),
        "brokenUrlCount": sum(
            1 for v in verifications if (v.get("urlCheck") or {}).get("status") == "dead"
        ),
        "sourceListLikely": source_list_likely,
        "inTextCheckSkipped": in_text_check_skipped,
    }
