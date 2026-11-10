from fastapi.testclient import TestClient

from app.main import app


def test_spa_route_blocks_path_traversal():
    """A URL-encoded traversal sequence reaches the `path` param as a literal
    "../" (TestClient/httpx normalizes a *plain* "../" in the request URL
    itself before it is even sent, so the encoded form is required to
    exercise the server-side containment check).
    """
    with TestClient(app) as c:
        r = c.get("/..%2f..%2f..%2f..%2fetc%2fpasswd")
        assert r.status_code == 200
        # Falls through to the SPA index.html fallback, not an arbitrary file.
        assert "<!doctype html>" in r.text.lower()
        assert "root:" not in r.text
