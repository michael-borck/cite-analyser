# cite-analyser

Part of the [lens family](https://github.com/michael-borck/lens-analysers).

**Citation extraction, reference verification, and in-text cross-checking.**
The Python successor to the [cite-sight](https://github.com/michael-borck/cite-sight)
TypeScript engine (frozen at 0.13.x — see cite-sight's MIGRATION.md). Ported
module-by-module in pipeline order, conformance-tested field-by-field against
goldens recorded from the TS engine over its own `demo-corpus/`.

## Port status

| cite-sight module | Status |
|---|---|
| `references/extractor.ts` (section finding, splitting, parsers, in-text) | ✅ ported, conformance-gated |
| `references/crossReference.ts`, `duplicates.ts` | ⏳ next |
| `references/verifier` (Crossref, DataCite, OpenAlex, EuropePMC, arXiv, URL liveness) | ⏳ |
| `formatValidator.ts` | ⏳ |
| `claims/*` (local claim-evidence review) | ⏳ last, optional |

## Install / use

```bash
pip install cite-analyser

cite-analyser analyse essay.md --json     # extract references + in-text citations
cite-analyser serve --port 8006           # the family HTTP contract (POST /analyse)
```

Python:

```python
from cite_analyser.extractor import extract_references

result = extract_references(text)  # {"references": [...], "inTextCitations": [...]}
```

## Development

```bash
uv venv && uv pip install -e ".[dev]"
python -m pytest                # unit + conformance (vs recorded goldens)
python tools/record_goldens.py /path/to/cite-sight   # only if the TS engine changes
```
