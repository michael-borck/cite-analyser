"""CLI: `cite-analyser analyse <file>` plus the family's serve/manifest."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .analysis import analyze
from .extractors import extract_document
from .manifest import MANIFEST


def _read_text(path: pathlib.Path) -> str:
    try:
        return extract_document(path.read_bytes(), path.name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    from lens_contract import run_contract_subcommands

    if run_contract_subcommands(
        MANIFEST,
        app_path="cite_analyser.api:app",
        default_port=8006,
        env_prefix="CITE_ANALYSER",
        argv=argv,
    ):
        return 0

    parser = argparse.ArgumentParser(
        prog="cite-analyser", description="Citation and reference analysis"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyse = sub.add_parser("analyse", help="Extract citations from a document")
    p_analyse.add_argument("file", type=pathlib.Path)
    p_analyse.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.command == "analyse":
        result = analyze(_read_text(args.file))
        if args.as_json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            refs = result["references"]
            cross = result["crossReference"]
            print(f"{len(refs)} references, {len(result['inTextCitations'])} in-text citations")
            print(
                f"  unmatched: {len(cross['unmatchedBibliography'])} bibliography, "
                f"{len(cross['unmatchedInText'])} in-text"
                + (
                    f", {len(cross['nearMatches'])} near matches"
                    if cross.get("nearMatches")
                    else ""
                )
            )
            if result["duplicates"]:
                print(f"  duplicates at positions {result['duplicates']}")
            for i, r in enumerate(refs):
                year = r.get("year") or "n.d."
                dup = " (duplicate)" if i in result["duplicates"] else ""
                author = r["authors"][0] if r["authors"] else "?"
                print(f"  [{r['detectedStyle']}] {author} ({year}){dup}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
