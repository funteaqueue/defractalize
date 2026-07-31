from __future__ import annotations

import asyncio
import io
import os
import sys
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError

CLEANER_ROOT = Path("/opt/cleaner")
WEIGHT_PATH = Path(
    os.getenv("CLEANER_WEIGHT", str(CLEANER_ROOT / "weights" / "latent_residual.pt"))
)
VAE_ID = os.getenv("VAE_ID", "black-forest-labs/FLUX.2-VAE")
REQUIRE_CUDA = os.getenv("REQUIRE_CUDA", "1") == "1"
OFFLOAD_AFTER_REQUEST = os.getenv("OFFLOAD_AFTER_REQUEST", "1") == "1"
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "50000000"))

sys.path.insert(0, str(CLEANER_ROOT))
modeling = import_module("modeling")


class CleanerRuntime:
    def __init__(self) -> None:
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if REQUIRE_CUDA and self.device != "cuda":
            raise RuntimeError("CUDA is required but no compatible NVIDIA GPU was found")
        self.dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self.vae, self.residual, self.metadata = modeling.load_models(
            str(WEIGHT_PATH),
            VAE_ID,
            "cpu",
        )
        self.encode, self.decode = modeling.build_fns(self.vae, False)
        self.lock = asyncio.Lock()

    def process(self, payload: bytes, alpha: float) -> bytes:
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                source = ImageOps.exif_transpose(opened)
                source.load()
                width, height = source.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Image has {width * height:,} pixels; "
                            f"the limit is {MAX_IMAGE_PIXELS:,}"
                        ),
                    )
                rgba = source.convert("RGBA")
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as error:
            raise HTTPException(status_code=415, detail="Could not decode image") from error

        alpha_channel = rgba.getchannel("A")
        rgb_array = np.asarray(rgba.convert("RGB"), dtype=np.uint8)

        self.vae.to(self.device)
        self.residual.to(self.device)
        try:
            restored = modeling.restore(
                rgb_array,
                self.encode,
                self.decode,
                self.residual,
                alpha,
                self.device,
                self.dtype,
            )
            result = Image.fromarray(restored, mode="RGB").convert("RGBA")
            result.putalpha(alpha_channel)
            output = io.BytesIO()
            result.save(output, format="PNG", optimize=False)
            return output.getvalue()
        finally:
            if OFFLOAD_AFTER_REQUEST:
                self.vae.to("cpu")
                self.residual.to("cpu")
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()


runtime: CleanerRuntime | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global runtime
    runtime = CleanerRuntime()
    yield
    runtime = None


app = FastAPI(
    title="GPT Image 2 Artifact Cleaner Service",
    version="0.1.0",
    lifespan=lifespan,
)


def get_runtime() -> CleanerRuntime:
    if runtime is None:
        raise HTTPException(status_code=503, detail="Cleaner is still loading")
    return runtime


@app.get("/health")
async def health() -> dict[str, Any]:
    current = get_runtime()
    return {
        "status": "ok",
        "device": current.device,
        "offload_after_request": OFFLOAD_AFTER_REQUEST,
    }


@app.get("/v1/info")
async def info() -> dict[str, Any]:
    current = get_runtime()
    return {
        "device": current.device,
        "dtype": str(current.dtype),
        "vae": VAE_ID,
        "metadata": current.metadata,
        "max_image_pixels": MAX_IMAGE_PIXELS,
        "license": "PolyForm-Noncommercial-1.0.0",
    }


@app.post("/v1/clean")
async def clean(
    file: Annotated[UploadFile, File(...)],
    alpha: Annotated[float, Form()] = 0.5,
) -> Response:
    if not 0 <= alpha <= 1.5:
        raise HTTPException(status_code=422, detail="alpha must be between 0 and 1.5")
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    current = get_runtime()
    async with current.lock:
        result = await asyncio.to_thread(current.process, payload, alpha)
    return Response(
        content=result,
        media_type="image/png",
        headers={"X-Cleaner-Alpha": f"{alpha:.2f}"},
    )
