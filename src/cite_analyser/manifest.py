"""Capability manifest for the lens family (consumed by auto-analyser)."""

from lens_contract import make_manifest

MANIFEST = make_manifest(
    name="cite-analyser",
    accepts=["text", "document"],
    extensions=[".txt", ".md", ".pdf", ".docx"],
    auto_routable=True,
    produces="CitationAnalysis",
)
