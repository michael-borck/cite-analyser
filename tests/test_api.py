"""/analyse contract tests (FastAPI TestClient)."""

from fastapi.testclient import TestClient

from cite_analyser.api import app

client = TestClient(app)

ESSAY = b"""# Essay

Dialogue matters (Vygotsky, 1978).

## References

Vygotsky, L. S. (1978). *Mind in Society.* Harvard University Press.
"""


def test_analyse_returns_extracted_text_and_full_block():
    resp = client.post(
        "/analyse",
        files={"file": ("essay.md", ESSAY, "text/markdown")},
        data={"verify": "false", "citationStyle": "auto", "checkInText": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fileName"] == "essay.md"
    assert "Vygotsky" in body["extractedText"]
    assert body["totalReferences"] == 1
    assert body["detectedStyle"] == "apa"
    assert body["references"][0]["authors"] == ["Vygotsky, L. S."]
    assert body["inTextCitations"][0]["authors"] == ["Vygotsky"]


def test_analyse_rejects_unsupported_type():
    resp = client.post("/analyse", files={"file": ("clip.mp4", b"xxxx", "video/mp4")})
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_analyse_honours_check_in_text_false():
    resp = client.post(
        "/analyse",
        files={"file": ("essay.md", ESSAY, "text/markdown")},
        data={"checkInText": "false"},
    )
    assert resp.json()["inTextCheckSkipped"] is True


def test_health_and_manifest():
    assert client.get("/health").json()["status"] == "ok"
    manifest = client.get("/manifest").json()
    assert manifest["name"] == "cite-analyser"
