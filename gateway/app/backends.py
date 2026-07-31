from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx


LOGGER = logging.getLogger(__name__)
RELEASED_TORCH_VRAM_BYTES = 64 * 1024 * 1024


def seedvr_gpu_memory_released(system_stats: dict[str, Any]) -> bool:
    devices = system_stats.get("devices")
    if not isinstance(devices, list) or not devices:
        return False
    reserved = [
        device.get("torch_vram_total")
        for device in devices
        if isinstance(device, dict) and isinstance(device.get("torch_vram_total"), int)
    ]
    return bool(reserved) and all(value <= RELEASED_TORCH_VRAM_BYTES for value in reserved)


class BackendError(RuntimeError):
    pass


def build_seedvr_prompt(
    input_name: str,
    output_prefix: str,
    scale: float,
    seed: int,
    color_correction: str,
) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": input_name},
        },
        "2": {
            "class_type": "JoinImageWithAlpha",
            "inputs": {"image": ["1", 0], "alpha": ["1", 1]},
        },
        "3": {
            "class_type": "ImageScaleBy",
            "inputs": {
                "image": ["2", 0],
                "upscale_method": "lanczos",
                "scale_by": scale,
            },
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "seedvr2_ema_vae_fp16.safetensors"},
        },
        "5": {
            "class_type": "SeedVR2Preprocess",
            "inputs": {"resized_images": ["3", 0]},
        },
        "6": {
            "class_type": "VAEEncodeTiled",
            "inputs": {
                "pixels": ["5", 0],
                "vae": ["4", 0],
                "tile_size": 512,
                "overlap": 128,
                "temporal_size": 4096,
                "temporal_overlap": 8,
            },
        },
        "7": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "seedvr2_distill_6L_1.4B_sharp_fp16_comfyui.safetensors",
                "weight_dtype": "default",
            },
        },
        "8": {
            "class_type": "SeedVR2Conditioning",
            "inputs": {"model": ["7", 0], "vae_conditioning": ["6", 0]},
        },
        "9": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["7", 0],
                "seed": seed,
                "steps": 1,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["8", 0],
                "negative": ["8", 1],
                "latent_image": ["6", 0],
                "denoise": 1.0,
            },
        },
        "10": {
            "class_type": "VAEDecodeTiled",
            "inputs": {
                "samples": ["9", 0],
                "vae": ["4", 0],
                "tile_size": 512,
                "overlap": 128,
                "temporal_size": 4096,
                "temporal_overlap": 8,
            },
        },
        "11": {
            "class_type": "SeedVR2PostProcessing",
            "inputs": {
                "images": ["10", 0],
                "original_resized_images": ["3", 0],
                "color_correction_method": color_correction,
            },
        },
        "12": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["11", 0],
                "filename_prefix": output_prefix,
            },
        },
    }


class CleanerClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    async def clean(self, image: bytes, filename: str, alpha: float) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/clean",
                files={"file": (filename, image, "application/octet-stream")},
                data={"alpha": str(alpha)},
            )
            if response.is_error:
                raise BackendError(
                    f"Cleaner failed ({response.status_code}): {response.text[:500]}"
                )
            return response.content


class SeedVRClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        release_timeout_seconds: float = 30,
        release_poll_seconds: float = 0.25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.release_timeout_seconds = max(0, release_timeout_seconds)
        self.release_poll_seconds = max(0, release_poll_seconds)

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base_url}/system_stats")
            response.raise_for_status()
            return response.json()

    async def release_gpu(self, client: httpx.AsyncClient) -> bool:
        request_timeout = max(1, min(5, self.release_timeout_seconds or 1))
        try:
            response = await client.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=request_timeout,
            )
            response.raise_for_status()
        except Exception as error:  # noqa: BLE001 - cleanup must not hide a valid result
            LOGGER.warning("Could not request SeedVR GPU release: %s", error)
            return False

        if self.release_timeout_seconds == 0:
            return True

        deadline = time.monotonic() + self.release_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(self.release_poll_seconds)
            try:
                stats_response = await client.get(
                    f"{self.base_url}/system_stats",
                    timeout=request_timeout,
                )
                stats_response.raise_for_status()
                if seedvr_gpu_memory_released(stats_response.json()):
                    return True
            except Exception as error:  # noqa: BLE001 - retry confirmation until deadline
                last_error = error

        if last_error:
            LOGGER.warning(
                "SeedVR GPU release was requested but could not be confirmed: %s",
                last_error,
            )
        else:
            LOGGER.warning(
                "SeedVR GPU release was not confirmed within %.1f seconds",
                self.release_timeout_seconds,
            )
        return False

    async def upscale(
        self,
        image: bytes,
        job_id: str,
        scale: float,
        seed: int,
        color_correction: str,
    ) -> bytes:
        upload_name = f"{job_id}.png"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds)
        ) as client:
            try:
                upload = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (upload_name, image, "image/png")},
                    data={"type": "input", "overwrite": "true"},
                )
                if upload.is_error:
                    raise BackendError(
                        f"SeedVR upload failed ({upload.status_code}): {upload.text[:500]}"
                    )
                uploaded = upload.json()
                input_name = (
                    f"{uploaded.get('subfolder')}/{uploaded['name']}"
                    if uploaded.get("subfolder")
                    else uploaded["name"]
                )

                prompt = build_seedvr_prompt(
                    input_name=input_name,
                    output_prefix=f"api/{job_id}",
                    scale=scale,
                    seed=seed,
                    color_correction=color_correction,
                )
                queued = await client.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": prompt},
                )
                if queued.is_error:
                    raise BackendError(
                        f"SeedVR prompt rejected ({queued.status_code}): {queued.text[:1000]}"
                    )
                queued_data = queued.json()
                prompt_id = queued_data.get("prompt_id")
                if not prompt_id:
                    raise BackendError(f"SeedVR returned no prompt id: {queued_data}")

                deadline = time.monotonic() + self.timeout_seconds
                output_info: dict[str, Any] | None = None
                while time.monotonic() < deadline:
                    history_response = await client.get(
                        f"{self.base_url}/history/{prompt_id}"
                    )
                    history_response.raise_for_status()
                    history = history_response.json().get(prompt_id)
                    if history:
                        status = history.get("status", {})
                        if not status.get("completed"):
                            messages = status.get("messages", [])
                            raise BackendError(
                                f"SeedVR execution failed: {messages[-1] if messages else status}"
                            )
                        images = history.get("outputs", {}).get("12", {}).get("images", [])
                        if not images:
                            raise BackendError("SeedVR completed without a SaveImage output")
                        output_info = images[0]
                        break
                    await asyncio.sleep(0.5)

                if output_info is None:
                    raise BackendError(
                        f"SeedVR timed out after {self.timeout_seconds:.0f} seconds"
                    )

                result = await client.get(
                    f"{self.base_url}/view",
                    params={
                        "filename": output_info["filename"],
                        "subfolder": output_info.get("subfolder", ""),
                        "type": output_info.get("type", "output"),
                    },
                )
                result.raise_for_status()
                return result.content
            finally:
                await self.release_gpu(client)
