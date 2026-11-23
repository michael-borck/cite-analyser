"""CLI: `cite-analyser analyse <file>` plus the family's serve/manifest."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .analysis import analyze
from .claims import SourceDocument, check_claims
from .extractors import extract_document
from .llm import chat
from .manifest import MANIFEST
from .verifier import verify_references  # noqa: F401 — re-exported for library users


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
    p_analyse.add_argument(
        "--verify", action="store_true", help="Verify references against academic databases"
    )
    p_analyse.add_argument("--email", help="Contact email for the Crossref polite pool (mailto)")
    p_claims = sub.add_parser(
        "claims",
        help=(
            "Does your supplied source support this sentence? Checks each "
            "citation-bearing sentence against the sources you provide."
        ),
    )
    p_claims.add_argument("file", type=pathlib.Path, help="The document to check")
    p_claims.add_argument(
        "--sources",
        type=pathlib.Path,
        required=True,
        help=(
            'Source manifest JSON: {"sources": [{"reference": 1, "path": '
            '"paper.pdf"}], "provider": {"base_url": "...", "model": "..."}}'
        ),
    )
    p_claims.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.command == "analyse":
        result = analyze(_read_text(args.file), verify=args.verify, mailto=args.email)
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
            verdicts = result.get("verifications") or []
            if verdicts:
                counts: dict[str, int] = {}
                for v in verdicts:
                    counts[v["status"]] = counts.get(v["status"], 0) + 1
                print("  verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
                for v in verdicts:
                    if v["status"] in ("verified", "likely_valid"):
                        continue
                    who = v["reference"]["authors"][0] if v["reference"].get("authors") else "?"
                    print(f"    {v['status']}: {who} — {', '.join(v['flags'])}")
            for i, r in enumerate(refs):
                year = r.get("year") or "n.d."
                dup = " (duplicate)" if i in result["duplicates"] else ""
                author = r["authors"][0] if r["authors"] else "?"
                print(f"  [{r['detectedStyle']}] {author} ({year}){dup}")
        return 0

    if args.command == "claims":
        manifest = json.loads(args.sources.read_text())
        provider = manifest.get("provider") or {}
        sources = [
            SourceDocument(
                reference=int(entry["reference"]),
                name=pathlib.Path(entry["path"]).name,
                text=_read_text(pathlib.Path(entry["path"])),
            )
            for entry in manifest.get("sources") or []
        ]
        document_text = _read_text(args.file)
        extraction = analyze(document_text)
        result = check_claims(
            document_text,
            extraction["references"],
            extraction["inTextCitations"],
            sources,
            lambda prompt: chat(
                provider.get("base_url") or "http://localhost:11434/v1",
                provider.get("model", ""),
                prompt,
                provider.get("api_key", ""),
            ),
        )
        if args.as_json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(result["question"])
            for finding in result["findings"]:
                print(f"  [{finding['status']}] {finding['claim'][:100]}")
                if finding.get("reason"):
                    print(f"      {finding['reason']}")
                for evidence in finding.get("evidence") or []:
                    print(f"      \u201c{evidence['quote'][:120]}\u201d")
            print()
            print(result["disclaimer"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
