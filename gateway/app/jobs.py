from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def directory(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise ValueError("Invalid job id")
        return self.root / job_id

    def record_path(self, job_id: str) -> Path:
        return self.directory(job_id) / "job.json"

    async def create(
        self,
        job_id: str,
        original_filename: str,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "id": job_id,
            "status": "queued",
            "stage": "Waiting for GPU worker",
            "progress": 0,
            "original_filename": original_filename,
            "options": options,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "error": None,
            "result_available": False,
        }
        self.directory(job_id).mkdir(parents=True, exist_ok=False)
        await self.write(record)
        return record

    async def write(self, record: dict[str, Any]) -> None:
        record["updated_at"] = utc_now()
        path = self.record_path(record["id"])
        temporary = path.with_suffix(".tmp")
        async with self._lock:
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)

    async def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        record = await self.get(job_id)
        record.update(changes)
        await self.write(record)
        return record

    async def get(self, job_id: str) -> dict[str, Any]:
        path = self.record_path(job_id)
        if not path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        paths = sorted(
            self.root.glob("*/job.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in paths[:limit]
        ]
