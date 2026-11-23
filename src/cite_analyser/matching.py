"""Bibliographic matching and corroboration — full port of matching.ts.

Title similarity alone cannot tell a real reference from a fabricated one;
verdicts corroborate a matched work against the reference's authors and year.
"""

from __future__ import annotations

import unicodedata

import regex

from .duplicates import normalize_title  # re-exported below for one home

__all__ = [
    "author_corroboration",
    "bibliographic_conflicts",
    "character_similarity",
    "normalize_title",
    "repaired_title_query",
    "subtitle_variant",
    "surname_of",
    "title_token_conflict",
    "year_corroboration",
]

_RE_I = regex.IGNORECASE


def surname_of(name: str) -> str:
    """Accent-folded surname: before the comma, else the last word."""
    folded = unicodedata.normalize("NFKD", name)
    folded = regex.sub(r"\p{M}", "", folded)
    folded = regex.sub(r"[^\p{L},'\-\s]", "", folded).strip().lower()
    if "," in folded:
        return folded.split(",")[0].strip()
    return folded.split()[-1] if folded.split() else ""


Corroboration = str  # 'match' | 'mismatch' | 'unknown'


def _surnames(authors: list[str]) -> list[str]:
    kept = [a for a in authors if not regex.match(r"^et\s+al\.?$", a.strip(), _RE_I)]
    return [s for s in (surname_of(a) for a in kept) if s]


def author_corroboration(ref_authors: list[str], work_authors: list[str]) -> Corroboration:
    """Every cited surname must appear in the recorded set; given-name
    initials must not conflict. One coincidentally shared surname must not
    excuse other conflicting authors."""
    cited = _surnames(ref_authors)
    recorded = set(_surnames(work_authors))
    if not cited or not recorded:
        return "unknown"
    if not all(name in recorded for name in cited):
        return "mismatch"

    def initial_of(name: str) -> str:
        words = normalize_title(name).split(" ")
        if "," in name:
            given = normalize_title(",".join(name.split(",")[1:]))
        else:
            given = words[0] if len(words) > 1 else ""
        return given[0] if given else ""

    for author in [a for a in ref_authors if not regex.match(r"^et\s+al\.?$", a.strip(), _RE_I)]:
        known = initial_of(author)
        matches = [a for a in work_authors if surname_of(a) == surname_of(author)]
        if known and matches and all(initial_of(a) and initial_of(a) != known for a in matches):
            return "mismatch"
    return "match"


def subtitle_variant(a: str, b: str) -> bool:
    """One title is the other's main title (before the colon)."""

    def main(value: str) -> str:
        return normalize_title(regex.split(r"[:：]", value)[0])

    return (main(a) != normalize_title(a) and main(a) == normalize_title(b)) or (
        main(b) != normalize_title(b) and main(b) == normalize_title(a)
    )


def year_corroboration(a: int | None, b: int | None) -> Corroboration:
    if not a or not b:
        return "unknown"
    return "match" if abs(a - b) <= 1 else "mismatch"


def title_token_conflict(a: str, b: str) -> bool:
    """Critical token changes (not/no/retracted/numbers) must not disappear
    inside an otherwise high fuzzy score."""

    def critical(title: str) -> str:
        words = normalize_title(title).split(" ")
        kept = [
            w
            for w in words
            if regex.match(
                r"^(not|no|without|non|never|retraction|retracted|correction|erratum|corrigendum|review)$",
                w,
            )
            or regex.search(r"\d", w)
        ]
        return " ".join(sorted(kept))

    return critical(a) != critical(b)


def _doi_normal(value: str) -> str:
    return regex.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=_RE_I).strip().lower()


def bibliographic_conflicts(ref: dict, work: dict) -> list[str]:
    flags: list[str] = []
    if ref.get("doi") and work.get("doi") and _doi_normal(ref["doi"]) != _doi_normal(work["doi"]):
        flags.append("doi_mismatch")
    for field in ("volume", "issue", "pages"):
        if (
            ref.get(field)
            and work.get(field)
            and normalize_title(ref[field]) != normalize_title(work[field])
        ):
            flags.append(f"{field}_mismatch")
    # Journal abbreviations vary too much to treat inequality as a conflict.
    return flags


def character_similarity(a: str, b: str) -> float:
    """1 - normalised Levenshtein, used only for near-identical titles of
    similar length (ratio guard mirrors the TS engine)."""
    if not a or not b or max(len(a), len(b)) > 1000:
        return 0.0
    if min(len(a), len(b)) / max(len(a), len(b)) < 0.9:
        return 0.0
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        row = [i]
        for j in range(1, len(b) + 1):
            row.append(
                min(
                    row[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (0 if a[i - 1] == b[j - 1] else 1),
                )
            )
        previous = row
    return 1 - previous[len(b)] / max(len(a), len(b))


def repaired_title_query(title: str, max_words: int = 6) -> str:
    """Typo-tolerant retry query: keep long (information-dense) words so a
    misspelt distinctive term still leaves the rest for relevance ranking."""
    words = [w for w in normalize_title(title).split(" ") if w]
    if not words:
        return ""
    preferred = [w for w in words if len(w) >= 5]
    chosen = preferred if len(preferred) >= 3 else [w for w in words if len(w) >= 3]
    return " ".join(chosen[:max_words]).strip()
