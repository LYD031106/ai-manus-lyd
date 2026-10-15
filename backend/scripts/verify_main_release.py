"""Verify the lightweight release through its JWT-authenticated HTTP/SSE APIs.

Use the configured local administrator without printing credentials. --run-task
allows one small model/sandbox task; all fixture HTTP resources are cleaned up.
Run in the backend environment and keep --log-file on persistent storage.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings


class CheckFailed(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailed(code)


async def sse_events(response):
    """Parse comments and multiline data without buffering the whole response."""
    require(response.status_code == 200, "sse_http_" + str(response.status_code))
    require("text/event-stream" in response.headers.get("content-type", ""), "sse_content_type")
    kind, data = "message", []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield kind, json.loads("\n".join(data))
            kind, data = "message", []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                kind = value
            elif field == "data":
                data.append(value)
    if data:
        yield kind, json.loads("\n".join(data))


def history_digest(history):
    return hashlib.sha256(json.dumps(history["events"], sort_keys=True).encode()).hexdigest()


async def verify(args, emit):
    settings = get_settings()
    require(settings.auth_provider == "local", "local_auth_required")
    require(settings.browser_enabled is False, "lightweight_browser_setting")
    marker = "MANUS_RELEASE_" + uuid.uuid4().hex[:12]
    fixture_name = "release-fixture-" + uuid.uuid4().hex[:12] + ".txt"
    session_id = file_id = token = None
    primary_error = None
    cleanup_errors = []
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/") + "/api/v1/", timeout=45) as client:
        async def api(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            require(response.status_code == 200, f"http_{method}_{path.split('/')[0]}_{response.status_code}")
            body = response.json()
            require(body.get("code") == 0, "api_error")
            return body.get("data")

        try:
            config = await api("GET", "config/frontend")
            require(config["auth_provider"] == "local", "frontend_auth_setting")
            login = await api("POST", "auth/login", json={
                "email": settings.local_auth_email,
                "password": settings.local_auth_password,
            })
            token = login["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            me = await api("GET", "auth/me")
            require(me["id"] == login["user"]["id"], "login_identity")
            refresh = await api("POST", "auth/refresh", json={"refresh_token": login["refresh_token"]})
            token = refresh["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            require((await api("GET", "auth/me"))["id"] == me["id"], "refresh_identity")
            listing = await api("GET", "sessions")
            history_counts = []
            for row in listing["sessions"][:3]:
                history = await api("GET", "sessions/" + row["session_id"])
                history_counts.append(len(history["events"]))
            emit("jwt_login_refresh_owned_histories", sessions=len(listing["sessions"]), history_counts=history_counts)
            if not args.run_task:
                return

            # Only reading the attachment reveals the expected stdout marker.
            source = (
                "Deployment verification fixture. Read this file, execute one small Python command "
                "in the sandbox that prints " + marker + ", then reply with that marker. "
                "Do not search the web or inspect unrelated files. No other deliverables are needed.\n"
            )
            uploaded = await api("POST", "files", files={"file": (fixture_name, source.encode(), "text/plain")})
            file_id = uploaded["file_id"]
            session_id = (await api("PUT", "sessions"))["session_id"]
            async with asyncio.timeout(20):
                async with client.stream("POST", "sessions") as response:
                    async for kind, data in sse_events(response):
                        if kind == "sessions":
                            require(any(row["session_id"] == session_id for row in data["sessions"]), "list_sse_session_missing")
                            break
                    else:
                        raise CheckFailed("list_sse_snapshot_missing")
            emit("session_list_sse", success=True)

            counts = Counter()
            terminal_event_id = None
            stream_timeout = httpx.Timeout(connect=20, read=None, write=45, pool=20)
            async with asyncio.timeout(args.task_timeout):
                async with client.stream("POST", "sessions/" + session_id + "/chat", timeout=stream_timeout, json={
                    "message": "",
                    "attachments": [{"file_id": file_id, "filename": fixture_name}],
                }) as response:
                    async for kind, data in sse_events(response):
                        counts[kind] += 1
                        require(kind not in ("error", "wait"), "task_" + kind)
                        if kind == "done":
                            terminal_event_id = data.get("event_id")
                            break
            require(counts["done"] == 1, "task_done_missing")
            require(bool(terminal_event_id), "task_done_identity_missing")
            history = await api("GET", "sessions/" + session_id)
            require(history["status"] == "completed", "task_not_completed")
            # The terminal event receives its durable Redis ID during publication.
            # Wait for that event's subsequent Mongo write before comparing history.
            async with asyncio.timeout(5):
                while not any(event["event"] == "done" and (event.get("data") or {}).get("event_id") == terminal_event_id
                              for event in history["events"]):
                    await asyncio.sleep(0.1)
                    history = await api("GET", "sessions/" + session_id)
            console_outputs, assistant_messages = [], []
            marker_shell_id = None
            attached_input_found = False
            input_event_id = None
            for event in history["events"]:
                data = event.get("data") or {}
                if event["event"] == "message":
                    if data.get("role") == "user":
                        attached_input_found |= any(
                            item.get("file_id") == file_id
                            for item in (data.get("attachments") or []) if isinstance(item, dict)
                        )
                        input_event_id = input_event_id or data.get("event_id")
                    elif data.get("role") == "assistant":
                        assistant_messages.append(data.get("content", ""))
                if event["event"] == "tool" and data.get("name") == "shell":
                    content = data.get("content") or {}
                    console = content.get("console") if isinstance(content, dict) else None
                    if isinstance(console, list):
                        console_outputs.extend(record["output"] for record in console
                                               if isinstance(record, dict) and isinstance(record.get("output"), str))
                    shell_id = (data.get("args") or {}).get("id")
                    if shell_id and data.get("status") == "called" and isinstance(console, list):
                        if any(isinstance(record, dict) and marker in str(record.get("output", "")).splitlines()
                               for record in console):
                            marker_shell_id = shell_id
            require(attached_input_found, "history_user_attachment_missing")
            require(any(marker in output.splitlines() for output in console_outputs), "shell_stdout_marker_missing")
            require(any(marker in message for message in assistant_messages), "assistant_marker_missing")
            require(bool(marker_shell_id), "marker_shell_session_id_missing")
            shell = await api("POST", "sessions/" + session_id + "/shell", json={"session_id": marker_shell_id})
            require(any(marker in record.get("output", "").splitlines() for record in shell.get("console", [])), "shell_rest_stdout_missing")
            emit("attachment_model_sandbox_sse_history", success=True, event_counts=dict(counts), history_events=len(history["events"]))

            require(bool(input_event_id), "input_history_anchor_missing")
            before_resume = history_digest(history)
            async with asyncio.timeout(20):
                async with client.stream("POST", "sessions/" + session_id + "/chat", json={"event_id": input_event_id}) as response:
                    async for kind, data in sse_events(response):
                        require(kind != "error", "history_resume_error")
                        if kind in ("done", "wait"):
                            break
            require(history_digest(await api("GET", "sessions/" + session_id)) == before_resume, "resume_changed_history")
            # Completed tasks may already have left the task registry; an empty
            # stream is valid here. Active UUID cursor translation is covered by
            # test_history_stream_resume, not proven by this completed-task check.
            emit("completed_history_resume_noop", success=True, active_cursor_verified=False)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_file_ids = {file_id} if file_id else set()
            if session_id:
                for step, method, path in [
                    ("stop_session", "POST", "sessions/" + session_id + "/stop"),
                    ("list_session_files", "GET", "sessions/" + session_id + "/files"),
                    ("delete_session", "DELETE", "sessions/" + session_id),
                ]:
                    try:
                        result = await api(method, path)
                        if step == "list_session_files":
                            cleanup_file_ids.update(item["file_id"] for item in result if item.get("file_id"))
                    except Exception as exc:
                        cleanup_errors.append({"step": step, "error_type": type(exc).__name__})
            for cleanup_file_id in cleanup_file_ids:
                try:
                    await api("DELETE", "files/" + cleanup_file_id)
                except Exception as exc:
                    cleanup_errors.append({"step": "delete_file", "error_type": type(exc).__name__})
            if token:
                try:
                    await api("POST", "auth/logout")
                except Exception as exc:
                    cleanup_errors.append({"step": "logout", "error_type": type(exc).__name__})
            emit("http_fixture_cleanup", success=not cleanup_errors, errors=cleanup_errors)
            if primary_error is None:
                require(not cleanup_errors, "cleanup_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run-task", action="store_true")
    parser.add_argument("--task-timeout", type=int, default=180)
    parser.add_argument("--log-file", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as log:
        def emit(case, **fields):
            line = json.dumps({"case": case, **fields})
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
        try:
            asyncio.run(verify(args, emit))
        except Exception as exc:
            emit("FAIL", error_type=type(exc).__name__, code=str(exc) if isinstance(exc, CheckFailed) else "details_redacted")
            return 1
        emit("PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
