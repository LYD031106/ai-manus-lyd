"""Exercise history storage against a disposable database, never production data.

Run inside the candidate backend image with its normal MongoDB connection.
Only the unique database created by this process is removed in the finally block.
"""
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beanie import init_beanie
from pymongo import AsyncMongoClient
from app.core.config import get_settings
from app.domain.models.event import MessageEvent
from app.domain.models.session import Session
from app.infrastructure.models.documents import SessionDocument
from app.infrastructure.repositories.mongo_session_repository import MongoSessionRepository
from app.infrastructure.storage.session_event_store import MongoSessionEventStore, _digest


def report(case, **data):
    print(json.dumps({"case": case, **data}, default=str), flush=True)


async def main():
    settings = get_settings()
    options = {"serverSelectionTimeoutMS": 10000}
    if settings.mongodb_username:
        options.update(username=settings.mongodb_username, password=settings.mongodb_password)
    client = AsyncMongoClient(settings.mongodb_uri, **options)
    name = "manus_history_verify_" + uuid.uuid4().hex
    assert name != settings.mongodb_database and name.startswith("manus_history_verify_")
    db = client[name]
    store = MongoSessionEventStore(db)
    try:
        await client.admin.command("ping")
        await store.ensure_indexes()
        stamp = datetime(2026, 10, 11, tzinfo=UTC)
        legacy = [MessageEvent(id="reused", message=text, timestamp=stamp).model_dump()
                  for text in ["first", "second", "first"]]
        await db.sessions.insert_one({"session_id": "legacy", "events": legacy, "updated_at": stamp})
        assert await store.migrate_legacy_events("legacy") == 3
        assert _digest(await store.load("legacy")) == _digest(legacy)
        assert await store.migrate_legacy_events("legacy") == 0
        report("legacy_duplicate_ids_preserved", events=3)

        large = MessageEvent(id="large", message="x" * (17 * 1024 * 1024), timestamp=stamp).model_dump()
        await store.append("legacy", large)
        restored = await store.load("legacy")
        assert len(restored) == 4 and _digest(restored[-1]) == _digest(large)
        assert await db[store.BUCKET_NAME + ".files"].count_documents({}) >= 1
        await store.append("legacy", large)
        assert await db.session_events.count_documents({"session_id": "legacy"}) == 4
        report("17_mib_event_gridfs_roundtrip_and_idempotency", content_bytes=len(large["message"]))
        del large, restored

        await db.sessions.insert_one({"session_id": "shell", "events": []})
        records = [{"ps1": "$ ", "command": "step_" + str(i), "output": str(i) * 50000}
                   for i in range(3)]
        for i in range(3):
            event = {"id": "shell_" + str(i), "type": "tool", "tool_name": "shell",
                     "timestamp": stamp, "tool_content": {"console": records[:i + 1]}}
            await store.append("shell", event)
        assert await db.session_event_payloads.count_documents({"session_id": "shell"}) == 3
        shell_events = await store.load("shell")
        assert shell_events[-1]["tool_content"]["console"] == records
        report("shell_snapshot_records_deduplicated", events=3, unique_records=3)

        await init_beanie(database=db, document_models=[SessionDocument])
        repo = MongoSessionRepository(store)
        session = Session(id="repository", agent_id="smoke", user_id="owner")
        await repo.save(session)
        await repo.add_event(session.id, MessageEvent(id="event_1", message="hello", timestamp=stamp))
        loaded = await repo.find_history_by_id_and_user_id(session.id, "owner")
        assert loaded is not None and len(loaded.events) == 1 and loaded.events[0].message == "hello"
        assert await repo.find_history_by_id_and_user_id(session.id, "another_user") is None
        loaded.task_id = "new_task"
        await repo.save(loaded)
        persisted = await db.sessions.find_one({"session_id": session.id})
        assert persisted["events"] == [] and persisted["task_id"] == "new_task"
        assert len(await store.load(session.id)) == 1
        report("repository_history_authorization_and_metadata_save", events=1)

        await store.delete("shell")
        assert await db.session_events.count_documents({"session_id": "shell"}) == 0
        assert await db.session_event_payloads.count_documents({"session_id": "shell"}) == 0
        assert len(await store.load("legacy")) == 4
        report("session_delete_isolation", preserved_events=4)
        report("PASS", database=name, production_database_modified=False)
    finally:
        await client.drop_database(name)
        report("cleanup", temporary_database_removed=name)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
