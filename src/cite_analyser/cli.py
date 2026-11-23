"""CLI: `cite-analyser analyse <file>` plus the family's serve/manifest."""

from __future__ import annotations

import argparse
import json
import pathlib

from .extractor import extract_references
from .extractors import extract_document
from .manifest import MANIFEST


def _read_text(path: pathlib.Path) -> str:
    return extract_document(path.read_bytes(), path.name)


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
        result = extract_references(_read_text(args.file))
        if args.as_json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            refs = result["references"]
            print(f"{len(refs)} references, {len(result['inTextCitations'])} in-text citations")
            for r in refs:
                year = r.get("year") or "n.d."
                print(
                    f"  [{r['detectedStyle']}] {r['authors'][0] if r['authors'] else '?'} ({year})"
                )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
