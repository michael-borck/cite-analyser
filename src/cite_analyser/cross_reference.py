"""Cross-reference check — port of cite-sight's ``references/crossReference.ts``.

Exact author-date identification claims citations first; near matches
(hand-typing slips: one-edit surnames, transpositions, year off by one,
second-author citations) only consider what is left, so a suggestion can
never steal a citation another entry identifies exactly.
"""

from __future__ import annotations

import unicodedata

import regex

from .extractor import InText, ParsedRef  # type-aliased dicts, see extractor.py


def author_key(author: str) -> str:
    """Normalised comparison form of one author name."""
    cleaned = regex.sub(r"\s+et\s+al\.?$", "", author, flags=_RE_I)
    cleaned = regex.sub(r"['’]s$", "", cleaned, flags=_RE_I)
    cleaned = unicodedata.normalize("NFC", cleaned).lower()
    return regex.sub(r"[^\p{L}]", "", cleaned)


_RE_I = regex.IGNORECASE


def reference_matches_citation(ref: ParsedRef, cite: InText) -> bool:
    """Exact author-date identification (first author, year, year suffix)."""
    ref_author = author_key((ref["authors"][0] if ref["authors"] else "").split(",")[0])
    cite_author = author_key(cite["authors"][0] if cite["authors"] else "")
    if not ref_author or not cite_author or ref_author != cite_author:
        return False
    if cite["year"] is not None and ref["year"] != cite["year"]:
        return False
    return not (
        cite["year"] is not None and ref.get("yearSuffix", "") != cite.get("yearSuffix", "")
    )


def within_edit_distance(a: str, b: str, max_edits: int) -> bool:
    """Bounded Levenshtein: True when a and b differ by at most `max` edits,
    early-exiting as soon as the bound is provably exceeded."""
    if abs(len(a) - len(b)) > max_edits:
        return False
    if a == b:
        return True
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        row = [i]
        best = i
        for j in range(1, len(b) + 1):
            row.append(
                min(
                    row[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (0 if a[i - 1] == b[j - 1] else 1),
                )
            )
            best = min(best, row[j])
        if best > max_edits:
            return False
        previous = row
    return previous[len(b)] <= max_edits


def _cited_surnames(authors: list[str]) -> list[str]:
    """All surnames in an author list, normalised; initials never glue onto a
    surname ("Wilmot, M." must not become "wilmotm")."""
    tokens: list[str] = []
    for author in authors:
        tokens.extend(regex.split(r"\s*(?:\band\b|&|,|;|\s+)\s*", author, flags=_RE_I))
    return [k for k in (author_key(t) for t in tokens) if len(k) >= 3]


def _surname_slip(a: str, b: str) -> bool:
    """One hand-typing slip: a single edit, or an adjacent-letter transposition
    ("Teh" -> "The"). Two plain edits stay a mismatch."""
    if within_edit_distance(a, b, 1):
        return True
    if len(a) != len(b):
        return False
    differing = [i for i, ch in enumerate(a) if ch != b[i]]
    return (
        len(differing) == 2
        and differing[1] == differing[0] + 1
        and a[differing[0]] == b[differing[1]]
        and a[differing[1]] == b[differing[0]]
    )


def near_matches(ref: ParsedRef, cite: InText) -> bool:
    if reference_matches_citation(ref, cite):
        return False
    same_year = (
        cite["year"] is not None
        and ref["year"] == cite["year"]
        and ref.get("yearSuffix", "") == cite.get("yearSuffix", "")
    )
    year_off_by_one = (
        cite["year"] is not None
        and ref["year"] is not None
        and abs(cite["year"] - ref["year"]) == 1
    )
    ref_author = author_key((ref["authors"][0] if ref["authors"] else "").split(",")[0])
    cite_author = author_key(cite["authors"][0] if cite["authors"] else "")

    # Spelled almost right, same year: "Smth (2020)" for Smith (2020).
    if same_year and len(ref_author) >= 3 and len(cite_author) >= 3:
        ref_surnames = [s for a in ref["authors"] for s in _cited_surnames([a])]
        if _surname_slip(ref_author, cite_author) or any(
            _surname_slip(s, cite_author) for s in ref_surnames
        ):
            return True
    # A cited surname appears in the entry's author list (second/third author).
    if same_year and any(
        s in _cited_surnames(cite["authors"]) for s in _cited_surnames(ref["authors"])
    ):
        return True
    # Surname exactly right, year one off — the classic year slip.
    if (
        year_off_by_one
        and len(ref_author) >= 3
        and len(cite_author) >= 3
        and any(s in _cited_surnames(cite["authors"]) for s in _cited_surnames(ref["authors"]))
    ):
        return True
    return False


def cross_reference_check(references: list[ParsedRef], in_text: list[InText]) -> dict:
    """Match citations against the bibliography; near matches surface as
    suggestions rather than orphans."""
    exact_for_cite: dict[int, int] = {}  # cite index -> ref index
    for ci, cite in enumerate(in_text):
        for ri, ref in enumerate(references):
            if reference_matches_citation(ref, cite):
                exact_for_cite[ci] = ri
                break
    matched_refs = {ri for ri in exact_for_cite.values()}

    pairs: list[dict] = []
    for ci, cite in enumerate(in_text):
        if ci in exact_for_cite:
            continue
        for ri, ref in enumerate(references):
            if ri not in matched_refs and near_matches(ref, cite):
                matched_refs.add(ri)
                pairs.append({"citeIndex": ci, "referenceIndex": ri})
                break

    result = {
        "unmatchedBibliography": [i for i, _ in enumerate(references) if i not in matched_refs],
        "unmatchedInText": [
            i
            for i in range(len(in_text))
            if i not in exact_for_cite and i not in {p["citeIndex"] for p in pairs}
        ],
    }
    if pairs:
        result["nearMatches"] = pairs
    return result
