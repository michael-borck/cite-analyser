"""Reference verification — port of verifier.ts (verdict engine).

The full cascade: format validation → DOI resolution → academic search
(crossref → datacite here; openalex/semantic-scholar/arxiv/europepmc plug in
via the optional provider seams as they are ported) → verdict with
author/year corroboration, grey-literature handling, degraded-lookup
protection, and ambiguity demotion.
"""

from __future__ import annotations

import datetime

from .duplicates import normalize_title
from .lookup_error import LookupError
from .matching import (
    author_corroboration,
    bibliographic_conflicts,
    character_similarity,
    repaired_title_query,
    subtitle_variant,
    title_token_conflict,
    year_corroboration,
)
from .providers import (
    clear_run_state,
    extract_arxiv_id,
    lookup_arxiv_id,
    resolve_doi,
    search_arxiv,
    search_crossref,
    search_datacite,
    search_europe_pmc,
    search_openalex,
    search_semantic_scholar,
)

# Title-similarity bands.
TITLE_FLOOR = 0.3
TITLE_STRONG = 0.9
TITLE_MODERATE = 0.6

CURRENT_YEAR = datetime.date.today().year


def title_similarity(a: str, b: str) -> float:
    """Jaccard word overlap with a bounded edit-distance rescue for nearly
    identical titles. Heuristic score in [0, 1], not a calibrated probability."""
    if not a or not b:
        return 0.0
    set_a = {w for w in normalize_title(a).split(" ") if w}
    set_b = {w for w in normalize_title(b).split(" ") if w}
    if not set_a or not set_b:
        return 0.0
    overlap = len(set_a & set_b) / len(set_a | set_b)
    characters = character_similarity(normalize_title(a), normalize_title(b))
    if not title_token_conflict(a, b) and characters >= 0.94:
        return max(overlap, characters)
    return overlap


def title_containment(a: str, b: str) -> tuple[float, int]:
    """How much of the shorter title's word set is in the longer one (plus the
    smaller set's size) — the main-title/subtitle signature."""
    if not a or not b:
        return 0.0, 0
    set_a = {w for w in normalize_title(a).split(" ") if w}
    set_b = {w for w in normalize_title(b).split(" ") if w}
    if not set_a or not set_b:
        return 0.0, 0
    small, large = (set_a, set_b) if len(set_a) <= len(set_b) else (set_b, set_a)
    inter = sum(1 for w in small if w in large)
    return inter / len(small), len(small)


def looks_grey_literature(ref: dict) -> bool:
    """Venue-less references by organisational authors — industry blogs,
    standards bodies, reports. Absence from academic indexes is the expected
    state for these, not evidence of fabrication."""
    if ref.get("doi") or ref.get("journal") or ref.get("volume") or ref.get("pages"):
        return False
    if ref.get("url"):
        return True
    authors = ref.get("authors") or []
    if len(authors) == 1:
        import regex

        a = authors[0].strip()
        person_shaped = regex.search(r",\s*[A-Z]", a)
        return not person_shaped and len(a) >= 3
    return False


def _assess_academic_match(
    ref: dict, matched: dict, title_sim: float, doi_resolved: bool, doi_had_metadata: bool
) -> dict:
    author = author_corroboration(ref.get("authors") or [], matched.get("authors") or [])
    year = year_corroboration(ref.get("year"), matched.get("year"))
    flags = bibliographic_conflicts(ref, matched)
    if author == "mismatch":
        flags.append("author_mismatch")
    if year == "mismatch":
        flags.append("year_mismatch")
    if (
        ref.get("title")
        and matched.get("title")
        and title_token_conflict(ref["title"], matched["title"])
    ):
        flags.append("title_token_mismatch")
    if doi_had_metadata or not doi_resolved:
        if "title_token_mismatch" in flags or author == "mismatch":
            if doi_resolved and title_sim < 0.7:
                flags.append("doi_title_mismatch")
            return {"status": "suspicious", "confidence": 0.35, "flags": flags}

    if doi_resolved:
        if not doi_had_metadata:
            flags.append("doi_unconfirmed")
            return {"status": "likely_valid", "confidence": 0.65, "flags": flags}
        if title_sim >= TITLE_MODERATE or subtitle_variant(
            ref.get("title") or "", matched.get("title") or ""
        ):
            if title_sim < TITLE_STRONG:
                flags.append("title_variant")
            status = "likely_valid" if (flags or author != "match") else "verified"
            confidence = 0.7 if flags else 0.97
            return {"status": status, "confidence": confidence, "flags": flags}
        flags.append("doi_title_mismatch")
        return {"status": "suspicious", "confidence": 0.35, "flags": flags}

    if (
        author == "match"
        and title_sim < TITLE_STRONG
        and subtitle_variant(ref.get("title") or "", matched.get("title") or "")
    ):
        flags.append("title_variant")
        return {"status": "likely_valid", "confidence": 0.7, "flags": flags}

    if title_sim >= TITLE_STRONG:
        corroborated = author == "match" and not flags
        if corroborated:
            return {"status": "verified", "confidence": 0.92, "flags": flags}
        return {"status": "likely_valid", "confidence": 0.8, "flags": flags}

    if title_sim >= TITLE_MODERATE:
        if author == "match" and year != "mismatch":
            flags.append("title_variant")
            return {"status": "likely_valid", "confidence": 0.7, "flags": flags}
        return {"status": "suspicious", "confidence": 0.4, "flags": flags}

    flags.append("weak_match")
    return {"status": "suspicious", "confidence": 0.3, "flags": flags}


def _compute_intrinsic_flags(ref: dict, matched: dict | None, has_format_issues: bool) -> list[str]:
    flags: list[str] = []
    if ref.get("year") and ref["year"] > CURRENT_YEAR:
        flags.append("future_date")
    if not ref.get("doi"):
        flags.append("no_doi")
    if has_format_issues:
        flags.append("format_issues")
    if (
        matched
        and matched.get("year")
        and ref.get("year")
        and abs(matched["year"] - ref["year"]) > 1
    ):
        flags.append("year_mismatch")
    return flags


def _categorize(status: str, flags: list[str], matched: dict | None) -> str:
    if "grey_literature" in flags:
        return "not_indexed_expected"
    if not matched:
        return "none"
    if "doi_title_mismatch" in flags or "doi_mismatch" in flags:
        return "conflict"
    if "ambiguous_match" in flags:
        return "match_dubious"
    if status == "suspicious":
        if "author_mismatch" in flags or "weak_match" in flags:
            return "match_dubious"
        return "metadata_drift"
    if "edition_difference" in flags:
        return "variant_record"
    if any(f.endswith("_mismatch") for f in flags) or "title_variant" in flags:
        return "metadata_drift"
    return "exact"


class VerifyOptions:
    def __init__(
        self,
        offline: bool = False,
        mailto: str | None = None,
        check_urls: bool = True,
        check_doi: bool = True,
        semantic_scholar_api_key: str | None = None,
        openalex_api_key: str | None = None,
    ):
        self.offline = offline
        self.mailto = mailto
        self.check_urls = check_urls
        self.check_doi = check_doi
        self.semantic_scholar_api_key = semantic_scholar_api_key
        self.openalex_api_key = openalex_api_key


def verify_single_reference(ref: dict, options: VerifyOptions) -> dict:
    """Verify one reference through the cascade; returns the verification."""
    # formatValidator arrives with its own milestone — the seam returns no
    # issues until then (flagged in the README status table).
    format_issues: list = []

    if options.offline:
        return {
            "reference": ref,
            "status": "format_only",
            "matchCategory": "none",
            "formatIssues": format_issues,
            "confidenceScore": 0,
            "flags": ["offline"],
            "evidence": {"existence": "unknown", "metadata": "unknown"},
        }

    matched: dict | None = None
    doi_resolved = False
    doi_had_metadata = False
    similarity = 0.0
    api_errored = False
    failure: tuple[str, str] | None = None

    def note_failure(err: Exception) -> None:
        nonlocal failure, api_errored
        api_errored = True
        if isinstance(err, LookupError):
            if failure is None or (err.reason == "rate_limited" and failure[1] != "rate_limited"):
                failure = (err.service, err.reason)

    authors = ref.get("authors") or []
    search_query = " ".join(x for x in [authors[0] if authors else "", ref.get("title") or ""] if x)

    best_score = 0.0
    matched_corroborated = False
    candidates: dict[str, tuple[dict, float]] = {}

    def ambiguous() -> bool:
        scores = sorted((s for _, s in candidates.values()), reverse=True)
        return len(scores) > 1 and scores[0] - scores[1] < 0.08

    def strong_match() -> bool:
        return bool(
            matched
            and similarity >= 0.9
            and matched_corroborated
            and year_corroboration(ref.get("year"), matched.get("year")) != "mismatch"
            and not title_token_conflict(ref.get("title") or "", matched.get("title") or "")
            and not bibliographic_conflicts(ref, matched)
            and not ambiguous()
        )

    def consider_results(results: list[dict]) -> None:
        nonlocal best_score, similarity, matched, matched_corroborated
        for wk in results:
            sim = title_similarity(ref.get("title") or "", wk.get("title") or "")
            if sim < TITLE_FLOOR:
                continue
            author = author_corroboration(authors, wk.get("authors") or [])
            year = year_corroboration(ref.get("year"), wk.get("year"))
            score = (
                sim
                + (0.25 if author == "match" else -0.2 if author == "mismatch" else 0)
                + (0.1 if year == "match" else -0.15 if year == "mismatch" else 0)
                - 0.1 * len(bibliographic_conflicts(ref, wk))
                + (
                    0.1
                    if ref.get("journal")
                    and wk.get("journal")
                    and normalize_title(ref["journal"]) == normalize_title(wk["journal"])
                    else 0
                )
            )
            wid = (wk.get("doi") or "").lower() or ":".join(
                [
                    normalize_title(wk.get("title") or ""),
                    "|".join(sorted(normalize_title(a) for a in wk.get("authors") or [])),
                    str(wk.get("year")),
                ]
            )
            if wid not in candidates or candidates[wid][1] < score:
                candidates[wid] = (wk, score)
            if score > best_score:
                best_score = score
                similarity = sim
                matched = wk
                matched_corroborated = author == "match"

    # --- DOI resolution ---
    if ref.get("doi") and options.check_doi:
        try:
            resolved = resolve_doi(ref["doi"], options.mailto)
            if resolved:
                matched = resolved
                doi_resolved = True
                doi_had_metadata = bool(resolved.get("title"))
                similarity = (
                    title_similarity(ref.get("title") or "", resolved["title"])
                    if resolved.get("title")
                    else 0.0
                )
        except Exception as e:  # noqa: BLE001
            note_failure(e)

    # --- arXiv ID: authoritative for preprints (DataCite-registered, so the
    # Crossref-first cascade cannot see them). ---
    if not matched and ref.get("raw"):
        arxiv_id = extract_arxiv_id(ref["raw"])
        if arxiv_id:
            try:
                ark = lookup_arxiv_id(arxiv_id)
                if (
                    ark
                    and title_similarity(ref.get("title") or "", ark.get("title") or "")
                    >= TITLE_FLOOR
                ):
                    consider_results([ark])
            except Exception as e:  # noqa: BLE001
                note_failure(e)

    # --- Academic search cascade ---
    if not doi_resolved and not strong_match() and len(search_query) > 3:
        searches = [
            lambda: search_crossref(search_query, options.mailto),
            lambda: search_openalex(search_query, options.mailto, options.openalex_api_key),
            lambda: search_semantic_scholar(search_query, options.semantic_scholar_api_key),
            # Last resorts: preprint-only works, then repository deposits and
            # biomedical indexes.
            lambda: search_arxiv(ref.get("title") or ""),
            lambda: search_datacite(ref.get("title") or "", options.mailto),
            lambda: search_europe_pmc(ref.get("title") or ""),
        ]
        for search in searches:
            if strong_match():
                break
            try:
                consider_results(search())
            except Exception as e:  # noqa: BLE001
                note_failure(e)
        if not strong_match() and ref.get("title") and search_query != ref["title"]:
            try:
                consider_results(search_crossref(ref["title"], options.mailto))
            except Exception as e:  # noqa: BLE001
                note_failure(e)
        # A typo in a distinctive word can zero out a full-title search; retry
        # the two most reliable fuzzy providers with the stripped query.
        if not strong_match() and ref.get("title"):
            repaired = repaired_title_query(ref["title"])
            if repaired and repaired != normalize_title(ref["title"]):
                for search in (
                    lambda: search_crossref(repaired, options.mailto),
                    lambda: search_openalex(repaired, options.mailto, options.openalex_api_key),
                ):
                    if strong_match():
                        break
                    try:
                        consider_results(search())
                    except Exception as e:  # noqa: BLE001
                        note_failure(e)

    # --- Verdict ---
    has_format_issues = bool(format_issues)
    flags = _compute_intrinsic_flags(ref, matched, has_format_issues)

    if matched:
        assessment = _assess_academic_match(
            ref, matched, similarity, doi_resolved, doi_had_metadata
        )
        status = assessment["status"]
        confidence = assessment["confidence"]
        flags.extend(assessment["flags"])
        if status == "suspicious" and api_errored and not doi_resolved and not matched_corroborated:
            status = "unverified"
            confidence = 0
            flags.extend(["verification_unavailable", "degraded_lookup"])
            if failure:
                flags.append(f"{failure[1]}:{failure[0]}")
    elif api_errored:
        status = "unverified"
        confidence = 0
        flags.append("verification_unavailable")
        if failure:
            flags.append(f"{failure[1]}:{failure[0]}")
    elif looks_grey_literature(ref):
        flags.append("grey_literature")
        status = "not_found"
        confidence = 0
    else:
        status = "not_found"
        confidence = 0

    if not doi_resolved and ambiguous():
        flags.append("ambiguous_match")
        if status == "verified":
            status = "likely_valid"
        confidence = min(confidence, 0.65)

    unavailable = failure if status == "unverified" else None
    return {
        "reference": ref,
        "status": status,
        "matchCategory": _categorize(status, flags, matched),
        "formatIssues": format_issues,
        "matchedWork": matched,
        "confidenceScore": confidence,
        "flags": list(dict.fromkeys(flags)),  # ordered unique
        "evidence": {
            "existence": "found" if matched else "unknown" if api_errored else "not_found",
            "metadata": (
                "unknown"
                if not matched or not matched.get("title")
                else "conflict"
                if any(f.endswith("_mismatch") for f in flags)
                else "match"
                if status == "verified"
                else "partial"
            ),
        },
        "unavailable": {"service": unavailable[0], "reason": unavailable[1]}
        if unavailable
        else None,
    }


def verify_references(refs: list[dict], options: VerifyOptions, on_verified=None) -> list[dict]:
    """Verify parsed references one at a time (paced, cached per run)."""
    clear_run_state()
    results = []
    for i, ref in enumerate(refs):
        verification = verify_single_reference(ref, options)
        results.append(verification)
        if on_verified:
            on_verified(verification, i, len(refs))
    return results
