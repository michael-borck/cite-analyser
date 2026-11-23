"""Conformance against the frozen TS engine: replay demo-corpus goldens.

Goldens are recorded by tools/record_goldens.py from cite-sight's TypeScript
engine (offline mode). The Python port must reproduce them field-by-field —
the drift-killer that lets cite-analyser replace the TS engine without
behavioural surprises.

One deliberate exception: for PDF/DOCX documents the in-text citation
``position`` offsets are compared as a *set without positions*. The two
engines use different text extractors (pdf.js vs pdfplumber, mammoth vs
python-docx); their extracted text differs by a space here and there, which
shifts absolute offsets without changing what was cited. What must match is
the citation set; exact offsets remain pinned for text formats.
"""

import json
import pathlib

import pytest

from cite_analyser.analysis import analyze
from cite_analyser.extractors import extract_document

CITE_SIGHT = pathlib.Path(__file__).resolve().parents[4] / "cite-sight"
GOLDENS = pathlib.Path(__file__).parent / "goldens"

_corpus = sorted(p for p in GOLDENS.glob("*.json") if not p.name.startswith("._"))
if not _corpus:
    pytest.skip("no goldens recorded — run tools/record_goldens.py", allow_module=True)

# Formats whose extracted text is byte-identical across engines, so exact
# in-text positions are pinned too.
POSITION_EXACT_SUFFIXES = {".md", ".txt", ".qmd", ".json"}


def _slim(result: dict, keep_position: bool) -> dict:
    keep_refs = (
        "raw",
        "authors",
        "title",
        "year",
        "yearSuffix",
        "journal",
        "volume",
        "issue",
        "pages",
        "doi",
        "url",
        "detectedStyle",
    )
    keep_cites = ["raw", "authors", "year", "yearSuffix", "pageNumbers"]
    if keep_position:
        keep_cites.append("position")
    refs = result["references"]
    cites = result["inTextCitations"]
    cross = result.get("crossReference") or {}
    slim_cross = {
        "unmatchedBibliography": [refs[i]["raw"] for i in cross.get("unmatchedBibliography", [])],
        "unmatchedInText": [cites[i]["raw"] for i in cross.get("unmatchedInText", [])],
        "nearMatches": [
            {
                "cite": cites[p["citeIndex"]]["raw"],
                "reference": refs[p["referenceIndex"]]["raw"],
            }
            for p in cross.get("nearMatches", [])
        ],
    }
    return {
        "references": [
            {k: r[k] for k in keep_refs if r.get(k) is not None} for r in result["references"]
        ],
        "inTextCitations": [
            {k: c[k] for k in keep_cites if c.get(k) is not None} for c in result["inTextCitations"]
        ],
        "crossReference": slim_cross,
    }


@pytest.mark.parametrize("golden_path", _corpus, ids=lambda p: p.name)
def test_matches_ts_engine(golden_path: pathlib.Path):
    doc = CITE_SIGHT / "demo-corpus" / golden_path.name.removesuffix(".json")
    if not doc.exists():
        pytest.skip(f"corpus document not found: {doc}")
    golden = json.loads(golden_path.read_text())
    text = extract_document(doc.read_bytes(), doc.name)
    keep_positions = doc.suffix.lower() in POSITION_EXACT_SUFFIXES
    actual = _slim(analyze(text), keep_positions)

    assert actual["references"] == golden["references"], _first_diff(
        actual["references"], golden["references"], "references"
    )
    assert actual["crossReference"] == golden["crossReference"], (
        f"crossReference differs:\n  actual: {actual['crossReference']}\n"
        f"  golden: {golden['crossReference']}"
    )
    # positions were stripped for non-exact formats; compare citations as sets
    actual_cites = sorted(json.dumps(c, sort_keys=True) for c in actual["inTextCitations"])
    golden_cites = sorted(
        json.dumps(
            {k: v for k, v in c.items() if k != "position"}
            if doc.suffix.lower() not in POSITION_EXACT_SUFFIXES
            else c,
            sort_keys=True,
        )
        for c in golden["inTextCitations"]
    )
    assert actual_cites == golden_cites, "in-text citation sets differ"


def _first_diff(actual: list, golden: list, label: str) -> str:
    if len(actual) != len(golden):
        return f"{label}: count {len(actual)} != golden {len(golden)}"
    for i, (a, g) in enumerate(zip(actual, golden)):
        if a != g:
            differing = [k for k in g if a.get(k) != g.get(k)]
            return f"{label}[{i}] differs on {differing}:\n  actual: {a}\n  golden: {g}"
    return f"{label}: unknown diff"
