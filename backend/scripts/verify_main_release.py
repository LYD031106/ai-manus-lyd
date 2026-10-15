"""Check a running main release through its public HTTP/WebSocket APIs.

Uses the configured local administrator without logging credentials. A unique
test session and upload are removed in finally. --run-task explicitly permits
the small live model/sandbox task; normal invocation checks login/history only.
"""
import argparse
import asyncio
from collections import Counter
import json
import logging
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings


class CheckFailed(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailed(code)


async def verify(args, emit):
    settings = get_settings()
    require(settings.auth_provider == "local", "local_auth_required")
    marker = "MANUS_RELEASE_" + uuid.uuid4().hex[:12]
    fixture_name = "release-fixture-" + uuid.uuid4().hex[:12] + ".txt"
    session_id = file_id = token = None
    cleanup_errors = []
    primary_error = None
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/") + "/api/v1/", timeout=45) as client:
        async def api(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            require(response.status_code == 200, f"http_{method}_{path.split('/')[0]}_{response.status_code}")
            body = response.json()
            require(body.get("code") == 0, "api_error")
            return body.get("data")

        try:
            config = await api("GET", "config/frontend")
            require(config["browser_enabled"] is False, "lightweight_browser_setting")
            login = await api("POST", "auth/login", json={
                "email": settings.local_auth_email,
                "password": settings.local_auth_password,
                "client": "web",
            })
            token = login["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            client.headers["X-Requested-With"] = "XMLHttpRequest"
            me = await api("GET", "auth/me")
            require(me["id"] == login["user"]["id"], "login_identity")
            refresh = await api("POST", "auth/refresh", json={"refresh_token": token, "rotate": False})
            token = refresh["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            # Exercise the browser path independently of Bearer credentials.
            del client.headers["Authorization"]
            try:
                cookie_me = await api("GET", "auth/me")
                require(cookie_me["id"] == login["user"]["id"], "cookie_login_identity")
                cookie_header = client.build_request("GET", "auth/me").headers.get("cookie")
                require(bool(cookie_header), "browser_session_cookie_missing")
            finally:
                client.headers["Authorization"] = "Bearer " + token
            emit("cookie_http_identity", success=True)
            listing = await api("GET", "sessions")
            # Exercise the actual history endpoint under authenticated ownership.
            own_histories = []
            for row in listing["sessions"][:3]:
                history = await api("GET", "sessions/" + row["session_id"])
                own_histories.append(len(history["events"]))
            emit("login_refresh_owned_histories", sessions=len(listing["sessions"]), history_counts=own_histories)
            if not args.run_task:
                return

            source = (
                "This is a deployment verification fixture. Execute exactly one small Python command "
                "in the sandbox that prints " + marker + ". Then reply with that marker. "
                "Do not search the web or inspect unrelated files. No other deliverables are needed.\n"
            )
            # The expected marker must only be available inside the attachment,
            # not in its filename or path, otherwise reading it is untested.
            uploaded = await api("POST", "files", files={"file": (fixture_name, source.encode(), "text/plain")})
            file_id = uploaded["file_id"]
            emit("upload", success=True)
            parsed = urlsplit(args.base_url)
            ws_base = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, parsed.path.rstrip("/"), "", ""))
            headers = {"Authorization": "Bearer " + token}
            cookie_headers = {
                "Cookie": cookie_header,
                "Origin": urlunsplit((parsed.scheme, parsed.netloc, "", "", "")),
            }
            async with websockets.connect(ws_base + "/api/v1/ws/sessions", additional_headers=cookie_headers, open_timeout=20) as list_ws:
                snapshot = json.loads(await asyncio.wait_for(list_ws.recv(), 20))
                require(snapshot.get("op") == "snapshot", "session_list_snapshot")
                created = await api("PUT", "sessions")
                session_id = created["session_id"]
                async with asyncio.timeout(20):
                    while True:
                        frame = json.loads(await list_ws.recv())
                        if frame.get("op") == "upsert" and frame.get("session", {}).get("session_id") == session_id:
                            break
                emit("session_list_websocket", snapshot=True, create_notification=True, auth="cookie")

            counts = Counter()
            phases = []
            async with websockets.connect(ws_base + "/api/v1/ws/chat", additional_headers=headers, open_timeout=20, max_size=8 * 1024 * 1024) as chat:
                def envelope(kind, **extra):
                    return {"id": uuid.uuid4().hex, "version": 2, "timestamp": int(time.time()), "type": kind, "session_id": session_id, **extra}

                def validate_frame(frame):
                    require(isinstance(frame, dict), "chat_frame_invalid")
                    require(frame.get("type") != "error", "chat_transport_error")
                    if frame.get("type") != "ping":
                        require(frame.get("session_id") == session_id, "chat_frame_session_mismatch")

                join_request = envelope("join_session")
                await chat.send(json.dumps(join_request))
                async with asyncio.timeout(20):
                    while True:
                        frame = json.loads(await chat.recv())
                        validate_frame(frame)
                        if frame.get("type") == "joined":
                            require(frame.get("request_id") == join_request["id"], "chat_join_request_mismatch")
                            break
                chat_request = envelope("chat", message="", attachments=[{"file_id": file_id, "filename": fixture_name}])
                await chat.send(json.dumps(chat_request))
                acknowledged = False
                emit("attachment_only_task_submitted", session_id=session_id)
                async with asyncio.timeout(args.task_timeout):
                    while True:
                        frame = json.loads(await chat.recv())
                        validate_frame(frame)
                        if frame.get("type") == "ack":
                            require(frame.get("request_id") == chat_request["id"], "chat_ack_request_mismatch")
                            require(frame.get("op") == "chat" and frame.get("ok") is True, "chat_ack_rejected")
                            acknowledged = True
                            emit("attachment_only_task_acknowledged", success=True)
                        if frame.get("type") == "event":
                            event_type = frame.get("event", "unknown")
                            counts[event_type] += 1
                            if event_type == "status_update":
                                phases.append((frame.get("data") or {}).get("agent_status"))
                            require(event_type not in ("error", "wait"), "task_" + event_type)
                        if frame.get("type") == "stream_end":
                            require(acknowledged, "chat_ack_missing")
                            break

            history = await api("GET", "sessions/" + session_id)
            require(history["status"] == "completed", "task_not_completed")
            console_outputs = []
            assistant_messages = []
            attached_input_found = False
            for event in history["events"]:
                data = event.get("data") or {}
                if event["event"] == "message" and data.get("role") == "user":
                    attached_input_found |= any(
                        item.get("file_id") == file_id
                        for item in (data.get("attachments") or [])
                        if isinstance(item, dict)
                    )
                if event["event"] == "tool" and data.get("name") == "shell":
                    content = data.get("content") or {}
                    console = content.get("console") if isinstance(content, dict) else None
                    if isinstance(console, list):
                        console_outputs.extend(
                            record["output"] for record in console
                            if isinstance(record, dict) and isinstance(record.get("output"), str)
                        )
                if event["event"] == "message" and data.get("role") == "assistant":
                    assistant_messages.append(data.get("content", ""))
            # A marker in tool arguments or an echoed command is insufficient.
            require(attached_input_found, "history_user_attachment_missing")
            require(any(marker in text.splitlines() for text in console_outputs), "shell_stdout_marker_missing")
            require(any(marker in text for text in assistant_messages), "assistant_marker_missing")
            require(counts["terminal_update"] > 0, "live_terminal_update_missing")
            require(counts["status_update"] > 0, "live_status_missing")
            require(phases[-1] == "completed", "live_final_status_not_completed")
            emit("live_attachment_model_sandbox_websocket_history", success=True, event_counts=dict(counts), history_events=len(history["events"]), shell_stdout_verified=True, final_status=phases[-1])
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_file_ids = {file_id} if file_id else set()
            if session_id:
                try:
                    await api("POST", "sessions/" + session_id + "/stop")
                except Exception as exc:
                    cleanup_errors.append({"step": "stop_session", "error_type": type(exc).__name__})
                try:
                    # Reading the fixture can create another stored file version.
                    # Limit cleanup to files linked to this unique test session.
                    session_files = await api("GET", "sessions/" + session_id + "/files")
                    cleanup_file_ids.update(item["file_id"] for item in session_files if item.get("file_id"))
                except Exception as exc:
                    cleanup_errors.append({"step": "list_session_files", "error_type": type(exc).__name__})
                try:
                    await api("DELETE", "sessions/" + session_id)
                except Exception as exc:
                    cleanup_errors.append({"step": "delete_session", "error_type": type(exc).__name__})
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
            emit("cleanup", success=not cleanup_errors, errors=cleanup_errors)
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
