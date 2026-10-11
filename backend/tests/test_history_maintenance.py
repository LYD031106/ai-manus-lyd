"""Offline verification of backup-first, lossless history maintenance."""
from copy import deepcopy
from datetime import datetime
import gzip
import json
import os
import stat

from bson import BSON, ObjectId
import pytest

from app.domain.models.memory import ToolHistoryError
from app.infrastructure.models.memory_serialization import deserialize_memory
from app.infrastructure.storage.session_event_store import MongoSessionEventStore
from scripts.repair_session_history import (
    MaintenanceError, backup_document, canonical_hash, plan_memory_repairs, process_session,
)
from test_session_event_store import Bucket, Database


def _memories():
    return {"execution": {"preserved_extra": "private-extra", "messages": [
        {"role": "user", "content": "private-user-message", "historical_field": "keep"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-real", "name": "file_write", "args": {"content": "private-content"}},
            {"id": "call-unknown", "name": "shell_exec", "args": {}},
            {"id": "call-question", "name": "message_ask_user", "args": {}},
        ]},
        {"role": "user", "content": "private-continuation"},
    ]}}


def _events():
    return [
        {"id": "event-real", "type": "tool", "status": "called", "tool_call_id": "call-real", "function_name": "file_write", "function_result": {"success": True, "data": "private-result"}},
        {"id": "event-question", "type": "tool", "status": "called", "tool_call_id": "call-question", "function_name": "message_ask_user", "function_result": {"success": True}},
    ]


async def _storage(status="completed"):
    database = Database()
    session = {"_id": ObjectId(), "session_id": "s1", "agent_id": "a1", "status": status, "events": _events(), "updated_at": datetime(2026, 10, 11)}
    agent = {"_id": ObjectId(), "agent_id": "a1", "memories": _memories(), "updated_at": datetime(2026, 10, 11)}
    await database["sessions"].insert_one(session)
    await database["agents"].insert_one(agent)
    return database, MongoSessionEventStore(database, bucket=Bucket(database)), session, agent


def test_recover_real_result_keep_user_data_and_do_not_invent_user_reply():
    original = _memories()
    memories, audit = plan_memory_repairs(original, _events())
    repaired = deserialize_memory(memories["execution"])
    repaired.validate_tool_pairs()
    replies = {m.tool_call_id: m.content for m in repaired.messages if m.tool_call_id}
    assert json.loads(replies["call-real"]) == {"success": True, "data": "private-result"}
    assert json.loads(replies["call-unknown"])["status"] == "outcome_unknown"
    assert json.loads(replies["call-question"])["status"] == "outcome_unknown"
    assert memories["execution"]["messages"][0] == original["execution"]["messages"][0]
    assert memories["execution"]["messages"][-1] == original["execution"]["messages"][-1]
    assert memories["execution"]["preserved_extra"] == "private-extra"
    assert original == _memories()
    assert {entry["status"] for entry in audit} == {"recovered_called_result", "outcome_unknown"}
    repeated, repeated_audit = plan_memory_repairs(memories, _events())
    assert repeated == memories and repeated_audit == []


def test_ambiguous_results_or_orphan_replies_fail_without_repair():
    events = _events()
    conflict = deepcopy(events[0])
    conflict["function_result"] = {"success": False}
    with pytest.raises(MaintenanceError, match="called_event_result_conflict"):
        plan_memory_repairs(_memories(), events + [conflict])
    memories = _memories()
    memories["execution"]["messages"].append({"role": "tool", "tool_call_id": "orphan", "content": "private"})
    with pytest.raises(ToolHistoryError):
        plan_memory_repairs(memories, events)


def test_backup_round_trips_raw_bson_and_has_restrictive_permissions(tmp_path):
    document = {"_id": ObjectId(), "data": "private-data", "timestamp": datetime(2026, 10, 11)}
    root = tmp_path / "backup"
    summary = backup_document(root, "sessions", document)
    files = list(root.iterdir())
    assert len(files) == 1
    with gzip.open(files[0], "rb") as stream:
        assert stream.read() == BSON.encode(document)
    assert "private" not in json.dumps(summary)
    assert backup_document(root, "sessions", document) == summary
    assert len(list(root.iterdir())) == 1
    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


async def test_dry_run_changes_no_database_documents_and_logs_no_contents(tmp_path):
    database, store, session, agent = await _storage()
    logs = []
    outcome = await process_session(database, store, session, apply=False, backup_dir=None,
                                    emit=lambda event, **fields: logs.append({"event": event, **fields}))
    assert outcome == "dry_run"
    assert await database["sessions"].find_one({"session_id": "s1"}) == session
    assert await database["agents"].find_one({"agent_id": "a1"}) == agent
    assert not database["session_events"].docs
    assert not database["session_events"].indexes
    assert "private" not in json.dumps(logs)


async def test_apply_verifies_backups_before_writes_and_is_idempotent(tmp_path):
    database, store, session, agent = await _storage()
    logs = []
    root = tmp_path / "backup"
    original_migrate = store.migrate_legacy_events

    async def checked_migrate(session_id):
        assert len(list(root.glob("*.bson.gz"))) >= 2
        return await original_migrate(session_id)

    store.migrate_legacy_events = checked_migrate
    emit = lambda event, **fields: logs.append({"event": event, **fields})
    assert await process_session(database, store, session, apply=True, backup_dir=root, emit=emit) == "applied"
    current = await database["sessions"].find_one({"session_id": "s1"})
    saved_agent = await database["agents"].find_one({"agent_id": "a1"})
    assert current["events"] == []
    assert canonical_hash(await store.load("s1")) == canonical_hash(session["events"])
    deserialize_memory(saved_agent["memories"]["execution"]).validate_tool_pairs()
    event_count = len(database["session_events"].docs)
    assert await process_session(database, store, current, apply=True, backup_dir=root, emit=emit) == "applied"
    assert len(database["session_events"].docs) == event_count
    assert await database["agents"].find_one({"agent_id": "a1"}) == saved_agent
    assert "private" not in json.dumps(logs)


@pytest.mark.parametrize("status", ["running", "waiting", "pending"])
async def test_active_or_nonidle_sessions_are_skipped(tmp_path, status):
    database, store, session, agent = await _storage(status)
    assert await process_session(database, store, session, apply=True, backup_dir=tmp_path / "backup", emit=lambda *a, **k: None) == "skipped"
    assert await database["agents"].find_one({"agent_id": "a1"}) == agent
    assert not list(tmp_path.iterdir())


async def test_changed_snapshot_aborts_before_migration(tmp_path):
    database, store, session, agent = await _storage()
    await database["agents"].update_one({"agent_id": "a1"}, {"$set": {"concurrent_field": True}})
    # Snapshot changes after it is read but before database writes are allowed.
    def emit(event, **fields):
        if event == "backup_verified" and fields["collection"] == "agents":
            database["agents"].docs[agent["_id"]]["another_change"] = True
    with pytest.raises(MaintenanceError, match="snapshot_changed_after_backup"):
        await process_session(database, store, session, apply=True, backup_dir=tmp_path / "backup", emit=emit)
    assert not database["session_events"].docs
    assert await database["sessions"].find_one({"session_id": "s1"}) == session


async def test_corrupt_backup_aborts_before_database_writes(tmp_path):
    database, store, session, agent = await _storage()
    root = tmp_path / "backup"
    backup_document(root, "sessions", session)
    next(root.iterdir()).write_bytes(b"corrupt backup")
    with pytest.raises(gzip.BadGzipFile):
        await process_session(database, store, session, apply=True, backup_dir=root, emit=lambda *a, **k: None)
    assert not database["session_events"].docs
    assert await database["sessions"].find_one({"session_id": "s1"}) == session
    assert await database["agents"].find_one({"agent_id": "a1"}) == agent


async def test_agent_cas_refuses_changes_during_event_migration(tmp_path):
    database, store, session, agent = await _storage()
    original_migrate = store.migrate_legacy_events

    async def racing_migrate(session_id):
        count = await original_migrate(session_id)
        await database["agents"].update_one({"agent_id": "a1"}, {"$set": {"updated_at": datetime(2026, 10, 12)}})
        return count

    store.migrate_legacy_events = racing_migrate
    with pytest.raises(MaintenanceError, match="agent_compare_and_swap_failed"):
        await process_session(database, store, session, apply=True, backup_dir=tmp_path / "backup", emit=lambda *a, **k: None)
    current = await database["agents"].find_one({"agent_id": "a1"})
    assert current["memories"] == agent["memories"]
    assert canonical_hash(await store.load("s1")) == canonical_hash(session["events"])
