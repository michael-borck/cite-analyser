"""HTTP API — the lens family contract plus POST /analyse."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from lens_contract import add_contract_routes, add_cors

from .extractor import extract_references
from .extractors import extract_document
from .manifest import MANIFEST

app = FastAPI(title=MANIFEST["name"], version=MANIFEST["version"])
add_contract_routes(app, MANIFEST)
add_cors(app, env_prefix="CITE_ANALYSER")


@app.post("/analyse")
async def analyse(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI's declared-parameter idiom
) -> dict[str, Any]:
    """Extract references and in-text citations from a document."""
    name = file.filename or "document.txt"
    raw = await file.read()
    try:
        text = extract_document(raw, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result = extract_references(text)
    return {"fileName": name, **result}
