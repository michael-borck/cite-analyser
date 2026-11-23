"""Cross-reference and duplicates tests — lifted from the TS engine's
cross-reference.test.ts and duplicates.test.ts cases."""

from cite_analyser.cross_reference import (
    author_key,
    cross_reference_check,
    near_matches,
    reference_matches_citation,
    within_edit_distance,
)
from cite_analyser.duplicates import find_duplicate_references, normalize_title


def ref(authors, year, suffix=None):
    r = {"raw": f"{authors} ({year}).", "authors": authors, "title": "T", "year": year}
    if suffix:
        r["yearSuffix"] = suffix
    return r


def cite(authors, year, suffix=None):
    c = {"raw": f"({authors}, {year})", "authors": authors, "year": year}
    if suffix:
        c["yearSuffix"] = suffix
    return c


def test_author_key_normalisation():
    # faithful to the TS engine: non-letters are stripped, initials kept —
    # the comma-split that removes initials happens in the callers
    assert author_key("Smith, J.") == "smithj"
    assert author_key("Smith et al.") == "smith"
    assert author_key("Buçinca, Z.") == "buçincaz"


def test_exact_match_including_year_suffix():
    assert reference_matches_citation(ref(["Vygotsky, L. S."], 1978), cite(["Vygotsky"], 1978))
    assert not reference_matches_citation(ref(["Vygotsky, L. S."], 1978), cite(["Vygotsky"], 1979))
    # 2026a vs plain 2026 are different works
    assert not reference_matches_citation(ref(["Borck, M."], 2026, "a"), cite(["Borck"], 2026))
    assert reference_matches_citation(ref(["Borck, M."], 2026, "a"), cite(["Borck"], 2026, "a"))


def test_within_edit_distance_bounds():
    assert within_edit_distance("smith", "smith", 1)
    assert within_edit_distance("smith", "smth", 1)
    assert not within_edit_distance("williams", "williamson", 1)
    assert within_edit_distance("williams", "williamson", 2)


def test_near_match_typo_same_year():
    assert near_matches(ref(["Smith, J."], 2020), cite(["Smth"], 2020))  # one deletion
    assert not near_matches(ref(["Smith, J."], 2020), cite(["Smti"], 2020))  # two edits
    assert not near_matches(ref(["Smith, J."], 2020), cite(["Jones"], 2020))


def test_near_match_year_off_by_one():
    assert near_matches(ref(["Wilmot, W."], 2019), cite(["Wilmot"], 2020))
    assert not near_matches(ref(["Wilmot, W."], 2019), cite(["Wilmot"], 2022))


def test_near_match_second_author():
    # citing a work by its second author (Aritzeta, Swailes & Senior)
    r = ref(["Aritzeta, A.", "Swailes, S.", "Senior, B."], 2007)
    assert near_matches(r, cite(["Swailes and Senior"], 2007))


def test_cross_reference_orphans_and_near_matches():
    references = [
        ref(["Smith, J."], 2020),
        ref(["Jones, K."], 2021),
        ref(["Lee, M."], 2022),
    ]
    citations = [
        cite(["Smith"], 2020),  # exact
        cite(["Jnoes"], 2021),  # typo -> near match
        cite(["Ghost"], 2030),  # orphan citation
    ]
    result = cross_reference_check(references, citations)
    assert result["unmatchedInText"] == [2]
    assert result["unmatchedBibliography"] == [2]  # Lee never cited
    assert len(result.get("nearMatches", [])) == 1


def test_duplicates_by_author_year_title():
    references = [
        ref(["Smith, J."], 2020),
        ref(["Smith, J."], 2021),
        ref(["Jones, K."], 2021),
        ref(["Smith, J."], 2020),  # duplicate of 0
        {
            "raw": "Smith, J. (2020).",
            "authors": ["Smith, J."],
            "title": "Different Work Entirely",
            "year": 2020,
        },
    ]
    dups = find_duplicate_references(references)
    assert dups == {3}


def test_normalize_title_folds():
    assert normalize_title("Café — Résumé!") == "cafe resume"
    assert normalize_title("A  B   C") == "a b c"
