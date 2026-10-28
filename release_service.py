"""HTTP entry point for the build-image release pipeline."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from devtool_release import BuildImagePublisher, BuildImageRequest, InfraiError, publisher_from_env


class GenerateBuildImage(BaseModel):
    build_id: str = Field(min_length=1, max_length=120)
    repository: str = Field(min_length=1, max_length=200)
    event: str = Field(min_length=1, max_length=80)
    release_version: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    diagnostic: str = Field(min_length=1, max_length=1000)
    expires_seconds: int = Field(default=900, ge=60, le=86400)


class BuildImageRelease(BaseModel):
    build_id: str
    state: str
    object_key: str
    download_url: str
    expires_seconds: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    publisher = publisher_from_env()
    publisher.setup()
    app.state.publisher = publisher
    yield


app = FastAPI(title="Developer build image publisher", lifespan=lifespan)


def get_publisher() -> BuildImagePublisher:
    return app.state.publisher


@app.post("/build-images", response_model=BuildImageRelease, status_code=201)
def generate_build_image(
    body: GenerateBuildImage,
    publisher: Annotated[BuildImagePublisher, Depends(get_publisher)],
) -> BuildImageRelease:
    try:
        result = publisher.publish(BuildImageRequest(**body.model_dump()))
    except InfraiError as exc:
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc
    return BuildImageRelease(**result.__dict__)
