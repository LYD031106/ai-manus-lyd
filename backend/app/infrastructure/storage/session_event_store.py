"""Append-only session history with lossless, deduplicated large payloads.

Events are ordered independently of their timestamp/Redis ID. Console records
are content addressed *within a session*, so repeated shell snapshots do not
duplicate their text and deleting a session cannot remove another session's data.
"""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import io
import logging

from bson import BSON
from gridfs import AsyncGridFSBucket
from gridfs.errors import NoFile
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _encode(value) -> bytes:
    # BSON gives the same datetime precision as MongoDB's existing event format.
    return BSON.encode({"value": _canonical(value)})


def _digest(value) -> str:
    return sha256(_encode(value)).hexdigest()


class MongoSessionEventStore:
    INLINE_LIMIT = 256 * 1024
    BUCKET_NAME = "session_event_blobs"

    def __init__(self, database, bucket=None):
        self.sessions = database["sessions"]
        self.events = database["session_events"]
        self.payloads = database["session_event_payloads"]
        self.blob_files = database[f"{self.BUCKET_NAME}.files"]
        self.bucket = bucket if bucket is not None else AsyncGridFSBucket(
            database, bucket_name=self.BUCKET_NAME
        )
        self._indexes_ready = False

    async def ensure_indexes(self):
        if self._indexes_ready:
            return
        await self.events.create_index(
            [("session_id", ASCENDING), ("sequence", ASCENDING)],
            unique=True, name="session_event_order",
        )
        await self.payloads.create_index("session_id")
        await self.events.create_index([("session_id", ASCENDING), ("event_id", ASCENDING)])
        await self.blob_files.create_index("metadata.session_id")
        self._indexes_ready = True

    @staticmethod
    def event_key(session_id, event_id, legacy_index=None):
        identity = ["live", event_id] if legacy_index is None else ["legacy", legacy_index]
        return _digest([session_id, *identity])

    async def _store_payload(self, session_id, value):
        raw = _encode(value)
        key = _digest([session_id, sha256(raw).hexdigest()])
        if await self.payloads.find_one({"_id": key}, {"_id": 1}):
            return key
        document = {"_id": key, "session_id": session_id}
        blob_id = None
        if len(raw) > self.INLINE_LIMIT:
            blob_id = await self.bucket.upload_from_stream(
                key, io.BytesIO(raw), metadata={"session_id": session_id}
            )
            document["blob_id"] = blob_id
        else:
            document["value"] = value
        try:
            await self.payloads.insert_one(document)
        except DuplicateKeyError:
            # Two writers can prepare the same record concurrently. Each upload
            # uses a fresh GridFS ID; only the losing writer's upload is removed.
            if blob_id is not None:
                await self.bucket.delete(blob_id)
        return key

    async def _read_payload(self, session_id, key, cache):
        if key in cache:
            return deepcopy(cache[key])
        document = await self.payloads.find_one({"_id": key, "session_id": session_id})
        if document is None:
            raise RuntimeError(f"Missing event payload {key} for session {session_id}")
        if "blob_id" in document:
            stream = io.BytesIO()
            await self.bucket.download_to_stream(document["blob_id"], stream)
            value = BSON(stream.getvalue()).decode()["value"]
        else:
            value = document["value"]
        cache[key] = value
        return deepcopy(value)

    async def append(self, session_id, event, sequence=None, legacy_index=None):
        await self.ensure_indexes()
        event_id = event["id"]
        key = self.event_key(session_id, event_id, legacy_index)
        digest = _digest(event)
        if legacy_index is None and await self.events.find_one({
            "session_id": session_id, "event_id": event_id, "digest": digest,
        }, {"_id": 1}):
            return
        existing = await self.events.find_one({"_id": key}, {"digest": 1})
        if existing:
            if existing["digest"] != digest:
                raise ValueError(f"Conflicting event ID {event_id} for session {session_id}")
            return

        if sequence is None:
            session = await self.sessions.find_one_and_update(
                {"session_id": session_id}, {"$inc": {"event_sequence": 1}},
                projection={"event_sequence": 1}, return_document=ReturnDocument.AFTER,
            )
            if not session:
                raise ValueError(f"Session {session_id} not found")
            sequence = session["event_sequence"]

        compact = deepcopy(event)
        envelope = {"event": compact}
        tool_content = compact.get("tool_content")
        if (compact.get("type") == "tool" and compact.get("tool_name") == "shell"
                and isinstance(tool_content, dict)
                and isinstance(tool_content.get("console"), list)):
            console = tool_content.pop("console")
            envelope["console_refs"] = [
                await self._store_payload(session_id, record) for record in console
            ]
        document = {
            "_id": key, "session_id": session_id, "event_id": event_id,
            "sequence": sequence, "digest": digest,
        }
        if legacy_index is not None:
            document["legacy_index"] = legacy_index
        if len(_encode(envelope)) > self.INLINE_LIMIT:
            document["payload_ref"] = await self._store_payload(session_id, envelope)
        else:
            document["payload"] = envelope
        try:
            await self.events.insert_one(document)
        except DuplicateKeyError:
            existing = await self.events.find_one({"_id": key})
            if existing is None or existing["digest"] != digest:
                raise

    async def _decode(self, session_id, document, cache):
        envelope = deepcopy(document.get("payload"))
        if envelope is None:
            envelope = await self._read_payload(session_id, document["payload_ref"], cache)
        event = envelope["event"]
        if "console_refs" in envelope:
            event["tool_content"]["console"] = [
                await self._read_payload(session_id, key, cache)
                for key in envelope["console_refs"]
            ]
        if _digest(event) != document["digest"]:
            raise RuntimeError(f"Event integrity check failed for {document['event_id']}")
        return event

    async def load(self, session_id, legacy_events=()):
        # Keep duplicate IDs in historical arrays: the legacy array position,
        # not a possibly reused Redis cursor, is their stable storage identity.
        result = list(legacy_events)
        cache = {}
        async for document in self.events.find({"session_id": session_id}).sort("sequence", 1):
            if document.get("legacy_index", len(legacy_events)) >= len(legacy_events):
                result.append(await self._decode(session_id, document, cache))
        return result

    async def migrate_legacy_events(self, session_id):
        """Copy, read back and verify legacy events before clearing the array.

        Safe to retry after a crash. Run bulk migrations with legacy writers
        stopped; the exact-array guard refuses to clear a changed snapshot.
        """
        await self.ensure_indexes()
        session = await self.sessions.find_one(
            {"session_id": session_id}, {"events": 1, "updated_at": 1}
        )
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        legacy = session.get("events", [])
        if not legacy:
            return 0
        await self.sessions.update_one(
            {"session_id": session_id}, {"$max": {"event_sequence": len(legacy)}}
        )
        logger.info("Migrating %s legacy events for session %s", len(legacy), session_id)
        cache = {}
        for sequence, event in enumerate(legacy, 1):
            await self.append(session_id, event, sequence=sequence, legacy_index=sequence - 1)
            saved = await self.events.find_one({
                "_id": self.event_key(session_id, event["id"], sequence - 1),
            })
            restored = await self._decode(session_id, saved, cache)
            if _digest(restored) != _digest(event):
                raise RuntimeError(f"Legacy event verification failed for {event['id']}")
        guard = {
            "session_id": session_id,
            "updated_at": session.get("updated_at"),
            "events": legacy,
        }
        result = await self.sessions.update_one(guard, {"$set": {
            "events": [], "event_storage_version": 1, "updated_at": datetime.now(UTC),
        }})
        if not result.matched_count:
            # Another new writer might have completed this same migration.
            current = await self.sessions.find_one({"session_id": session_id}, {"events": 1})
            if current is None or current.get("events"):
                raise RuntimeError(f"Session {session_id} changed during history migration; retry")
        logger.info("Verified and migrated %s events for session %s", len(legacy), session_id)
        return len(legacy)

    async def delete(self, session_id):
        # All payloads and uploads are session-owned, including uploads left by
        # an interrupted writer. No cross-session shared blob is ever deleted.
        await self.events.delete_many({"session_id": session_id})
        async for document in self.blob_files.find({"metadata.session_id": session_id}, {"_id": 1}):
            try:
                await self.bucket.delete(document["_id"])
            except NoFile:
                pass
        await self.payloads.delete_many({"session_id": session_id})
