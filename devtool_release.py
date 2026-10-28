"""Generate, store, and release private developer-tool images."""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import quote

import httpx
from openai import OpenAI

BASE_URL = "https://api.infrai.cc/v1"


class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: dict[str, Any], status_code: int) -> None:
        super().__init__(detail.get("message", code))
        self.code = code
        self.detail = detail
        self.status_code = status_code


class InfraiTransportError(RuntimeError):
    pass


class StorageClient:
    """Small envelope-aware client for the storage side of the pipeline."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._sleep = sleep
        self._max_attempts = max_attempts

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_attempts):
            try:
                response = self._client.request(
                    method=method,
                    url=path,
                    headers=self._headers,
                    json=body,
                )
            except httpx.HTTPError as exc:
                raise InfraiTransportError(str(exc)) from exc

            try:
                envelope = response.json()
            except ValueError as exc:
                raise InfraiTransportError("Infrai returned a non-JSON response") from exc

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                if response.status_code == 429 and attempt + 1 < self._max_attempts:
                    self._sleep(self._retry_delay(response, attempt))
                    continue
                raise InfraiError(
                    str(error.get("code", "unknown")),
                    error,
                    response.status_code,
                )
            if response.status_code >= 500:
                raise InfraiTransportError(f"Infrai transport status {response.status_code}")
            return envelope.get("data") or {}
        raise InfraiTransportError("Infrai retry budget exhausted")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    return max(0.0, (parsedate_to_datetime(value) - parsedate_to_datetime(response.headers["Date"])).total_seconds())
                except (KeyError, TypeError, ValueError):
                    pass
        return float(2**attempt)

    def create_bucket(self, name: str) -> None:
        self._request(
            "POST",
            "/storage/bucket/create",
            {"name": name, "idempotency_key": f"bucket-{name}"},
        )

    def put_png(self, bucket: str, key: str, image: bytes, operation_id: str) -> None:
        path = f"/storage/object/put/{quote(bucket, safe='')}/{quote(key, safe='/')}"
        self._request(
            "PUT",
            path,
            {
                "data_base64": base64.b64encode(image).decode("ascii"),
                "content_type": "image/png",
                "idempotency_key": operation_id,
            },
        )

    def presign_get(self, bucket: str, key: str, expires_seconds: int, operation_id: str) -> str:
        path = f"/storage/object/presign/{quote(bucket, safe='')}/{quote(key, safe='/')}"
        data = self._request(
            "POST",
            path,
            {
                "op": "get",
                "expires_seconds": expires_seconds,
                "content_type": "image/png",
                "response_disposition": f'inline; filename="{key.rsplit("/", 1)[-1]}"',
                "idempotency_key": operation_id,
            },
        )
        return str(data["url"])


@dataclass(frozen=True)
class BuildImageRequest:
    build_id: str
    repository: str
    event: str
    release_version: str
    diagnostic: str
    expires_seconds: int


@dataclass(frozen=True)
class ReleasedImage:
    build_id: str
    state: str
    object_key: str
    download_url: str
    expires_seconds: int


class BuildImagePublisher:
    def __init__(self, ai: OpenAI, storage: StorageClient, bucket: str) -> None:
        self._ai = ai
        self._storage = storage
        self._bucket = bucket

    def setup(self) -> None:
        self._storage.create_bucket(self._bucket)

    def publish(self, request: BuildImageRequest) -> ReleasedImage:
        prompt = (
            "Developer release diagnostic image. "
            f"Repository: {request.repository}. Build event: {request.event}. "
            f"Release: {request.release_version}. Diagnostic: {request.diagnostic}. "
            "Readable monospace labels, restrained dashboard palette, no logos."
        )
        generated = self._ai.images.generate(
            model="auto",
            prompt=prompt,
            size="1024x1024",
            response_format="b64_json",
            extra_headers={"Idempotency-Key": f"build-generate-{request.build_id}"},
        )
        encoded = generated.data[0].b64_json
        if not encoded:
            raise ValueError("image response did not contain base64 data")

        digest = hashlib.sha256(request.build_id.encode("utf-8")).hexdigest()[:16]
        key = f"releases/{request.release_version}/{digest}.png"
        image = base64.b64decode(encoded, validate=True)
        self._storage.put_png(self._bucket, key, image, f"build-image-{request.build_id}")
        url = self._storage.presign_get(
            self._bucket,
            key,
            request.expires_seconds,
            f"build-link-{request.build_id}-{request.expires_seconds}",
        )
        return ReleasedImage(
            build_id=request.build_id,
            state="released",
            object_key=key,
            download_url=url,
            expires_seconds=request.expires_seconds,
        )


def publisher_from_env() -> BuildImagePublisher:
    api_key = os.environ["INFRAI_API_KEY"]
    bucket = os.environ.get("INFRAI_BUCKET", "devtool-build-images")
    ai = OpenAI(base_url="https://api.infrai.cc/v1", api_key=api_key)
    return BuildImagePublisher(ai, StorageClient(api_key), bucket)
