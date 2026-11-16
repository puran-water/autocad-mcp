"""Explicit, document-bound resident worker sessions. No UI input or auto-start.

The native worker consumes the existing atomic claim/cancel envelopes. A missing
or stopped worker is a preflight failure, never a reason to fall back to typing.
"""

from __future__ import annotations

from pathlib import Path
import asyncio
import re
import time
import uuid

from autocad_mcp.ipc_protocol import publish, read_json
from autocad_mcp.build import LOADED


def normalize_drawing(value: str) -> str:
    if not isinstance(value, str) or not re.match(r"^[A-Za-z]:[/\\].+\.dwg$", value, re.I):
        raise ValueError("WORKER_REQUIRES_ABSOLUTE_WINDOWS_DWG")
    if any(c in value for c in "\n\r\0"):
        raise ValueError("WORKER_INVALID_DRAWING")
    return value.replace("\\", "/").upper()


def session_paths(directory: Path, session: str) -> dict[str, Path]:
    if not re.fullmatch(r"[0-9a-f]{32}", session):
        raise ValueError("WORKER_INVALID_SESSION")
    prefix = directory / f"pcad-worker-{session}"
    return {
        name: Path(f"{prefix}.{suffix}.json")
        for name, suffix in (
            ("manifest", "session"),
            ("stop", "stop"),
            ("heartbeat0", "heartbeat-0"),
            ("heartbeat1", "heartbeat-1"),
        )
    } | {"lock": directory / "pcad-worker.lock.json"}


def prepare(directory: Path, drawing: str, *, idle_timeout_s=1800, max_duration_s=14400) -> dict:
    drawing = normalize_drawing(drawing)
    if not 10 <= idle_timeout_s <= max_duration_s <= 14400:
        raise ValueError("WORKER_INVALID_SESSION_LIMITS")
    # Call under the same OS IPC lease as dispatch; do not override stale intent.
    if any(
        (directory / name).exists()
        for name in ("pcad-pending.json", "pcad-worker.lock.json", "pcad-handoff.json")
    ):
        raise RuntimeError("WORKER_EXISTING_INTENT_RECONCILE_REQUIRED")
    session = uuid.uuid4().hex
    data = dict(
        worker_protocol=1,
        session=session,
        drawing=drawing,
        idle_timeout_s=idle_timeout_s,
        max_duration_s=max_duration_s,
        build=LOADED["sha256"],
    )
    publish(session_paths(directory, session)["manifest"], data)
    return data


def discover(directory: Path) -> str:
    """Attach to a live native session after a Python server restart; never start one."""
    try:
        lock = read_json(directory / "pcad-worker.lock.json")
        token = lock["session"]
        session_paths(directory, token)
        return token
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            "WORKER_NOT_RUNNING; use system worker prepare, then run its bootstrap SCRIPT in the intended drawing"
        ) from exc


def prepare_successor(
    directory: Path, parent: str, drawing: str, *, idle_timeout_s=1800, max_duration_s=14400
) -> dict:
    """Prepare an explicit native handoff while retaining the parent's lock.

    This does not stop, activate or start anything. Caller must hold the IPC
    lease, retain a handoff intent, and verify both native endpoints afterward.
    """
    preflight(directory, parent)
    if (directory / "pcad-pending.json").exists():
        raise RuntimeError("WORKER_EXISTING_INTENT_RECONCILE_REQUIRED")
    if not 10 <= idle_timeout_s <= max_duration_s <= 14400:
        raise ValueError("WORKER_INVALID_SESSION_LIMITS")
    data = dict(
        worker_protocol=1,
        session=uuid.uuid4().hex,
        drawing=normalize_drawing(drawing),
        parent_session=parent,
        idle_timeout_s=idle_timeout_s,
        max_duration_s=max_duration_s,
        build=LOADED["sha256"],
    )
    publish(session_paths(directory, data["session"])["manifest"], data)
    return data


def observe(directory: Path, session: str) -> dict:
    files = session_paths(directory, session)
    records = []
    for key in ("heartbeat0", "heartbeat1"):
        try:
            data = read_json(files[key])
            if (
                data.get("worker_protocol") == 1
                and data.get("session") == session
                and isinstance(data.get("sequence"), int)
                and data["sequence"] > 0
            ):
                records.append((data["sequence"], files[key].stat().st_mtime, data))
        except (OSError, ValueError, AttributeError):
            continue
    if not records:
        return {"state": "absent", "session": session}
    _, modified, data = max(records, key=lambda item: item[0])
    return dict(data, heartbeat_age_s=max(0, time.time() - modified))


def preflight(directory: Path, session: str) -> dict:
    files = session_paths(directory, session)
    try:
        manifest, lock = read_json(files["manifest"]), read_json(files["lock"])
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "WORKER_NOT_RUNNING; explicitly start or reconcile the native worker"
        ) from exc
    if manifest != lock or manifest.get("session") != session:
        raise RuntimeError("WORKER_SESSION_LOCK_MISMATCH")
    state = observe(directory, session)
    if state.get("state") != "idle" or state.get("heartbeat_age_s", 999) > 2:
        raise RuntimeError("WORKER_NOT_READY: " + state.get("state", "unknown"))
    if normalize_drawing(state.get("drawing")) != manifest["drawing"]:
        raise RuntimeError("WORKER_DRAWING_CHANGED_REBIND_REQUIRED")
    return state


async def wait_ready(directory: Path, session: str, *, timeout=2.0) -> dict:
    """Wait for native cleanup after a proven result, before publishing new work.

    The caller holds the IPC lease and has already refused any pending intent.
    A running heartbeat is not an error to replay: no new envelope exists yet.
    Other failures (stopped, stale, wrong document/session) remain immediate.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return preflight(directory, session)
        except RuntimeError as exc:
            state = observe(directory, session)
            if (
                str(exc) != "WORKER_NOT_READY: running"
                or state.get("heartbeat_age_s", 999) > 2
                or time.monotonic() >= deadline
            ):
                raise
        await asyncio.sleep(0.025)


def request_stop(directory: Path, session: str) -> dict:
    files = session_paths(directory, session)
    manifest = read_json(files["manifest"])
    if manifest.get("session") != session:
        raise RuntimeError("WORKER_SESSION_MISMATCH")
    publish(files["stop"], {"worker_protocol": 1, "session": session, "reason": "owner_stop"})
    # A stop file is only a request. Completion requires a stopped heartbeat and
    # released lock; it never proves a claimed drawing operation rolled back.
    return {"session": session, "stop_requested": True}
