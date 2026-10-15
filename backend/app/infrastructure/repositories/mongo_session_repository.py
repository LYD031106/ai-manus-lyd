from typing import Optional, List
from datetime import datetime, UTC
from app.domain.models.session import Session, SessionStatus, SessionSummary, TaskMode
from app.domain.models.file import FileInfo
from app.domain.repositories.session_repository import SessionRepository
from app.domain.models.event import BaseEvent
from app.infrastructure.models.documents import SessionDocument
from app.infrastructure.storage.session_event_store import MongoSessionEventStore
import logging

logger = logging.getLogger(__name__)

SESSION_LIST_PROJECTION = {
    "session_id": 1,
    "user_id": 1,
    "title": 1,
    "unread_message_count": 1,
    "latest_message": 1,
    "latest_message_at": 1,
    "status": 1,
    "is_shared": 1,
    "is_favorite": 1,
    "project_id": 1,
    "task_mode": 1,
}

# The history endpoint does not need sandbox/file-library metadata.  Keeping
# this projection separate avoids transferring the per-session ``files`` array
# and unrelated fields before the event list is converted for the browser.
SESSION_HISTORY_PROJECTION = {
    "session_id": 1,
    "user_id": 1,
    "agent_id": 1,
    "title": 1,
    "status": 1,
    "is_shared": 1,
    "is_favorite": 1,
    "project_id": 1,
    "task_mode": 1,
    "events": 1,
}

class MongoSessionRepository(SessionRepository):
    """MongoDB implementation of SessionRepository"""

    def __init__(self, event_store=None):
        self._event_store = event_store

    @property
    def event_store(self):
        if self._event_store is None:
            self._event_store = MongoSessionEventStore(
                SessionDocument.get_pymongo_collection().database
            )
        return self._event_store

    async def _to_domain_with_events(self, mongo_session):
        if mongo_session is None:
            return None
        domain = mongo_session.to_domain()
        events = await self.event_store.load(
            domain.id, [event.model_dump() for event in domain.events]
        )
        return Session.model_validate({**domain.model_dump(exclude={"events"}), "events": events})

    async def migrate_legacy_events(self, session_id: str) -> int:
        return await self.event_store.migrate_legacy_events(session_id)

    async def save(self, session: Session) -> None:
        """Write metadata without copying hydrated history into one BSON document."""
        collection = SessionDocument.get_pymongo_collection()
        existing = await collection.find_one({"session_id": session.id}, {"_id": 1})
        if existing:
            await self.migrate_legacy_events(session.id)
        data = session.model_dump(exclude={"id", "events", "created_at"})
        data["updated_at"] = datetime.now(UTC)
        await collection.update_one(
            {"session_id": session.id},
            {"$set": data, "$setOnInsert": {
                "session_id": session.id, "created_at": session.created_at,
                "events": [], "event_storage_version": 1,
            }},
            upsert=True,
        )
        # Existing session saves update task/sandbox metadata. Their hydrated
        # events are an old snapshot, not a new append request (and validation
        # can have filled defaults absent from historical payloads).
        if not existing:
            for event in session.events:
                await self.event_store.append(session.id, event.model_dump())


    async def find_by_id(self, session_id: str) -> Optional[Session]:
        """Find a session by its ID"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        return await self._to_domain_with_events(mongo_session)
    
    async def find_by_user_id(self, user_id: str) -> List[Session]:
        """Find all sessions for a specific user"""
        mongo_sessions = await SessionDocument.find(
            SessionDocument.user_id == user_id
        ).sort("-latest_message_at").to_list()
        return [await self._to_domain_with_events(mongo_session) for mongo_session in mongo_sessions]

    async def find_summaries_by_user_id(self, user_id: str) -> List[SessionSummary]:
        """Find lightweight session summaries for a user (excludes events/files)"""
        collection = SessionDocument.get_pymongo_collection()
        cursor = collection.find(
            {"user_id": user_id},
            SESSION_LIST_PROJECTION,
        ).sort("latest_message_at", -1)
        summaries = []
        async for doc in cursor:
            summaries.append(SessionSummary(
                id=doc["session_id"],
                user_id=doc["user_id"],
                title=doc.get("title"),
                unread_message_count=doc.get("unread_message_count", 0),
                latest_message=doc.get("latest_message"),
                latest_message_at=doc.get("latest_message_at"),
                status=doc.get("status", SessionStatus.PENDING),
                is_shared=doc.get("is_shared", False),
                is_favorite=doc.get("is_favorite", False),
                project_id=doc.get("project_id"),
                task_mode=doc.get("task_mode") or TaskMode.AGENT,
            ))
        return summaries
    
    async def find_by_id_and_user_id(self, session_id: str, user_id: str) -> Optional[Session]:
        """Find a session by ID and user ID (for authorization)"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id,
            SessionDocument.user_id == user_id
        )
        return await self._to_domain_with_events(mongo_session)

    async def find_history_by_id_and_user_id(self, session_id: str, user_id: str) -> Optional[Session]:
        """Load only the event payload needed to restore a chat page."""
        collection = SessionDocument.get_pymongo_collection()
        document = await collection.find_one(
            {"session_id": session_id, "user_id": user_id},
            SESSION_HISTORY_PROJECTION,
        )
        if not document:
            return None

        # Beanie's document model supplies defaults for fields omitted by the
        # projection (files, timestamps, counters, etc.).
        mongo_session = SessionDocument.model_validate(document)
        return await self._to_domain_with_events(mongo_session)
    
    async def update_title(self, session_id: str, title: str) -> None:
        """Update the title of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"title": title, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_latest_message(self, session_id: str, message: str, timestamp: datetime) -> None:
        """Update the latest message of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"latest_message": message, "latest_message_at": timestamp, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def add_event(self, session_id: str, event: BaseEvent) -> None:
        """Persist an event separately; retrying its stable ID is idempotent."""
        await self.migrate_legacy_events(session_id)
        await self.event_store.append(session_id, event.model_dump())
        result = await SessionDocument.get_pymongo_collection().update_one(
            {"session_id": session_id}, {"$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result.matched_count:
            raise ValueError(f"Session {session_id} not found")
    
    async def add_file(self, session_id: str, file_info: FileInfo) -> None:
        """Add a file to a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$push": {"files": file_info.model_dump()}, "$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")
    
    async def remove_file(self, session_id: str, file_id: str) -> None:
        """Remove a file from a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$pull": {"files": {"file_id": file_id}}, "$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def get_file_by_path(self, session_id: str, file_path: str) -> Optional[FileInfo]:
        """Get file by path from a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        if not mongo_session:
            raise ValueError(f"Session {session_id} not found")
        
        # Search for file with matching path
        for file_info in mongo_session.files:
            if file_info.file_path == file_path:
                return file_info
        return None

    async def delete(self, session_id: str) -> None:
        """Delete a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        if mongo_session:
            await mongo_session.delete()
        await self.event_store.delete(session_id)

    async def get_all(self) -> List[Session]:
        """Get all sessions"""
        mongo_sessions = await SessionDocument.find().sort("-latest_message_at").to_list()
        return [await self._to_domain_with_events(mongo_session) for mongo_session in mongo_sessions]
    
    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """Update the status of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"status": status, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_unread_message_count(self, session_id: str, count: int) -> None:
        """Update the unread message count of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"unread_message_count": count, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def increment_unread_message_count(self, session_id: str) -> None:
        """Atomically increment the unread message count of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$inc": {"unread_message_count": 1}, "$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def decrement_unread_message_count(self, session_id: str) -> None:
        """Atomically decrement the unread message count of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$inc": {"unread_message_count": -1}, "$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_shared_status(self, session_id: str, is_shared: bool) -> None:
        """Update the shared status of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"is_shared": is_shared, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_favorite_status(self, session_id: str, is_favorite: bool) -> None:
        """Update the favorite status of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"is_favorite": is_favorite, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_project_id(self, session_id: str, project_id: Optional[str]) -> None:
        """Assign or clear project association for a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"project_id": project_id, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def clear_project_id(self, project_id: str) -> None:
        """Clear project_id from all sessions belonging to a project"""
        await SessionDocument.find(
            SessionDocument.project_id == project_id
        ).update(
            {"$set": {"project_id": None, "updated_at": datetime.now(UTC)}}
        )

    async def update_task_mode(self, session_id: str, task_mode: str) -> None:
        """Update session task mode (agent | chat)"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"task_mode": task_mode, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

