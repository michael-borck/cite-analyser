"""Publication notices (retractions/corrections) — port of publicationUpdates.ts.

A Crossref "updated-by" notice about a work is not proof of anything if the
lookup failed; absence of a notice does not certify the paper was never
retracted. Fresh per-run records are reused rather than re-fetched."""

from __future__ import annotations

import datetime

from .providers import lookup_doi_crossref


def check_publication_updates(
    work: dict, mailto: str | None = None, allow_lookup: bool = True
) -> dict:
    """Return {status: checked|not_available|unavailable, updates, checkedAt?}."""
    try:
        checked_at = work.get("publicationStatusCheckedAt")
        fresh = False
        if checked_at:
            try:
                when = datetime.datetime.fromisoformat(checked_at)
                age = (datetime.datetime.now(datetime.UTC) - when).total_seconds()
                fresh = age < 86_400
            except ValueError:
                fresh = False
        record = work if fresh else None
        if record is None and work.get("doi") and allow_lookup:
            record = lookup_doi_crossref(work["doi"], mailto)
        if not record or not record.get("publicationStatusCheckedAt"):
            return {"status": "not_available", "updates": []}
        return {
            "status": "checked",
            "updates": record.get("publicationUpdates") or [],
            "checkedAt": record["publicationStatusCheckedAt"],
        }
    except Exception:  # noqa: BLE001 — a notice check must never break a verdict
        return {"status": "unavailable", "updates": []}
