"""Durable request-specific File IPC v3. Never infer a claim from disappearance.

Only the process that exclusively creates the decision directory may move READY.
Competing renames alone can both succeed on Windows (#31). The directory is a
permanent fence, including after a crash before the move. A delayed trigger names
its original request and cannot consume a later request. The v3 namespace also
prevents old, rename-only dispatchers from claiming a fenced request.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

PROTOCOL = 3


def paths(directory: Path, request_id: str) -> dict[str, Path]:
    if not re.fullmatch(r"[a-f0-9]{12}", request_id):
        raise ValueError("INVALID_IPC_REQUEST_ID")
    prefix = directory / f"pcad-v3-{request_id}"
    return {
        state: Path(f"{prefix}.{state}.json")
        for state in ("ready", "claimed", "cancelled", "result")
    } | {"decision": Path(f"{prefix}.decision")}


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), strict=False)
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="cp1252"), strict=False)


def publish(path: Path, data: dict):
    """Publish a complete JSON document in the same filesystem."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def move_unclaimed(directory: Path, request_id: str, state: str) -> bool:
    """Arbitrate one irreversible claim/cancel decision; never remove its fence.

    AutoLISP's mcp-claim-request uses the same mkdir-before-rename sequence.
    Failure after mkdir is uncertain, not permission to retry or reclaim.
    """
    if state not in {"claimed", "cancelled"}:
        raise ValueError("INVALID_IPC_DECISION")
    files = paths(directory, request_id)
    try:
        if not files["ready"].is_file():
            return False
        files["decision"].mkdir()
        if any(files[key].exists() for key in ("claimed", "cancelled", "result")):
            return False
        files["ready"].rename(files[state])
    except OSError:
        return False
    return True


def cancel_unclaimed(directory: Path, request_id: str) -> bool:
    """Win the claim/cancel race or fail closed; retain the original envelope."""
    return move_unclaimed(directory, request_id, "cancelled")


def cancellation_proof(directory: Path, pending: dict) -> bool:
    if pending.get("protocol") != PROTOCOL:
        return False
    files = paths(directory, pending["request_id"])
    if not files["decision"].is_dir():
        return False
    if any(files[key].exists() for key in ("ready", "claimed", "result")):
        return False
    try:
        return read_json(files["cancelled"]) == pending
    except (OSError, ValueError):
        return False
