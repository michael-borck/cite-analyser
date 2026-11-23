"""HTTP API — the lens family contract plus POST /analyse."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from lens_contract import add_contract_routes, add_cors

from .extractor import extract_references
from .manifest import MANIFEST

app = FastAPI(title=MANIFEST["name"], version=MANIFEST["version"])
add_contract_routes(app, MANIFEST)
add_cors(app, env_prefix="CITE_ANALYSER")

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


@app.post("/analyse")
async def analyse(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI's declared-parameter idiom
) -> dict[str, Any]:
    """Extract references and in-text citations from a text document."""
    name = file.filename or "document.txt"
    if not any(name.lower().endswith(sfx) for sfx in TEXT_SUFFIXES):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only text documents (.txt/.md) are supported in this milestone; "
                "pdf/docx extraction lands with the extractors port."
            ),
        )
    raw = await file.read()
    result = extract_references(raw.decode("utf-8", errors="replace"))
    return {"fileName": name, **result}
