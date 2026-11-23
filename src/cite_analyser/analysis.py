"""The analysis pipeline (offline stages): extract → cross-reference →
duplicates — the same order as cite-sight's pipeline.ts minus verification,
which arrives with the verifier port."""

from __future__ import annotations

from .cross_reference import cross_reference_check
from .duplicates import find_duplicate_references
from .extractor import extract_references


def analyze(text: str) -> dict:
    """Run the offline analysis stages over document text."""
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

    return {
        "references": references,
        "inTextCitations": in_text,
        "crossReference": cross,
        "duplicates": sorted(find_duplicate_references(references)),
        "sourceListLikely": source_list_likely,
    }
