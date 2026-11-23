"""Reference and in-text citation extraction.

Faithful port of cite-sight's ``references/extractor.ts`` (frozen at 0.13.x;
see that file for the extensive rationale behind each heuristic). Conformance
is enforced by tests/conformance, which replays goldens recorded from the TS
engine — when changing behaviour here, change it there and re-record.

Uses the ``regex`` module for \\p{...} classes and variable-width lookbehind,
which the TS engine relies on for accented surnames (Buçinca, Kovanović).
"""

from __future__ import annotations

import regex

_RE_I = regex.IGNORECASE

# ============================================================
# Regex constants
# ============================================================

DOI_RE = regex.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", _RE_I)
URL_RE = regex.compile(r"https?://[^\s)]+")
# Allows an APA same-author/same-year disambiguation letter (2026a).
YEAR_STRICT_RE = regex.compile(r"\b(19|20)\d{2}[a-z]?\b")

# Headings that indicate the start of a reference/bibliography section; the
# optional space after each first letter covers pdfjs's glyph-splitting.
REF_SECTION_RE = regex.compile(
    r"^(?:#{1,6}\s+)?(r\s?eferences|b\s?ibliography|w\s?orks\s+cited"
    r"|l\s?iterature\s+cited)\s*$",
    _RE_I,
)

# ============================================================
# Style detection
# ============================================================


def detect_style(raw: str) -> str:
    """Heuristically detect the citation style of one raw reference string."""
    # APA: year in parentheses early, after the author block.
    if regex.match(r"[A-Z][^(]+\(\d{4}[a-z]?\)\.", raw):
        return "apa"
    # Chicago before MLA (shared opening; distinguished by endings).
    if regex.search(r"\((?:19|20)\d{2}\):", raw):
        return "chicago"
    if regex.search(r":\s+[A-Z][^,]+,\s*(?:19|20)\d{2}\.?\s*$", raw):
        return "chicago"
    # MLA: quoted title, no year parenthesis after the author.
    if regex.match(r"[A-Z][^.]+\.\s+\"", raw):
        return "mla"
    return "unknown"


# ============================================================
# Individual reference parser
# ============================================================


def parse_reference(raw: str) -> dict:
    trimmed = raw.strip()

    doi_matches = DOI_RE.findall(trimmed)
    doi = regex.sub(r"[.)]+$", "", doi_matches[0]) if doi_matches else None

    url_matches = URL_RE.findall(trimmed)
    url = regex.sub(r"[.)]+$", "", url_matches[0]) if url_matches else None

    year_match = YEAR_STRICT_RE.search(trimmed)
    year = int(year_match.group(0)[:4]) if year_match else None

    style = detect_style(trimmed)
    authors = _extract_authors(trimmed)
    title = _extract_title(trimmed, style)
    journal, volume, issue, pages = _extract_journal_details(trimmed, style)

    ref = {
        "raw": trimmed,
        "authors": authors,
        "title": title,
        "year": year,
        "detectedStyle": style,
    }
    suffix = regex.search(r"[a-z]$", year_match.group(0)) if year_match else None
    if suffix:
        ref["yearSuffix"] = suffix.group(0)
    if journal is not None:
        ref["journal"] = journal
    if volume is not None:
        ref["volume"] = volume
    if issue is not None:
        ref["issue"] = issue
    if pages is not None:
        ref["pages"] = pages
    if doi is not None:
        ref["doi"] = doi
    if url is not None:
        ref["url"] = url
    return ref


# ============================================================
# Field-level extractors
# ============================================================


def _extract_authors(raw: str) -> list[str]:
    stripped = regex.sub(r"^[\d]+[.)]\s*", "", raw)
    stripped = regex.sub(r"^[•-]\s*", "", stripped)

    # Author block ends at the year "(YYYY)".
    year_idx_m = regex.search(r"\((19|20)\d{2}[a-z]?\)", stripped)
    if year_idx_m and year_idx_m.start() > 0:
        author_block = stripped[: year_idx_m.start()]
    else:
        # Author–date without the parenthesis (MISQ/ACIS, some Chicago):
        # anchor at the bare year so initials don't end the block early.
        bare_year = regex.match(r"^(.{2,300}?)[.,]?\s+(?:19|20)\d{2}[a-z]?[.,]", stripped)
        if bare_year:
            author_block = bare_year.group(1)
        else:
            # Fallback: first period followed by a capital or an opening
            # quote (straight or typographic).
            parts = regex.split(r"\.\s+[A-Z\"\u201c]", stripped)
            author_block = parts[0]

    if not author_block or len(author_block) > 300:
        return []

    parts = regex.split(
        r";\s*|,\s*(?:&|and)\s*|\s+(?:&|and)\s+"
        r"|,\s+(?=\p{Lu}[\p{L}\-']+\s*,\s*\p{Lu}\.)",
        author_block,
    )
    return [p for p in (s.strip() for s in parts) if p and regex.search(r"[A-Za-z]", p)]


def _title_up_to_sentence_break(s: str) -> str:
    """Substring up to the first sentence break outside parentheses — an
    editor/translator note embeds "X. Y" patterns a naive search would
    mistake for the title's end."""
    depth = 0
    for i in range(len(s) - 1):
        c = s[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        elif c == "." and depth == 0 and regex.match(r"\s+[A-Z*_\"]", s[i + 1 :]):
            return s[:i]
    return s


def _clean_title(t: str) -> str:
    t = regex.sub(
        r"\s*\([^)]*\b(?:Eds?|Trans|Edited|Translated|Comp|Compiled)\b\.?[^)]*\)\s*$",
        "",
        t,
        flags=_RE_I,
    )
    return regex.sub(r"[\s.,;:]+$", "", t).strip()


def _extract_title(raw: str, style: str) -> str:
    stripped = regex.sub(r"^[\d]+[.)]\s*", "", raw)

    if style == "apa":
        m = regex.match(r"\(\d{4}[a-z]?\)\.\s+([\s\S]+)$", stripped)
        if m:
            return _clean_title(_title_up_to_sentence_break(m.group(1)))

    if style != "apa":
        # Quoted title — MLA, Chicago, and quote-styles without a
        # distinctive signature; real documents use typographic quotes.
        m = regex.search(r"[\"\u201c]([^\"\u201d]{8,})[\"\u201d]", stripped)
        if m:
            return regex.sub(r"[\s,.;]+$", "", m.group(1)).strip()

    year_m = regex.search(r"\b(?:19|20)\d{2}\b", stripped)
    if year_m:
        after_year = regex.sub(r"^[).:\s]+", "", stripped[year_m.end() :])
        return _clean_title(_title_up_to_sentence_break(after_year))

    return ""


def _extract_journal_details(raw: str, style: str) -> tuple:
    volume = issue = pages = None

    vol_issue_m = regex.search(r"\b(\d+)\((\d+)\)", raw) or regex.search(
        r"vol\.\s*(\d+),\s*no\.\s*(\d+)", raw, _RE_I
    )
    if vol_issue_m:
        volume, issue = vol_issue_m.group(1), vol_issue_m.group(2)
    else:
        vol_only_m = regex.search(r"\b(\d+),\s*\d+[-\u2013]\d+", raw)
        if vol_only_m:
            volume = vol_only_m.group(1)

    pages_m = (
        regex.search(r"pp\.\s*([\d]+[-\u2013][\d]+)", raw, _RE_I)
        or regex.search(r",\s*([\d]+[-\u2013][\d]+)[.,\s]", raw)
        or regex.search(r":\s*([\d]+[-\u2013][\d]+)", raw)
    )
    if pages_m:
        pages = pages_m.group(1)

    journal = None
    italic_m = regex.search(r"\*([^*]+)\*", raw)
    if italic_m:
        journal = italic_m.group(1).strip()
    elif style == "apa":
        # Structure after "(Year). ": "<title>. <journal>, <vol>(<issue>)..."
        # Anchor at the END of the title, not the year, so the journal field
        # doesn't swallow the sentence-case title.
        year_end = raw.rfind("). ")
        if year_end >= 0:
            body = raw[year_end + 3 :]
            title_sep_m = regex.search(r"\.\s+(?=[A-Z*_])", body)
            after_title = (body[title_sep_m.start() + 1 :] if title_sep_m else body).lstrip()
            journal_end_m = regex.search(r",\s*\d", after_title)
            journal = (
                after_title[: journal_end_m.start()].strip()
                if journal_end_m and journal_end_m.start() > 0
                else regex.split(r"\.\s", after_title)[0].strip()
            )
    return journal, volume, issue, pages


# ============================================================
# Reference section splitter
# ============================================================


def _find_reference_section(text: str) -> str | None:
    lines = text.split("\n")
    start_idx = -1

    for i, line in enumerate(lines):
        if REF_SECTION_RE.match(line.strip()):
            start_idx = i + 1
            break

    if start_idx < 0:
        # PDF fallback: the heading may be inline within a long text block
        # ("References Borck, M. (2026)..."); the lookahead requires the next
        # token to look like an author surname so "References by Document"
        # doesn't trip it.
        inline_re = regex.compile(
            r"(?:^|\s)(R\s?eferences|B\s?ibliography|W\s?orks\s+Cited"
            r"|L\s?iterature\s+Cited)\s+([A-Z])(?=[a-zA-Z\-']+)",
            _RE_I,
        )
        for i, line in enumerate(lines):
            m = inline_re.search(line)
            if m:
                offset = m.start() + len(m.group(0)) - 1
                return line[offset:] + "\n" + "\n".join(lines[i + 1 :])

        # No heading anywhere, but the document may still be a reference
        # list (bulleted/numbered APA-like entries). Three+ matches = signal.
        ref_bullet_re = regex.compile(
            r"^\s*(?:[-*•]|\d+[.)])\s+[A-Z].*\((?:(?:19|20)\d{2}|[Nn]\.\s*[Dd]\.)"
        )
        bullet_lines = [ln for ln in lines if ref_bullet_re.search(ln)]
        if len(bullet_lines) >= 3:
            return "\n".join(bullet_lines)

            # APA hanging-indent layout: one blank-line-separated paragraph per
        # entry, each starting with "Surname, I." and carrying a (Year).
        ref_para_start_re = regex.compile(r"^\p{Lu}[\p{L}\-']+,\s+(?:\p{Lu}\.|\p{Lu}[\p{L}\-']+)")
        ref_para_year_re = regex.compile(r"\((?:19|20)\d{2}|\([Nn]\.\s*[Dd]\.\)")
        blocks = [b.strip() for b in regex.split(r"\n\s*\n", text)]
        ref_blocks = [
            b for b in blocks if ref_para_start_re.match(b) and ref_para_year_re.search(b)
        ]
        if len(ref_blocks) >= 3:
            return "\n\n".join(ref_blocks)

        return None

    # Stop at the next heading or horizontal rule so appendices stay out.
    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        trimmed = lines[i].strip()
        if regex.match(r"^#{1,6}\s+\S", trimmed) or regex.match(r"^[-*_]{3,}\s*$", trimmed):
            end_idx = i
            break

    return "\n".join(lines[start_idx:end_idx])


def _split_by_lines(block: str) -> list[str]:
    lines = block.split("\n")
    refs: list[str] = []
    current = ""

    def is_new_ref_start(line: str) -> bool:
        if regex.match(r"^\s*\d+[.)]\s+", line):
            return True
        if regex.match(r"^\s*[•-]\s+", line):
            return True
        return line.strip() == "" and current.strip() != ""

    for line in lines:
        if is_new_ref_start(line):
            if current.strip():
                refs.append(current.strip())
            current = "" if line.strip() == "" else line
        else:
            if line.strip() == "":
                if current.strip():
                    refs.append(current.strip())
                    current = ""
            else:
                current = f"{current} {line.strip()}" if current else line.strip()

    if current.strip():
        refs.append(current.strip())
    return [r for r in refs if len(r) > 10]


def _split_continuous_text(block: str) -> list[str]:
    # Boundaries, not full author lists: a new APA entry starts where a
    # surname (tolerating a kerning-split hyphen) is followed by initials
    # and, within a few hundred chars, a "(Year)" — guarded by a lookbehind
    # for the previous entry's ending. See extractor.ts for the full story.
    ref_start_re = regex.compile(
        r"(?<=\.\s+|(?:https?://\S+)\s+)"
        r"(?=(?:[A-Z][a-zA-Z\-']+(?:\s*-\s*[A-Z][a-zA-Z\-']+)?,\s*[A-Z]\."
        r"[\s\S]{0,300}?\(\s*(?:19|20)\d{2}"
        r"|[A-Z][a-zA-Z\-']+(?:\s+[A-Za-z&][a-zA-Z\-'.]*){0,6}\.\s*\(\s*(?:19|20)\d{2}))"
    )
    text = block.strip()
    indices = [0] + [m.start() for m in ref_start_re.finditer(text) if m.start() > 0]

    if len(indices) <= 1:
        return [text] if len(text) > 10 else []

    refs = []
    for i, start in enumerate(indices):
        end = indices[i + 1] if i + 1 < len(indices) else len(text)
        ref = text[start:end].strip()
        if len(ref) > 10:
            refs.append(ref)
    return refs


def _split_into_references(block: str) -> list[str]:
    line_refs = _split_by_lines(block)
    if len(line_refs) > 1:
        return line_refs
    return _split_continuous_text(block)


# ============================================================
# In-text citation extraction
# ============================================================

_MLA_NON_CITATION_WORDS = {
    "table",
    "figure",
    "fig",
    "study",
    "section",
    "appendix",
    "chapter",
    "equation",
    "eq",
    "part",
    "step",
    "phase",
    "model",
    "panel",
    "item",
    "note",
    "row",
    "column",
    "col",
    "vol",
    "no",
    "box",
    "experiment",
    "condition",
    "question",
    "footnote",
    "line",
    "para",
    "paragraph",
    "version",
    "level",
    "round",
    "wave",
    "cohort",
    "day",
    "week",
    "page",
    "p",
    "pp",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
}


def _split_citation_authors(raw: str) -> list[str]:
    parts = regex.split(r"\s*(?:,\s*(?:(?:&|and)\s+)?|&\s*|\s+and\s+)\s*", raw, flags=_RE_I)
    return [a.strip() for a in parts if a.strip()]


_APA_PAREN_RE = regex.compile(
    r"\((\p{Lu}[\p{L}\-']+(?:\s*-\s*\p{Lu}[\p{L}\-']+)?"
    r"(?:(?:\s+\p{Lu}[\p{L}\-']+)"
    r"|(?:\s*,\s*(?:(?:&|and)\s+)?\p{Lu}[\p{L}\-']+(?:\s*-\s*\p{Lu}[\p{L}\-']+)?)"
    r"|(?:\s*(?:&|and)\s*\p{Lu}[\p{L}\-']+(?:\s*-\s*\p{Lu}[\p{L}\-']+)?)"
    r"|(?:\s+et\s+al\.))*),\s*"
    r"((?:19|20)\d{2}[a-z]?(?:,\s*(?:19|20)\d{2}[a-z]?)*"
    r"(?:;\s*\p{Lu}[^;)]+,\s*(?:19|20)\d{2}[a-z]?)*)\)"
)

_APA_NARRATIVE_RE = regex.compile(
    r"\b(\p{Lu}[\p{L}\-']+(?:\s*-\s*\p{Lu}[\p{L}\-']+)?"
    r"(?:(?:\s+\p{Lu}[\p{L}\-']+)"
    r"|(?:\s*,\s*(?:(?:&|and)\s+)?\p{Lu}[\p{L}\-']+(?:\s*-\s*\p{Lu}[\p{L}\-']+)?)"
    r"|(?:\s+(?:&|and)\s+\p{Lu}[\p{L}\-']+)"
    r"|(?:\s+et\s+al\.))*)\s+\(((?:19|20)\d{2})([a-z]?)\)"
)

_MLA_RE = regex.compile(r"\(([A-Z][a-zA-Z\-']+)\s+(\d+(?:-\d+)?)\)")
_CHICAGO_RE = regex.compile(r"\[(\d+)\]|([¹²³⁴⁵⁶⁷⁸⁹⁰]+)")


def _extract_in_text_citations(text: str, style: str = "unknown") -> list[dict]:
    results: list[dict] = []
    run_mla = style in ("mla", "unknown")
    run_chicago = style in ("chicago", "unknown")

    for m in _APA_PAREN_RE.finditer(text):
        for group in m.group(0)[1:-1].split(";"):
            split = regex.match(
                r"^\s*(.+?),\s*((?:19|20)\d{2}[a-z]?(?:,\s*(?:19|20)\d{2}[a-z]?)*)\s*$",
                group,
            )
            if not split:
                continue
            authors = _split_citation_authors(split.group(1))
            for year_token in regex.split(r",\s*", split.group(2)):
                citation = {
                    "raw": m.group(0),
                    "authors": authors,
                    "year": int(year_token[:4]),
                }
                suffix = regex.search(r"[a-z]$", year_token)
                if suffix:
                    citation["yearSuffix"] = suffix.group(0)
                citation["position"] = m.start()
                results.append(citation)

    for m in _APA_NARRATIVE_RE.finditer(text):
        citation = {
            "raw": m.group(0),
            "authors": _split_citation_authors(m.group(1)),
            "year": int(m.group(2)),
        }
        if m.group(3):
            citation["yearSuffix"] = m.group(3)
        citation["position"] = m.start()
        results.append(citation)

    if run_mla:
        for m in _MLA_RE.finditer(text):
            word = m.group(1).lower()
            if word in _MLA_NON_CITATION_WORDS:
                continue
            if regex.match(r"^(?:19|20)\d{2}$", m.group(2)):
                continue
            results.append(
                {
                    "raw": m.group(0),
                    "authors": [m.group(1)],
                    "year": None,
                    "pageNumbers": m.group(2),
                    "position": m.start(),
                }
            )

    if run_chicago:
        for m in _CHICAGO_RE.finditer(text):
            results.append({"raw": m.group(0), "authors": [], "year": None, "position": m.start()})

    # Deduplicate by position (narrative can double-match parenthetical).
    seen: set[tuple] = set()
    deduped = []
    for r in results:
        key = (
            r["position"],
            tuple(r["authors"]),
            r["year"],
            r.get("yearSuffix"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


# ============================================================
# Public API
# ============================================================


def _looks_like_reference(ref: dict) -> bool:
    """Reject fragments the section splitter swept in that are not actually
    references (footers, draft notes): a genuine reference carries at least
    one hard bibliographic anchor."""
    if ref["year"] or ref.get("doi"):
        return True
    if ref.get("url") and regex.match(r"https?://", ref["url"], _RE_I):
        return True
    if regex.search(r"\(\s*[Nn]\.\s*[Dd]\.\s*\)", ref["raw"]):
        return True
    return bool(regex.search(r"\b(?:1[89]|20)\d{2}\b", ref["raw"]))


def majority_style(refs: list[dict]) -> str:
    counts = {"apa": 0, "mla": 0, "chicago": 0, "unknown": 0}
    for r in refs:
        counts[r["detectedStyle"]] += 1
    best, best_n = "unknown", 0
    for s in ("apa", "mla", "chicago"):
        if counts[s] > best_n:
            best, best_n = s, counts[s]
    return best


def extract_references(text: str) -> dict:
    """Extract the bibliography and in-text citations from document text."""
    section = _find_reference_section(text)
    raw_refs = _split_into_references(section) if section else []
    references = [r for r in map(parse_reference, raw_refs) if _looks_like_reference(r)]

    # The bibliography's own style decides which in-text shapes are
    # plausible; entries themselves are not in-text citations. Keep offsets
    # stable by blanking the section instead of removing it.
    section_start = text.find(section) if section else -1
    if section and section_start >= 0:
        blanked = regex.sub(r"[^\n]", " ", section)
        body = text[:section_start] + blanked + text[section_start + len(section) :]
    else:
        body = text
    in_text = _extract_in_text_citations(body, majority_style(references))
    return {"references": references, "inTextCitations": in_text}
