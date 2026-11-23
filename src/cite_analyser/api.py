"""HTTP API — the lens family contract plus POST /analyse."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from lens_contract import add_contract_routes, add_cors
from pydantic import BaseModel, Field

from .analysis import analyze
from .claims import SourceDocument, check_claims
from .extractors import extract_document
from .llm import LLMError, chat
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


# ---------------------------------------------------------------------------
# Claim-evidence checking ("Does your supplied source support this sentence?")
# ---------------------------------------------------------------------------


class ClaimFile(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=16, max_length=200_000_000)


class ClaimSourceIn(ClaimFile):
    reference: int = Field(ge=1, description="One-based bibliography position")


class ClaimProviderIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class ClaimsRequest(BaseModel):
    document: ClaimFile
    sources: list[ClaimSourceIn] = Field(default_factory=list, max_length=100)
    provider: ClaimProviderIn = ClaimProviderIn()
    max_claims: int = Field(default=200, ge=1, le=1000)


@app.post("/claims")
def claims(req: ClaimsRequest) -> dict[str, Any]:
    """Check each citation-bearing sentence against the sources the student
    supplied for those references. Advisory only — findings carry verbatim
    quotes for the student to read against the original."""
    import base64

    try:
        document_text = extract_document(
            base64.b64decode(req.document.data_base64), req.document.name
        )
    except (ValueError, Exception) as e:
        raise HTTPException(status_code=400, detail=f"Could not read the document: {e}") from e

    sources = []
    for source in req.sources:
        try:
            sources.append(
                SourceDocument(
                    reference=source.reference,
                    name=source.name,
                    text=extract_document(base64.b64decode(source.data_base64), source.name),
                )
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Could not read source #{source.reference} ({source.name}): {e}",
            ) from e

    extraction = analyze(document_text)
    provider = req.provider
    try:
        result = check_claims(
            document_text,
            extraction["references"],
            extraction["inTextCitations"],
            sources,
            lambda prompt: chat(
                provider.base_url or "http://localhost:11434/v1",
                provider.model,
                prompt,
                provider.api_key,
            ),
            max_claims=req.max_claims,
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"fileName": req.document.name, **result}
