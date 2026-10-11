"""Offline maintenance for session history and interrupted tool replies.

Run with all application writers stopped. Defaults to dry-run. The backup
directory and JSON log file must be persistent paths chosen by the operator.
This script never calls a model, sandbox, or tool and never deletes user input.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import stat
import sys

# A script mounted outside /app can still import the installed application.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bson import BSON
from pymongo import AsyncMongoClient

from app.core.config import get_settings
from app.domain.models.message import Role
from app.infrastructure.models.memory_serialization import deserialize_memory
from app.infrastructure.storage.session_event_store import MongoSessionEventStore


SAFE_STATUSES = {"completed", "idle"}


class MaintenanceError(RuntimeError):
    """A fixed diagnostic code, without potentially sensitive database data."""


def canonical_hash(value):
    def canonical(item):
        if isinstance(item, dict):
            return {key: canonical(item[key]) for key in sorted(item)}
        if isinstance(item, (list, tuple)):
            return [canonical(child) for child in item]
        return item
    return sha256(BSON.encode({"value": canonical(value)})).hexdigest()


def secure_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise MaintenanceError("backup_directory_is_symlink")
    path.chmod(0o700)
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise MaintenanceError("backup_directory_permissions")
    return path


def backup_document(directory, collection, document):
    """Create, fsync and read-back verify a restrictive gzip BSON backup."""
    raw = BSON.encode(document)
    digest = sha256(raw).hexdigest()
    path = secure_directory(directory) / f"{collection}-{digest}.bson.gz"
    if not path.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as target:
            with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
                compressed.write(raw)
            target.flush()
            os.fsync(target.fileno())
    if path.is_symlink():
        raise MaintenanceError("backup_file_is_symlink")
    path.chmod(0o600)
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise MaintenanceError("backup_file_permissions")
    with gzip.open(path, "rb") as source:
        if source.read() != raw:
            raise MaintenanceError("backup_verification_failed")
    # Persist the directory entry before allowing a database mutation.
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {"collection": collection, "sha256": digest, "bytes": len(raw)}


def plan_memory_repairs(memories, events):
    """Preserve all original message dictionaries; insert only missing replies."""
    results = {}
    for event in events:
        if event.get("type") != "tool" or event.get("status") != "called":
            continue
        call_id = event.get("tool_call_id")
        name = event.get("function_name")
        # The ask-user tool's success result only acknowledges displaying a
        # question. It is not evidence of the human's answer.
        if not call_id or name == "message_ask_user" or event.get("function_result") is None:
            continue
        results.setdefault(call_id, []).append((name, event["function_result"]))

    repaired_memories = deepcopy(memories or {})
    audit = []
    for memory_name, raw in (memories or {}).items():
        memory = deserialize_memory(raw)
        # This validates duplicate/orphan IDs before any in-memory mutation.
        missing = memory.repair_tool_pairs()
        if not missing:
            memory.validate_tool_pairs()
            continue
        by_id = {entry["tool_call_id"]: entry for entry in missing}
        for message in memory.messages:
            if message.role != Role.TOOL or message.tool_call_id not in by_id:
                continue
            entry = by_id[message.tool_call_id]
            candidates = results.get(message.tool_call_id, [])
            if candidates:
                if any(name != entry["name"] for name, _ in candidates):
                    raise MaintenanceError("called_event_name_conflict")
                hashes = {canonical_hash(result) for _, result in candidates}
                if len(hashes) != 1:
                    raise MaintenanceError("called_event_result_conflict")
                result = candidates[0][1]
                message.content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                entry["status"] = "recovered_called_result"
            # Without durable result evidence, use the domain repair's explicit
            # outcome_unknown/not_executed response. No tools are replayed.
        memory.validate_tool_pairs()
        original_messages = iter((raw or {}).get("messages", []) or [])
        inserted = []
        for message in memory.messages:
            if message.role == Role.TOOL and message.tool_call_id in by_id:
                inserted.append(message.model_dump())
            else:
                inserted.append(deepcopy(next(original_messages)))
        sentinel = object()
        if next(original_messages, sentinel) is not sentinel:
            raise MaintenanceError("original_message_preservation_failed")
        candidate = deepcopy(raw)
        candidate["messages"] = inserted
        candidate["tool_call_states"] = memory.tool_call_states
        candidate["waiting_tool_call_id"] = memory.waiting_tool_call_id
        deserialize_memory(candidate).validate_tool_pairs()
        repaired_memories[memory_name] = candidate
        audit.extend({"memory": memory_name, **entry} for entry in missing)
    return repaired_memories, audit


async def process_session(database, store, session, *, apply, backup_dir, emit):
    session_id = session["session_id"]
    agent_id = session.get("agent_id")
    identity = {"session_id": session_id, "agent_id": agent_id}
    if session.get("status") not in SAFE_STATUSES:
        emit("skipped_active_or_nonidle", **identity, status=session.get("status"))
        return "skipped"
    # A shared agent with an active session cannot be changed safely.
    async for sibling in database["sessions"].find({"agent_id": agent_id}, {"status": 1}):
        if sibling.get("status") in {"running", "waiting"}:
            emit("skipped_active_shared_agent", **identity)
            return "skipped"
    agent = await database["agents"].find_one({"agent_id": agent_id})
    if agent is None:
        raise MaintenanceError("agent_missing")
    history = await store.load(session_id, legacy_events=session.get("events", []))
    history_hash = canonical_hash(history)
    repaired_memories, audit = plan_memory_repairs(agent.get("memories", {}), history)
    emit("session_plan", **identity, legacy_event_count=len(session.get("events", [])),
         history_count=len(history), history_sha256=history_hash, repair_count=len(audit))
    for entry in audit:
        emit("tool_reply_plan", **identity, **entry)
    if not apply:
        return "dry_run"
    if not backup_dir:
        raise MaintenanceError("backup_directory_required")
    for collection, document in (("sessions", session), ("agents", agent)):
        emit("backup_verified", **identity, **backup_document(backup_dir, collection, document))
    # Recheck both backed-up snapshots before the first database mutation.
    current_session = await database["sessions"].find_one({"_id": session["_id"]})
    current_agent = await database["agents"].find_one({"_id": agent["_id"]})
    if canonical_hash(current_session) != canonical_hash(session) or canonical_hash(current_agent) != canonical_hash(agent):
        raise MaintenanceError("snapshot_changed_after_backup")
    migrated = await store.migrate_legacy_events(session_id)
    restored = await store.load(session_id)
    if canonical_hash(restored) != history_hash:
        raise MaintenanceError("hydrated_history_hash_changed")
    if audit:
        current_session = await database["sessions"].find_one({"_id": session["_id"]}, {"status": 1})
        if current_session is None or current_session.get("status") not in SAFE_STATUSES:
            raise MaintenanceError("session_became_active")
        # Matching all original fields protects the backed-up memory snapshot.
        update = await database["agents"].update_one(agent, {"$set": {
            "memories": repaired_memories, "updated_at": datetime.now(UTC),
        }})
        if update.matched_count != 1:
            raise MaintenanceError("agent_compare_and_swap_failed")
        saved = await database["agents"].find_one({"_id": agent["_id"]})
        if canonical_hash(saved.get("memories")) != canonical_hash(repaired_memories):
            raise MaintenanceError("repaired_memory_readback_failed")
        for raw in saved.get("memories", {}).values():
            deserialize_memory(raw).validate_tool_pairs()
    emit("session_verified", **identity, migrated_event_count=migrated, repair_count=len(audit), history_sha256=history_hash)
    return "applied"


def make_emitter(log_file):
    path = Path(log_file)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    stream = os.fdopen(os.open(path, flags, 0o600), "a", encoding="utf-8")
    path.chmod(0o600)

    def emit(event, **fields):
        line = json.dumps({"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields}, ensure_ascii=False)
        print(line, flush=True)
        stream.write(line + "\n")
        stream.flush()
    return emit, stream


async def run(args, emit):
    settings = get_settings()
    kwargs = {}
    if settings.mongodb_username and settings.mongodb_password:
        kwargs.update(username=settings.mongodb_username, password=settings.mongodb_password)
    client = AsyncMongoClient(settings.mongodb_uri, **kwargs)
    counts = {"applied": 0, "dry_run": 0, "skipped": 0, "failed": 0}
    try:
        database = client[settings.mongodb_database]
        store = MongoSessionEventStore(database)
        query = {"session_id": {"$in": args.session_id}} if args.session_id else {}
        found = set()
        async for session in database["sessions"].find(query):
            found.add(session["session_id"])
            try:
                outcome = await process_session(database, store, session, apply=args.apply, backup_dir=args.backup_dir, emit=emit)
                counts[outcome] += 1
            except Exception as exc:
                counts["failed"] += 1
                emit("session_failed", session_id=session.get("session_id"), agent_id=session.get("agent_id"),
                     error_type=type(exc).__name__, error_code=str(exc) if isinstance(exc, MaintenanceError) else "details_redacted")
        for session_id in set(args.session_id or []) - found:
            counts["failed"] += 1
            emit("session_missing", session_id=session_id)
        emit("summary", mode="apply" if args.apply else "dry_run", **counts)
        return 1 if counts["failed"] else 0
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write verified repairs; default is read-only dry-run")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--session-id", action="append", default=[])
    args = parser.parse_args()
    if args.apply and args.backup_dir is None:
        parser.error("--apply requires --backup-dir")
    # Third-party logging is excluded: exceptions may embed content or URIs.
    logging.disable(logging.CRITICAL)
    emit, stream = make_emitter(args.log_file)
    try:
        emit("start", mode="apply" if args.apply else "dry_run")
        try:
            return asyncio.run(run(args, emit))
        except Exception as exc:
            emit("fatal", error_type=type(exc).__name__, error_code="details_redacted")
            return 1
    finally:
        stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
