import asyncio

import pytest
from app.jobs import JobStore


def test_store_persists_job_records(tmp_path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path)
        created = await store.create(
            "abcdef123456",
            "source.png",
            {"pipeline": "cleaner", "alpha": 0.5},
        )
        assert created["status"] == "queued"

        updated = await store.update(
            created["id"],
            status="completed",
            progress=100,
            result_available=True,
        )
        loaded = await store.get(created["id"])
        listed = await store.list()

        assert loaded == updated
        assert listed == [updated]

    asyncio.run(scenario())


def test_store_rejects_unsafe_job_id(tmp_path) -> None:
    store = JobStore(tmp_path)
    with pytest.raises(ValueError):
        store.directory("../outside")
