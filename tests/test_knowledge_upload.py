"""Upload runs off the event loop thread (see app/api/knowledge.py) but must still behave like
a normal request: 200 with a queued document, 400 on bad content."""


def test_upload_txt_document(client, base):
    text = b"This is a readable knowledge base document with enough characters to pass validation."
    response = client.post(
        f"{base}/knowledge/upload",
        files={"file": ("notes.txt", text, "text/plain")},
        data={"title": "Notes"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filename"] == "notes.txt"


def test_upload_rejects_unsupported_type(client, base):
    response = client.post(
        f"{base}/knowledge/upload",
        files={"file": ("image.png", b"\x89PNG\r\n", "image/png")},
    )
    assert response.status_code == 400
