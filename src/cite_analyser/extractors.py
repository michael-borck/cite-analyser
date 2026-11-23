"""Document text extraction — port of cite-sight's ``extractors/``.

Resource limits and behaviour mirror the TS engine: inputs are adversarial
(zip-bomb DOCX, vast-page PDFs), so sizes are capped before parsing and text
is clamped after. PDF pages are reduced to one continuous line each — the
pdf.js item-join shape the reference extractor's continuous-text splitter
was built for.
"""

from __future__ import annotations

import io

import regex

# Max raw bytes handed to a parser (compressed, for DOCX).
MAX_INPUT_BYTES = 50 * 1024 * 1024
# Max characters of extracted text retained for analysis (~2000-page book).
MAX_TEXT_CHARS = 8 * 1024 * 1024
# Max PDF pages walked.
MAX_PDF_PAGES = 3000

TEXT_EXTENSIONS = {".txt", ".md", ".qmd", ".json"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}


def assert_input_size(length: int, file_name: str) -> None:
    if length > MAX_INPUT_BYTES:
        raise ValueError(
            f'File "{file_name}" is too large to process '
            f"({round(length / 1024 / 1024)} MB > {MAX_INPUT_BYTES // 1024 // 1024} MB limit)."
        )


def clamp_text(text: str) -> str:
    return text[:MAX_TEXT_CHARS] if len(text) > MAX_TEXT_CHARS else text


def repair_split_dois(text: str) -> str:
    """pdf.js splits long DOIs across text items at arbitrary glyph runs,
    leaving spaces inside the address ("doi.org/1 0.1186/... - 023"). Rejoin
    the fragments: after doi.org/, swallow tokens while they look like DOI
    fragments (carry a digit, or a bare hyphen)."""

    def _rejoin(m: regex.Match) -> str:
        prefix, head, tail = m.group(1), m.group(2), m.group(3)
        frags = tail.strip().split()
        kept: list[str] = []
        for frag in frags:
            if frag == "-" or regex.search(r"\d", frag):
                kept.append(frag)
            else:
                break
        rest = " ".join(frags[len(kept) :])
        return prefix + head + "".join(kept) + (f" {rest}" if rest else "")

    return regex.sub(r"(https?://doi\.org/)([\w.\-/]+)((?:\s+[\w.\-/]+)+)", _rejoin, text)


def _extract_plain(raw: bytes, file_name: str) -> str:
    assert_input_size(len(raw), file_name)
    return clamp_text(raw.decode("utf-8", errors="replace").lstrip("﻿"))


def _extract_pdf(raw: bytes, file_name: str) -> str:
    import pdfplumber

    assert_input_size(len(raw), file_name)
    page_texts: list[str] = []
    char_count = 0
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            # pdf.js joins a page's text items with spaces: the page becomes
            # one continuous line. Reproduce that shape — the extractor's
            # continuous-text splitter depends on it.
            page_text = (page.extract_text() or "").replace("\n", " ")
            page_text = regex.sub(r" {2,}", " ", page_text).strip()
            page_texts.append(page_text)
            char_count += len(page_text) + 1
            if char_count > MAX_TEXT_CHARS:
                break
    return clamp_text(repair_split_dois("\n".join(page_texts)))


def _extract_docx(raw: bytes, file_name: str) -> str:
    import docx

    assert_input_size(len(raw), file_name)
    document = docx.Document(io.BytesIO(raw))
    # mammoth's extractRawText yields the document's prose with blank lines
    # between block elements; python-docx gives us paragraphs, so join the
    # same way.
    return clamp_text("\n\n".join(p.text for p in document.paragraphs))


def extract_document(raw: bytes, file_name: str) -> str:
    """Dispatch on the file name's extension; raises for unknown types."""
    name = file_name.lower()
    for extensions, extractor in (
        (TEXT_EXTENSIONS, _extract_plain),
        (PDF_EXTENSIONS, _extract_pdf),
        (DOCX_EXTENSIONS, _extract_docx),
    ):
        if any(name.endswith(sfx) for sfx in extensions):
            return extractor(raw, file_name)
    raise ValueError(
        f"Unsupported file type '{file_name}' — expected text (.txt/.md/.qmd/.json), PDF, or DOCX."
    )
