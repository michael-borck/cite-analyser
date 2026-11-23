"""Title normalisation and duplicate detection — port of cite-sight's
``references/matching.ts`` (normalizeTitle) and ``references/duplicates.ts``."""

from __future__ import annotations

import unicodedata

import regex

from .cross_reference import author_key


def normalize_title(value: str) -> str:
    """NFKD-folded, marks stripped, punctuation to spaces, collapsed."""
    folded = unicodedata.normalize("NFKD", value.lower())
    folded = regex.sub(r"\p{M}", "", folded)
    folded = regex.sub(r"[^\p{L}\p{N}\s]", " ", folded)
    return regex.sub(r"\s+", " ", folded).strip()


def find_duplicate_references(references: list[dict]) -> set[int]:
    """Indices of entries repeating an earlier entry — same first author,
    year, and first 12 normalised title words. Reported on every occurrence
    after the first (hand-built lists accumulate these across editing
    sessions)."""
    seen: dict[str, int] = {}
    duplicates: set[int] = set()
    for index, ref in enumerate(references):
        key = "|".join(
            (
                author_key((ref["authors"][0] if ref["authors"] else ref["raw"]).split(",")[0]),
                str(ref["year"] if ref["year"] is not None else "nd"),
                " ".join(normalize_title(ref["title"] or ref["raw"]).split(" ")[:12]),
            )
        )
        if key in seen:
            duplicates.add(index)
        else:
            seen[key] = index
    return duplicates
