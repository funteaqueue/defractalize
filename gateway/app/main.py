from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import os
import secrets
from io import BytesIO
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from .backends import CleanerClient, SeedVRClient
from .jobs import JobStore

DATA_DIR = Path(os.getenv("DATA_DIR", "/data/jobs"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
BACKEND_TIMEOUT_SECONDS = float(os.getenv("BACKEND_TIMEOUT_SECONDS", "900"))
GPU_RELEASE_TIMEOUT_SECONDS = float(os.getenv("GPU_RELEASE_TIMEOUT_SECONDS", "30"))
CLEANER_URL = os.getenv("CLEANER_URL", "http://cleaner:8001")
SEEDVR_URL = os.getenv("SEEDVR_URL", "http://seedvr:8188")
STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_PIPELINES = {"cleaner", "seedvr2", "cleaner_seedvr2"}
ALLOWED_COLOR_CORRECTIONS = {"none", "wavelet", "lab", "adain"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PREVIEW_SIZE = (480, 320)


def create_result_preview(source: Path, destination: Path) -> None:
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(4)}.tmp"
    )
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = BytesIO()
            image.save(output, format="WEBP", quality=82, method=4)
        temporary.write_bytes(output.getvalue())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


class PipelineRunner:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.cleaner = CleanerClient(CLEANER_URL, BACKEND_TIMEOUT_SECONDS)
        self.seedvr = SeedVRClient(
            SEEDVR_URL,
            BACKEND_TIMEOUT_SECONDS,
            release_timeout_seconds=GPU_RELEASE_TIMEOUT_SECONDS,
        )

    async def run(self, job_id: str) -> None:
        record = await self.store.get(job_id)
        options = record["options"]
        job_dir = self.store.directory(job_id)
        image = (job_dir / "input.bin").read_bytes()
        filename = record["original_filename"]

        try:
            await self.store.update(
                job_id,
                status="running",
                stage="Preparing image",
                progress=5,
            )

            if options["pipeline"] in {"cleaner", "cleaner_seedvr2"}:
                await self.store.update(
                    job_id,
                    stage=f"Cleaning GPT Image 2 artifacts (alpha {options['alpha']:.2f})",
                    progress=20,
                )
                image = await self.cleaner.clean(
                    image=image,
                    filename=filename,
                    alpha=options["alpha"],
                )
                (job_dir / "cleaned.png").write_bytes(image)

            if options["pipeline"] in {"seedvr2", "cleaner_seedvr2"}:
                await self.store.update(
                    job_id,
                    stage=(
                        f"Restoring details with SeedVR2 "
                        f"({options['scale']:g}x, {options['color_correction']})"
                    ),
                    progress=55,
                )
                image = await self.seedvr.upscale(
                    image=image,
                    job_id=job_id,
                    scale=options["scale"],
                    seed=options["seed"],
                    color_correction=options["color_correction"],
                )

            (job_dir / "result.png").write_bytes(image)
            await self.store.update(
                job_id,
                status="completed",
                stage="Completed",
                progress=100,
                result_available=True,
            )
        except Exception as error:  # noqa: BLE001 - job failures must be persisted
            await self.store.update(
                job_id,
                status="failed",
                stage="Failed",
                error=str(error),
            )


async def worker(app: FastAPI) -> None:
    queue: asyncio.Queue[str] = app.state.queue
    while True:
        job_id = await queue.get()
        try:
            await app.state.runner.run(job_id)
        finally:
            queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = JobStore(DATA_DIR)
    queue: asyncio.Queue[str] = asyncio.Queue()
    app.state.store = store
    app.state.queue = queue
    app.state.runner = PipelineRunner(store)

    for record in await store.list(limit=1000):
        if record["status"] in {"queued", "running"}:
            await store.update(
                record["id"],
                status="queued",
                stage="Recovered after gateway restart",
                progress=0,
                error=None,
            )
            await queue.put(record["id"])

    task = asyncio.create_task(worker(app))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="defractalize",
    version="0.1.0",
    description="Sequential API for GPT Image 2 artifact cleanup and SeedVR2 detail restoration.",
    lifespan=lifespan,
)


def store_from(request: Request) -> JobStore:
    return request.app.state.store


async def save_upload(file: UploadFile, destination: Path) -> int:
    total = 0
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
                )
            output.write(chunk)
    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    return total


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    runner: PipelineRunner = request.app.state.runner

    async def check(name: str, call: Any) -> tuple[str, dict[str, Any]]:
        try:
            await call()
            return name, {"status": "ok"}
        except Exception as error:  # noqa: BLE001 - health reports all backend failures
            return name, {"status": "unavailable", "error": str(error)}

    results = await asyncio.gather(
        check("cleaner", runner.cleaner.health),
        check("seedvr2", runner.seedvr.health),
    )
    return {
        "status": "ok",
        "queue_size": request.app.state.queue.qsize(),
        "backends": dict(results),
    }


@app.post("/api/jobs", status_code=202)
async def create_job(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    pipeline: Annotated[str, Form()] = "cleaner_seedvr2",
    alpha: Annotated[float, Form()] = 0.5,
    scale: Annotated[float, Form()] = 2.0,
    color_correction: Annotated[str, Form()] = "wavelet",
    seed: Annotated[int, Form()] = 42,
) -> dict[str, Any]:
    if pipeline not in ALLOWED_PIPELINES:
        raise HTTPException(status_code=422, detail="Unknown pipeline")
    if not 0 <= alpha <= 1.5:
        raise HTTPException(status_code=422, detail="alpha must be between 0 and 1.5")
    if not 1 <= scale <= 4:
        raise HTTPException(status_code=422, detail="scale must be between 1 and 4")
    if color_correction not in ALLOWED_COLOR_CORRECTIONS:
        raise HTTPException(status_code=422, detail="Unknown color correction method")
    if not 0 <= seed <= 2**63 - 1:
        raise HTTPException(status_code=422, detail="seed is outside the supported range")

    original_filename = Path(file.filename or "upload.png").name
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported image extension")

    runner: PipelineRunner = request.app.state.runner
    try:
        if pipeline in {"cleaner", "cleaner_seedvr2"}:
            await runner.cleaner.health()
        if pipeline in {"seedvr2", "cleaner_seedvr2"}:
            await runner.seedvr.health()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"A required GPU service is not ready: {error}",
        ) from error

    job_id = secrets.token_hex(12)
    store = store_from(request)
    options = {
        "pipeline": pipeline,
        "alpha": alpha,
        "scale": scale,
        "color_correction": color_correction,
        "seed": seed,
    }
    record = await store.create(job_id, original_filename, options)
    try:
        size = await save_upload(file, store.directory(job_id) / "input.bin")
        record = await store.update(job_id, upload_bytes=size)
    except Exception:
        with contextlib.suppress(Exception):
            for path in store.directory(job_id).iterdir():
                path.unlink()
            store.directory(job_id).rmdir()
        raise

    await request.app.state.queue.put(job_id)
    return record


@app.get("/api/jobs")
async def list_jobs(request: Request, limit: int = 20) -> list[dict[str, Any]]:
    return await store_from(request).list(limit=max(1, min(limit, 5000)))


@app.get("/api/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict[str, Any]:
    try:
        return await store_from(request).get(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Job not found") from None


@app.get("/api/jobs/{job_id}/input")
async def get_input(request: Request, job_id: str) -> FileResponse:
    try:
        record = await store_from(request).get(job_id)
        path = store_from(request).directory(job_id) / "input.bin"
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Job not found") from None
    media_type = (
        mimetypes.guess_type(record["original_filename"])[0]
        or "application/octet-stream"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=record["original_filename"],
    )


@app.get("/api/jobs/{job_id}/result")
async def get_result(request: Request, job_id: str) -> FileResponse:
    try:
        store = store_from(request)
        record = await store.get(job_id)
        path = store.directory(job_id) / "result.png"
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Job not found") from None
    if record["status"] != "completed" or not path.exists():
        raise HTTPException(status_code=409, detail="Result is not ready")
    stem = Path(record["original_filename"]).stem
    return FileResponse(path, media_type="image/png", filename=f"{stem}-restored.png")


@app.get("/api/jobs/{job_id}/preview")
async def get_result_preview(request: Request, job_id: str) -> FileResponse:
    try:
        store = store_from(request)
        record = await store.get(job_id)
        job_dir = store.directory(job_id)
        result = job_dir / "result.png"
        preview = job_dir / "preview.webp"
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Job not found") from None
    if record["status"] != "completed" or not result.exists():
        raise HTTPException(status_code=409, detail="Result preview is not ready")
    if not preview.exists() or preview.stat().st_mtime_ns < result.stat().st_mtime_ns:
        await asyncio.to_thread(create_result_preview, result, preview)
    return FileResponse(
        preview,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.delete("/api/jobs/{job_id}", status_code=204, response_class=Response)
async def delete_job(request: Request, job_id: str) -> Response:
    store = store_from(request)
    try:
        record = await store.get(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Job not found") from None
    if record["status"] in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="A queued or running job cannot be deleted",
        )
    for path in store.directory(job_id).iterdir():
        path.unlink()
    store.directory(job_id).rmdir()
    return Response(status_code=204)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
