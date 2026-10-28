import base64
from types import SimpleNamespace

from fastapi.testclient import TestClient

from devtool_release import BuildImagePublisher
from release_service import app, get_publisher


class FakeImages:
    def generate(self, **request):
        assert request["model"] == "auto"
        assert "build.completed" in request["prompt"]
        assert request["extra_headers"]["Idempotency-Key"] == "build-generate-build-1842"
        image = base64.b64encode(b"png-bytes").decode("ascii")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=image)])


class RecordingStorage:
    def __init__(self):
        self.put = None

    def put_png(self, bucket, key, image, operation_id):
        self.put = (bucket, key, image, operation_id)

    def presign_get(self, bucket, key, expires_seconds, operation_id):
        assert self.put[:3] == (bucket, key, b"png-bytes")
        assert expires_seconds == 600
        return "https://download.example/signed-build-image"


def test_completed_build_is_stored_before_release_link_is_returned():
    storage = RecordingStorage()
    publisher = BuildImagePublisher(SimpleNamespace(images=FakeImages()), storage, "ci-images")
    app.dependency_overrides[get_publisher] = lambda: publisher

    client = TestClient(app)
    response = client.post(
        "/build-images",
        json={
            "build_id": "build-1842",
            "repository": "acme/compiler",
            "event": "build.completed",
            "release_version": "v2.4.1",
            "diagnostic": "All 318 checks passed in 94 seconds",
            "expires_seconds": 600,
        },
    )
    client.close()

    app.dependency_overrides.clear()
    assert response.status_code == 201
    assert response.json()["state"] == "released"
    assert response.json()["download_url"] == "https://download.example/signed-build-image"
    assert storage.put[3] == "build-image-build-1842"
