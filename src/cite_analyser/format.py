"""Reference format validation — port of formatValidator.ts (APA/MLA/Chicago).

The `effectiveStyle == 'unknown'` fallback to the reference's own detected
style is preserved: an auto-style run uses each entry's parse-time verdict.
"""

from __future__ import annotations

import regex

FormatIssue = dict


def _is_sentence_case(s: str) -> bool:
    """First word capitalised, fewer than half the remaining words capitalised
    (allows proper nouns)."""
    if not s:
        return True
    words = s.strip().split()
    if not words:
        return True
    if not regex.match(r"^[A-Z]", words[0]):
        return False
    non_first = words[1:]
    if not non_first:
        return True
    uppercase = sum(1 for w in non_first if regex.match(r"^[A-Z]", w))
    return uppercase / len(non_first) <= 0.3


def _is_title_case(s: str) -> bool:
    if not s:
        return False
    significant = [w for w in s.strip().split() if len(w) > 3]
    if not significant:
        return True
    return all(regex.match(r"^[A-Z]", w) for w in significant)


def _has_parenthesis_year(raw: str, year: int | None) -> bool:
    if not year:
        return False
    return regex.search(rf"\({year}\)", raw) is not None


def _validate_apa(ref: dict) -> list[FormatIssue]:
    issues: list[FormatIssue] = []

    # 1. Year must be in parentheses after the author block.
    if ref.get("year") and not _has_parenthesis_year(ref["raw"], ref["year"]):
        issues.append(
            {
                "field": "year",
                "message": "Year should be enclosed in parentheses after the author list",
                "expected": f"({ref['year']})",
                "actual": str(ref["year"]),
            }
        )

    # 2. Title should be in sentence case.
    if ref.get("title") and not _is_sentence_case(ref["title"]):
        issues.append(
            {
                "field": "title",
                "message": (
                    "APA titles should be in sentence case (only first word and "
                    "proper nouns capitalised)"
                ),
                "actual": ref["title"],
            }
        )

    # Journal rules apply only to actual journal articles — a volume or issue
    # number is the reliable periodical marker (books have neither, though the
    # parser often captures their publisher into `journal`).
    looks_like_journal = bool(ref.get("volume") or ref.get("issue"))

    # 3. Journal title should be in Title Case.
    if ref.get("journal") and looks_like_journal and not _is_title_case(ref["journal"]):
        issues.append(
            {
                "field": "journal",
                "message": "APA journal names should be in Title Case",
                "actual": ref["journal"],
            }
        )

    # 4. DOI should be present for journal articles.
    if ref.get("journal") and looks_like_journal and not ref.get("doi") and not ref.get("url"):
        issues.append(
            {"field": "doi", "message": "APA journal articles should include a DOI or URL"}
        )

    # 5. Authors: each should be "Last, F.".
    for author in ref.get("authors") or []:
        if not regex.search(r", [A-Z]\.", author):
            issues.append(
                {
                    "field": "authors",
                    "message": f'APA author format should be "Last, F." — got: "{author}"',
                    "actual": author,
                }
            )

    return issues


def _validate_mla(ref: dict) -> list[FormatIssue]:
    issues: list[FormatIssue] = []
    raw = ref["raw"]

    # 1. Year should not be parenthesised directly after the author.
    if ref.get("year") and _has_parenthesis_year(raw, ref["year"]):
        early = raw.find(f"({ref['year']})")
        if 0 <= early < 60:
            issues.append(
                {
                    "field": "year",
                    "message": (
                        "MLA format places the year near the end of the citation, "
                        "not in parentheses after the author"
                    ),
                    "actual": raw[early : early + 6],
                }
            )

    # 2. Article titles should be quoted.
    if ref.get("title") and '"' not in raw and "\u201c" not in raw:
        issues.append(
            {
                "field": "title",
                "message": "MLA article titles should be enclosed in quotation marks",
                "actual": ref["title"],
            }
        )

    # 3. Volume/issue labels.
    if ref.get("volume") and not regex.search(r"vol\.\s*\d", raw, regex.IGNORECASE):
        issues.append(
            {
                "field": "volume",
                "message": 'MLA format requires "vol." before the volume number',
                "expected": f"vol. {ref['volume']}",
                "actual": ref["volume"],
            }
        )
    if ref.get("issue") and not regex.search(r"no\.\s*\d", raw, regex.IGNORECASE):
        issues.append(
            {
                "field": "issue",
                "message": 'MLA format requires "no." before the issue number',
                "expected": f"no. {ref['issue']}",
                "actual": ref["issue"],
            }
        )

    # 4. Pages should use "pp.".
    if ref.get("pages") and not regex.search(r"pp\.\s*\d", raw, regex.IGNORECASE):
        issues.append(
            {
                "field": "pages",
                "message": 'MLA format requires "pp." before page numbers',
                "expected": f"pp. {ref['pages']}",
                "actual": ref["pages"],
            }
        )

    # 5. First author inverted.
    authors = ref.get("authors") or []
    if authors and "," not in authors[0]:
        issues.append(
            {
                "field": "authors",
                "message": 'MLA first author should be in inverted order: "Last, First"',
                "actual": authors[0],
            }
        )

    return issues


def _validate_chicago(ref: dict) -> list[FormatIssue]:
    issues: list[FormatIssue] = []
    raw = ref["raw"]

    # 1. Year in parentheses after volume/issue: "no. X (Year): pages".
    if ref.get("year") and ref.get("journal"):
        if not regex.search(r",\s*no\.\s*\d+\s*\(\d{4}\):", raw, regex.IGNORECASE):
            issues.append(
                {
                    "field": "year",
                    "message": (
                        "Chicago journal citations should place the year in parentheses "
                        'after the issue number: "no. X (Year): pages"'
                    ),
                    "expected": (
                        f"no. {ref.get('issue') or 'X'} ({ref['year']}): "
                        f"{ref.get('pages') or 'pages'}"
                    ),
                }
            )

    # 2. Article titles quoted.
    if ref.get("title") and '"' not in raw and "\u201c" not in raw:
        issues.append(
            {
                "field": "title",
                "message": "Chicago article titles should be enclosed in quotation marks",
                "actual": ref["title"],
            }
        )

    # 3. Journal italicised.
    if ref.get("journal") and "*" not in raw and "_" not in raw:
        issues.append(
            {
                "field": "journal",
                "message": (
                    "Chicago journal names should be italicised "
                    "(represented as *Journal* in plain text)"
                ),
                "actual": ref["journal"],
            }
        )

    # 4. First author inverted.
    authors = ref.get("authors") or []
    if authors and "," not in authors[0]:
        issues.append(
            {
                "field": "authors",
                "message": 'Chicago first author should be in inverted order: "Last, First"',
                "actual": authors[0],
            }
        )

    # 5. Pages follow "(Year): ".
    if ref.get("pages") and not regex.search(
        rf"\({ref.get('year')}\):\s*{regex.escape(ref['pages'])}", raw
    ):
        issues.append(
            {
                "field": "pages",
                "message": (
                    "Chicago page numbers should directly follow the year in "
                    'parentheses with a colon: "(Year): pages"'
                ),
                "actual": ref["pages"],
            }
        )

    return issues


def validate_format(ref: dict, style: str) -> list[FormatIssue]:
    """Validate one reference against the style's structural rules."""
    if style == "apa":
        return _validate_apa(ref)
    if style == "mla":
        return _validate_mla(ref)
    if style == "chicago":
        return _validate_chicago(ref)
    # Unknown style — fall back to the reference's own detected style.
    detected = ref.get("detectedStyle") or "unknown"
    if detected != "unknown":
        return validate_format(ref, detected)
    return []
