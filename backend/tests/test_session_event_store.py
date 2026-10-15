"""Offline persistence regressions; BSON round trips model MongoDB precision.

The doubles enforce MongoDB's document limit, so a large event cannot silently
pass a test by remaining in the session/event document.
"""
from copy import deepcopy
import asyncio
from datetime import datetime
from types import SimpleNamespace

from bson import BSON, ObjectId
import pytest
from pymongo.errors import DocumentTooLarge, DuplicateKeyError

from app.domain.models.event import MessageEvent
from app.domain.models.session import Session
from app.infrastructure.repositories import mongo_session_repository as repository_module
from app.infrastructure.storage.session_event_store import MongoSessionEventStore, _digest


def matches(document, query):
    for key, value in query.items():
        if key == "$expr":
            if len(document.get("events", [])) != value["$eq"][1]:
                return False
            continue
        current = document
        for part in key.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        if current != value:
            return False
    return True


def bson_copy(document):
    data = BSON.encode(document)
    if len(data) >= 16 * 1024 * 1024:
        raise DocumentTooLarge("BSON document exceeds 16 MiB")
    return BSON(data).decode()


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda doc: doc.get(key, 0), reverse=direction == -1)
        return self

    def __aiter__(self):
        self.iterator = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self.iterator))
        except StopIteration:
            raise StopAsyncIteration


class Collection:
    def __init__(self, database, name):
        self.database = database
        self.name = name
        self.docs = {}
        self.indexes = []
        self.fail_insert = False
        self.fail_clear = False
        self.queries = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def find(self, query=None, projection=None):
        self.queries.append(query or {})
        return Cursor([deepcopy(doc) for doc in self.docs.values() if matches(doc, query or {})])

    async def find_one(self, query, projection=None):
        self.queries.append(query)
        return next((deepcopy(doc) for doc in self.docs.values() if matches(doc, query)), None)

    async def insert_one(self, document):
        if self.fail_insert:
            raise RuntimeError("injected insert failure")
        document = bson_copy({"_id": ObjectId(), **document})
        if document["_id"] in self.docs:
            raise DuplicateKeyError("duplicate id")
        if self.name == "session_events" and any(
            item["session_id"] == document["session_id"] and item["sequence"] == document["sequence"]
            for item in self.docs.values()
        ):
            raise DuplicateKeyError("duplicate sequence")
        self.docs[document["_id"]] = document

    async def update_one(self, query, update, upsert=False):
        document = await self.find_one(query)
        inserted = document is None
        if inserted and not upsert:
            return SimpleNamespace(matched_count=0)
        if inserted:
            document = {"_id": ObjectId(), **query, **update.get("$setOnInsert", {})}
        if self.fail_clear and update.get("$set", {}).get("events") == []:
            raise RuntimeError("injected clear failure")
        document.update(update.get("$set", {}))
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        for key, value in update.get("$max", {}).items():
            document[key] = max(value, document.get(key, value))
        self.docs[document["_id"]] = bson_copy(document)
        return SimpleNamespace(matched_count=0 if inserted else 1)

    async def find_one_and_update(self, query, update, **kwargs):
        result = await self.update_one(query, update)
        return await self.find_one(query) if result.matched_count else None

    async def delete_many(self, query):
        self.docs = {key: doc for key, doc in self.docs.items() if not matches(doc, query)}


class Database(dict):
    def __missing__(self, name):
        self[name] = Collection(self, name)
        return self[name]


class Bucket:
    def __init__(self, database):
        self.files = database["session_event_blobs.files"]
        self.data = {}

    async def upload_from_stream(self, filename, stream, metadata):
        key = ObjectId()
        self.data[key] = stream.read()
        await self.files.insert_one({"_id": key, "metadata": metadata})
        return key

    async def download_to_stream(self, key, stream):
        stream.write(self.data[key])

    async def delete(self, key):
        self.data.pop(key, None)
        await self.files.delete_many({"_id": key})


@pytest.fixture
def storage():
    database = Database()
    bucket = Bucket(database)
    store = MongoSessionEventStore(database, bucket=bucket)
    return database, bucket, store


async def create_session(database, events=(), session_id="s1"):
    document = {
        "session_id": session_id, "user_id": "owner", "agent_id": "agent",
        "status": "pending", "events": list(events), "updated_at": datetime(2026, 10, 11),
    }
    await database["sessions"].insert_one(document)


def message(event_id, text="hello"):
    return MessageEvent(id=event_id, message=text).model_dump()


def shell(event_id, console):
    return {
        "id": event_id, "type": "tool", "tool_name": "shell", "status": "called",
        "timestamp": datetime(2026, 10, 11), "function_name": "shell_exec",
        "function_args": {"id": "shell-1"}, "tool_call_id": "call-1",
        "tool_content": {"console": console}, "function_result": {"success": True},
    }


@pytest.mark.asyncio
async def test_order_uses_append_order_and_retry_is_idempotent(storage):
    db, _, store = storage
    await create_session(db)
    first, second = message("redis-9"), message("redis-1")
    await store.append("s1", first)
    await store.append("s1", second)
    await store.append("s1", first)
    result = await store.load("s1")
    assert [event["id"] for event in result] == ["redis-9", "redis-1"]
    assert len(db["session_events"].docs) == 2
    with pytest.raises(ValueError, match="Conflicting"):
        await store.append("s1", {**first, "message": "different"})


@pytest.mark.asyncio
async def test_large_event_round_trips_above_mongo_limit(storage):
    db, bucket, store = storage
    await create_session(db)
    event = message("large", "x" * (17 * 1024 * 1024))
    await store.append("s1", event)
    restored = await store.load("s1")
    assert _digest(restored[0]) == _digest(event)
    assert len(bucket.data) == 1
    assert len(BSON.encode(next(iter(db["session_events"].docs.values())))) < 1024
    assert (await db["sessions"].find_one({"session_id": "s1"}))["events"] == []


@pytest.mark.asyncio
async def test_shell_snapshots_reuse_records_without_losing_old_versions(storage):
    db, bucket, store = storage
    await create_session(db)
    large_record = {"command": "cat data", "output": "a" * 400_000, "ps1": "$"}
    first = shell("1", [large_record])
    second = shell("2", [large_record, {"command": "pwd", "output": "/tmp"}])
    third = shell("3", [large_record, {"command": "pwd", "output": "/tmp/new"}])
    for event in (first, second, third):
        await store.append("s1", event)
    assert len(db["session_event_payloads"].docs) == 3
    assert len(bucket.data) == 1
    assert await store.load("s1") == [first, second, third]


@pytest.mark.asyncio
async def test_legacy_migration_preserves_duplicate_ids_and_identical_events(storage):
    db, _, store = storage
    first = message("same", "first")
    legacy = [first, message("same", "second"), first]
    await create_session(db, legacy)
    assert await store.migrate_legacy_events("s1") == 3
    assert await store.migrate_legacy_events("s1") == 0
    result = await store.load("s1")
    assert [_digest(event) for event in result] == [_digest(event) for event in legacy]
    await store.append("s1", message("new"))
    assert [item["sequence"] for item in db["session_events"].docs.values()] == [1, 2, 3, 4]
    assert (await db["sessions"].find_one({"session_id": "s1"}))["events"] == []


@pytest.mark.asyncio
async def test_partial_migration_keeps_source_and_can_resume(storage):
    db, _, store = storage
    legacy = [message("a"), message("b")]
    await create_session(db, legacy)
    db["sessions"].fail_clear = True
    with pytest.raises(RuntimeError, match="clear failure"):
        await store.migrate_legacy_events("s1")
    original = (await db["sessions"].find_one({"session_id": "s1"}))["events"]
    assert [_digest(event) for event in original] == [_digest(event) for event in legacy]
    assert await store.load("s1", original) == original
    db["sessions"].fail_clear = False
    assert await store.migrate_legacy_events("s1") == 2
    assert len(db["session_events"].docs) == 2


@pytest.mark.asyncio
async def test_failed_event_copy_never_clears_legacy(storage):
    db, _, store = storage
    await create_session(db, [message("a")])
    db["session_events"].fail_insert = True
    with pytest.raises(RuntimeError, match="insert failure"):
        await store.migrate_legacy_events("s1")
    assert len((await db["sessions"].find_one({"session_id": "s1"}))["events"]) == 1


@pytest.mark.asyncio
async def test_migration_refuses_to_clear_a_concurrently_changed_snapshot(storage, monkeypatch):
    db, _, store = storage
    await create_session(db, [message("a")])
    original_update = db["sessions"].update_one

    async def mutate_before_clear(query, update, **kwargs):
        if update.get("$set", {}).get("events") == []:
            document = next(iter(db["sessions"].docs.values()))
            document["updated_at"] = datetime(2026, 10, 12)
        return await original_update(query, update, **kwargs)

    monkeypatch.setattr(db["sessions"], "update_one", mutate_before_clear)
    with pytest.raises(RuntimeError, match="changed during history migration"):
        await store.migrate_legacy_events("s1")
    assert len((await db["sessions"].find_one({"session_id": "s1"}))["events"]) == 1


@pytest.mark.asyncio
async def test_migration_detects_same_length_edit_without_timestamp_change(storage, monkeypatch):
    db, _, store = storage
    await create_session(db, [message("a", "original")])
    original_update = db["sessions"].update_one

    async def edit_content_before_clear(query, update, **kwargs):
        if update.get("$set", {}).get("events") == []:
            document = next(iter(db["sessions"].docs.values()))
            document["events"][0]["message"] = "concurrent edit"
        return await original_update(query, update, **kwargs)

    monkeypatch.setattr(db["sessions"], "update_one", edit_content_before_clear)
    with pytest.raises(RuntimeError, match="changed during history migration"):
        await store.migrate_legacy_events("s1")
    original = (await db["sessions"].find_one({"session_id": "s1"}))["events"]
    assert original[0]["message"] == "concurrent edit"


@pytest.mark.asyncio
async def test_concurrent_retries_keep_one_event_and_one_complete_blob(storage, monkeypatch):
    db, bucket, store = storage
    await create_session(db)
    rendezvous = asyncio.Event()
    waiting = 0
    original_upload = bucket.upload_from_stream

    async def concurrent_upload(*args, **kwargs):
        nonlocal waiting
        waiting += 1
        if waiting == 2:
            rendezvous.set()
        await rendezvous.wait()
        return await original_upload(*args, **kwargs)

    monkeypatch.setattr(bucket, "upload_from_stream", concurrent_upload)
    event = message("same", "x" * 300_000)
    await asyncio.wait_for(asyncio.gather(
        store.append("s1", event), store.append("s1", event),
    ), timeout=5)
    assert len(db["session_events"].docs) == 1
    assert len(db["session_event_payloads"].docs) == 1
    assert len(bucket.data) == 1
    assert _digest((await store.load("s1"))[0]) == _digest(event)


@pytest.mark.asyncio
async def test_deletion_removes_owned_payloads_but_preserves_other_session(storage):
    db, bucket, store = storage
    for session_id in ("s1", "s2"):
        await create_session(db, session_id=session_id)
        await store.append(session_id, message("large", "x" * 300_000))
    assert len(bucket.data) == 2
    await store.delete("s1")
    assert len(bucket.data) == 1
    assert await store.load("s1") == []
    assert len(await store.load("s2")) == 1
    assert all(item["session_id"] == "s2" for item in db["session_event_payloads"].docs.values())


@pytest.mark.asyncio
async def test_read_detects_payload_corruption(storage):
    db, _, store = storage
    await create_session(db)
    await store.append("s1", message("a"))
    row = next(iter(db["session_events"].docs.values()))
    row["payload"]["event"]["message"] = "corrupted"
    with pytest.raises(RuntimeError, match="integrity"):
        await store.load("s1")


@pytest.fixture
def repository(storage, monkeypatch):
    db, _, store = storage

    class FakeDocument:
        @staticmethod
        def get_pymongo_collection():
            return db["sessions"]

        @staticmethod
        def model_validate(document):
            domain = Session.model_validate({**document, "id": document["session_id"]})
            return SimpleNamespace(to_domain=lambda: domain)

    monkeypatch.setattr(repository_module, "SessionDocument", FakeDocument)
    return repository_module.MongoSessionRepository(event_store=store)


@pytest.mark.asyncio
async def test_history_authorization_precedes_loading_payloads(storage, repository):
    db, _, store = storage
    await create_session(db)
    await store.append("s1", message("a"))
    db["session_events"].queries.clear()
    assert await repository.find_history_by_id_and_user_id("s1", "intruder") is None
    assert db["session_events"].queries == []
    authorized = await repository.find_history_by_id_and_user_id("s1", "owner")
    assert authorized.events[0].message == "hello"
    assert all(query.get("session_id") == "s1" for query in db["session_events"].queries)


@pytest.mark.asyncio
async def test_save_never_reembeds_hydrated_events_or_resets_sequence(storage, repository):
    db, _, store = storage
    legacy = [message("a"), message("a", "second occurrence")]
    await create_session(db, legacy)
    loaded = await repository.find_history_by_id_and_user_id("s1", "owner")
    loaded.title = "updated"
    await repository.save(loaded)
    loaded.events[0].message = "a stale or locally normalized history snapshot"
    await repository.save(loaded)
    saved = await db["sessions"].find_one({"session_id": "s1"})
    assert saved["events"] == []
    assert saved["title"] == "updated"
    assert saved["event_sequence"] == 2
    assert len(await store.load("s1")) == 2
    assert (await store.load("s1"))[0]["message"] == "hello"
    await repository.add_event("s1", MessageEvent(id="third", message="new"))
    assert len(await store.load("s1")) == 3


@pytest.mark.asyncio
async def test_new_save_externalizes_prepopulated_events(storage, repository):
    db, _, store = storage
    session = Session(id="s1", user_id="owner", agent_id="agent", events=[MessageEvent(message="hi")])
    await repository.save(session)
    assert (await db["sessions"].find_one({"session_id": "s1"}))["events"] == []
    assert (await store.load("s1"))[0]["message"] == "hi"


async def test_history_projection_keeps_favorite_and_project(storage, repository, monkeypatch):
    db, _, _ = storage
    session = Session(id="project-history", user_id="owner", agent_id="agent",
                      is_favorite=True, project_id="project")
    await repository.save(session)
    collection = db["sessions"]
    original_find = collection.find_one

    async def projected_find(query, projection=None):
        doc = await original_find(query, projection)
        if doc and projection:
            return {key: value for key, value in doc.items() if projection.get(key)}
        return doc

    monkeypatch.setattr(collection, "find_one", projected_find)
    history = await repository.find_history_by_id_and_user_id(session.id, "owner")
    assert history.is_favorite
    assert history.project_id == "project"
