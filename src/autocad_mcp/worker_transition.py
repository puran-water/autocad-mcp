"""Journaled native PostCommand handoff; no simulated keyboard bootstrap."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
import uuid

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.ipc_protocol import publish
from autocad_mcp.revision import literal
from autocad_mcp.worker import (
    normalize_drawing,
    observe,
    prepare_successor,
    preflight,
    request_stop,
)
from autocad_mcp.worker_control import bootstrap


def transition_code(mode, destination, successor, lisp_dir, file_format=64, ipc_dir="C:/temp"):
    destination = normalize_drawing(destination)
    if mode != "save" or file_format not in (48, 60, 64):
        raise ValueError("INVALID_WORKER_TRANSITION")
    library = Path(lisp_dir).as_posix()
    target = literal(destination)
    common = (
        "(if (/= *mcp-worker-bound* (mcp-worker-normal-path (mcp-worker-drawing))) "
        '(pcad:fail "TRANSITION_SOURCE_CHANGED")) '
        "(setq pcadTarget (vla-get-ActiveDocument (vlax-get-acad-object))) "
    )
    action = (
        f"(if (and (findfile {target}) (/= *mcp-worker-bound* {target})) "
        '(pcad:fail "TRANSITION_DESTINATION_EXISTS")) '
        f"(vla-SaveAs pcadTarget {target} {file_format}) "
        f"(if (/= (mcp-worker-normal-path (vla-get-FullName pcadTarget)) {target}) "
        '(pcad:fail "TRANSITION_SAVE_IDENTITY_MISMATCH")) '
    )
    submit = f"(vla-PostCommand pcadTarget {literal(bootstrap(successor['session'], library, ipc_dir, successor.get('build')))}) "
    return (
        f"(progn (load {literal(library + '/revision_tools.lsp')}) "
        "(pcad:run '(lambda (/ pcadTarget) "
        + common
        + action
        + submit
        # Save As renames this document. Hold the old worker until the client has
        # verified its result and explicitly requests stop; all clients are fenced
        # by pcad-handoff.json during this interval.
        + "(setq *mcp-worker-bound* (mcp-worker-normal-path (mcp-worker-drawing))) "
        + '"QUEUED_SUCCESSOR") nil))'
    )


async def transition_locked(backend, mode, destination, *, file_format=64, timeout=20):
    """Caller holds both backend and OS locks and has verified no pending intent."""
    from autocad_mcp.config import LISP_DIR

    destination = normalize_drawing(destination)
    if mode != "save" or file_format not in (48, 60, 64):
        raise ValueError("INVALID_WORKER_TRANSITION")
    root = backend._ipc_dir
    intent_path = root / "pcad-handoff.json"
    if intent_path.exists():
        return CommandResult(ok=False, error="IPC_HANDOFF_RECONCILE_REQUIRED")
    parent = backend._worker_session
    successor = prepare_successor(root, parent, destination)
    intent = dict(
        id=uuid.uuid4().hex,
        state="prepared",
        parent=parent,
        successor=successor,
        mode=mode,
        destination=normalize_drawing(destination),
    )
    publish(intent_path, intent)
    code = transition_code(mode, destination, successor, LISP_DIR, file_format, root)
    code_file = root / f"pcad-handoff-{intent['id']}.lsp"
    code_file.write_text(code, encoding="utf-8")
    result, _ = await backend._dispatch_unlocked(
        "execute-lisp", {"code_file": code_file.as_posix()}, timeout=120
    )
    if not result.ok or result.payload != "QUEUED_SUCCESSOR":
        intent.update(state="uncertain", error=result.error, result=result.payload)
        publish(intent_path, intent)
        return CommandResult(ok=False, error="IPC_HANDOFF_RECONCILE_REQUIRED", payload=intent)
    intent["state"] = "queued"
    publish(intent_path, intent)
    request_stop(root, parent)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = observe(root, successor["session"])
        if state.get("state") == "idle":
            try:
                state = preflight(root, successor["session"])
            except RuntimeError:
                pass
            else:
                old = observe(root, parent)
                if old.get("state") != "stopped":
                    break
                intent.update(state="complete", old_state=old, new_state=state)
                publish(root / "pcad-receipts" / f"handoff-{intent['id']}.json", intent)
                backend._worker_session = successor["session"]
                backend._worker_state = state
                intent_path.unlink()
                return CommandResult(
                    ok=True,
                    payload=dict(
                        drawing=state["drawing"],
                        session=successor["session"],
                        transition=intent["id"],
                        dbmod=state.get("dbmod"),
                    ),
                )
        if state.get("state") == "stopped":
            break
        await asyncio.sleep(0.05)
    intent.update(state="uncertain", successor_state=observe(root, successor["session"]))
    publish(intent_path, intent)
    return CommandResult(ok=False, error="IPC_HANDOFF_RECONCILE_REQUIRED", payload=intent)
