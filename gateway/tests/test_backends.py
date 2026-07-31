import httpx
import pytest

from app.backends import SeedVRClient, build_seedvr_prompt, seedvr_gpu_memory_released


def test_seedvr_prompt_uses_distilled_one_step_recipe() -> None:
    prompt = build_seedvr_prompt(
        input_name="job.png",
        output_prefix="api/job",
        scale=2.0,
        seed=42,
        color_correction="wavelet",
    )

    assert prompt["3"]["inputs"]["scale_by"] == 2.0
    assert prompt["6"]["inputs"]["tile_size"] == 512
    assert prompt["7"]["inputs"]["unet_name"].startswith("seedvr2_distill_6L_1.4B")
    assert prompt["9"]["inputs"] == {
        "model": ["7", 0],
        "seed": 42,
        "steps": 1,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "positive": ["8", 0],
        "negative": ["8", 1],
        "latent_image": ["6", 0],
        "denoise": 1.0,
    }
    assert prompt["11"]["inputs"]["color_correction_method"] == "wavelet"
    assert prompt["12"]["inputs"]["filename_prefix"] == "api/job"


def test_seedvr_gpu_memory_release_detection() -> None:
    assert seedvr_gpu_memory_released(
        {"devices": [{"torch_vram_total": 0}]}
    )
    assert not seedvr_gpu_memory_released(
        {"devices": [{"torch_vram_total": 512 * 1024 * 1024}]}
    )
    assert not seedvr_gpu_memory_released({"devices": []})


@pytest.mark.asyncio
async def test_seedvr_release_waits_for_confirmed_vram_cleanup() -> None:
    stats_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stats_reads
        if request.url.path == "/free":
            return httpx.Response(200)
        if request.url.path == "/system_stats":
            stats_reads += 1
            reserved = 512 * 1024 * 1024 if stats_reads == 1 else 0
            return httpx.Response(
                200,
                json={"devices": [{"torch_vram_total": reserved}]},
            )
        return httpx.Response(404)

    seedvr = SeedVRClient(
        "http://seedvr",
        timeout_seconds=10,
        release_timeout_seconds=1,
        release_poll_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await seedvr.release_gpu(client)

    assert stats_reads == 2
