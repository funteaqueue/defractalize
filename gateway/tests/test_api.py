import base64
import time

from app import main
from fastapi.testclient import TestClient


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


def test_combined_pipeline_runs_in_order_and_serves_result(tmp_path, monkeypatch) -> None:
    events: list[str] = []

    class FakeCleaner:
        async def health(self):
            return {"status": "ok"}

        async def clean(self, image, filename, alpha):
            events.append(f"clean:{filename}:{alpha}")
            return b"cleaned"

    class FakeSeedVR:
        async def health(self):
            return {"status": "ok"}

        async def upscale(self, image, job_id, scale, seed, color_correction):
            assert image == b"cleaned"
            events.append(f"seed:{scale}:{seed}:{color_correction}")
            return PNG_1X1

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    with TestClient(main.app) as client:
        client.app.state.runner.cleaner = FakeCleaner()
        client.app.state.runner.seedvr = FakeSeedVR()
        response = client.post(
            "/api/jobs",
            files={"file": ("source.png", b"input-image", "image/png")},
            data={
                "pipeline": "cleaner_seedvr2",
                "alpha": "0.5",
                "scale": "2",
                "color_correction": "wavelet",
                "seed": "42",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            record = client.get(f"/api/jobs/{job_id}").json()
            if record["status"] == "completed":
                break
            time.sleep(0.01)

        assert record["status"] == "completed"
        assert events == ["clean:source.png:0.5", "seed:2.0:42:wavelet"]
        result = client.get(f"/api/jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.content == PNG_1X1

        preview = client.get(f"/api/jobs/{job_id}/preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("image/webp")
        assert preview.content.startswith(b"RIFF")
