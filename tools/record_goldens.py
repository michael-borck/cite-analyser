#!/usr/bin/env python3
"""Record conformance goldens from the FROZEN TypeScript cite-sight engine.

Runs the TS CLI (offline mode — deterministic, no network) over every
demo-corpus document and stores the citation-relevant subset as golden JSON
in tests/conformance/goldens/. cite-analyser's test suite must reproduce
these byte-for-byte (field-by-field). Re-record only when the TS engine
changes (it is frozen, so ideally never).

Usage: python3 tools/record_goldens.py /path/to/cite-sight
"""

import json
import pathlib
import subprocess
import sys

CORPUS_DIRS = ("demo-corpus",)
# Deterministic, extraction-relevant subset (timing and network verifications
# stripped — offline verifications carry no lookup payload).
KEEP = {
    "references": [
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
    ],
    "inTextCitations": ["raw", "authors", "year", "yearSuffix", "pageNumbers", "position"],
}


def slim(result: dict) -> dict:
    refs_block = result.get("references", {})
    return {
        "references": [
            {k: r[k] for k in KEEP["references"] if r.get(k) is not None}
            for r in refs_block.get("references", [])
        ],
        "inTextCitations": [
            {k: c[k] for k in KEEP["inTextCitations"] if c.get(k) is not None}
            for c in refs_block.get("inTextCitations", [])
        ],
    }


def main(repo: pathlib.Path) -> None:
    cli = repo / "packages" / "cli" / "dist" / "index.js"
    if not cli.exists():
        sys.exit(f"TS CLI not built: {cli}")
    out_dir = pathlib.Path(__file__).resolve().parents[1] / "tests" / "conformance" / "goldens"
    out_dir.mkdir(parents=True, exist_ok=True)

    for corpus in CORPUS_DIRS:
        for doc in sorted((repo / corpus).glob("*.md")):
            if doc.name.startswith("._"):
                continue  # macOS AppleDouble sidecars on external volumes
            proc = subprocess.run(
                ["node", str(cli), "check", str(doc), "--offline", "--json"],
                capture_output=True,
                text=True,
                check=True,
                cwd=repo,
            )
            golden = slim(json.loads(proc.stdout))
            dest = out_dir / f"{doc.stem}.json"
            dest.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n")
            print(f"{doc.name}: {len(golden['references'])} refs -> {dest.name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]))
