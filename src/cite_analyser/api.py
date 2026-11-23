"""HTTP API — the lens family contract plus POST /analyse."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from lens_contract import add_contract_routes, add_cors

from .analysis import analyze
from .extractors import extract_document
from .manifest import MANIFEST

app = FastAPI(title=MANIFEST["name"], version=MANIFEST["version"])
add_contract_routes(app, MANIFEST)
add_cors(app, env_prefix="CITE_ANALYSER")


class AnalyseOptions:
    """Mirror of the cite-sight server's /analyse form options."""

    def __init__(
        self,
        verify: bool = False,
        mailto: str = "",
        check_urls: bool = True,
        check_doi: bool = True,
    ):
        self.verify = verify
        self.mailto = mailto or None
        self.check_urls = check_urls
        self.check_doi = check_doi


@app.post("/analyse")
async def analyse(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI's declared-parameter idiom
    verify: bool = Form(default=False),
    mailto: str = Form(default=""),
    checkUrls: bool = Form(default=True),
    checkDoi: bool = Form(default=True),
    checkInText: bool = Form(default=True),
    citationStyle: str = Form(default="auto"),
    documentType: str = Form(default=""),
    semanticScholarApiKey: str = Form(default=""),
    openAlexApiKey: str = Form(default=""),
) -> dict[str, Any]:
    """Analyse a document: references, in-text citations, cross-reference,
    duplicates, format issues, and (when verify=true) database verdicts.

    Response keys mirror cite-sight's AnalysisResult.references block plus the
    extracted text, so the frozen engine's server can delegate wholesale."""
    name = file.filename or "document.txt"
    raw = await file.read()
    try:
        text = extract_document(raw, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result = analyze(
        text,
        verify=verify,
        mailto=mailto,
        check_urls=checkUrls,
        check_doi=checkDoi,
        semantic_scholar_api_key=semanticScholarApiKey or None,
        openalex_api_key=openAlexApiKey or None,
        citation_style=citationStyle
        if citationStyle in ("auto", "apa", "mla", "chicago")
        else "auto",
        check_in_text=checkInText,
        document_type=documentType or None,
    )
    return {"fileName": name, "extractedText": text, **result}
