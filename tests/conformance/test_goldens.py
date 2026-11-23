"""Conformance against the frozen TS engine: replay demo-corpus goldens.

The goldens are recorded by tools/record_goldens.py from cite-sight's
TypeScript engine (offline mode). The Python port must reproduce them
field-by-field — this is the drift-killer that lets cite-analyser replace
the TS engine without behavioural surprises.
"""

import json
import pathlib

import pytest

from cite_analyser.extractor import extract_references

CITE_SIGHT = pathlib.Path(__file__).resolve().parents[4] / "cite-sight"
GOLDENS = pathlib.Path(__file__).parent / "goldens"

_corpus = sorted(p for p in GOLDENS.glob("*.json") if not p.name.startswith("._"))
if not _corpus:
    pytest.skip("no goldens recorded — run tools/record_goldens.py", allow_module=True)


def _slim(result: dict) -> dict:
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
    keep_cites = ("raw", "authors", "year", "yearSuffix", "pageNumbers", "position")
    return {
        "references": [
            {k: r[k] for k in keep_refs if r.get(k) is not None} for r in result["references"]
        ],
        "inTextCitations": [
            {k: c[k] for k in keep_cites if c.get(k) is not None} for c in result["inTextCitations"]
        ],
    }


@pytest.mark.parametrize("golden_path", _corpus, ids=lambda p: p.stem)
def test_matches_ts_engine(golden_path: pathlib.Path):
    doc = CITE_SIGHT / "demo-corpus" / f"{golden_path.stem}.md"
    if not doc.exists():
        pytest.skip(f"corpus document not found: {doc}")
    golden = json.loads(golden_path.read_text())
    actual = _slim(extract_references(doc.read_text()))

    assert actual["references"] == golden["references"], _first_diff(
        actual["references"], golden["references"], "references"
    )
    assert actual["inTextCitations"] == golden["inTextCitations"], _first_diff(
        actual["inTextCitations"], golden["inTextCitations"], "inTextCitations"
    )


def _first_diff(actual: list, golden: list, label: str) -> str:
    if len(actual) != len(golden):
        return f"{label}: count {len(actual)} != golden {len(golden)}"
    for i, (a, g) in enumerate(zip(actual, golden)):
        if a != g:
            differing = [k for k in g if a.get(k) != g.get(k)]
            return f"{label}[{i}] differs on {differing}:\n  actual: {a}\n  golden: {g}"
    return f"{label}: unknown diff"
