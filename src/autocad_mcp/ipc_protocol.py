"""Durable request-specific File IPC v2. Never infer a claim from disappearance.

Only the process that renames READY owns the request. AutoLISP renames it to
CLAIMED before reading; Python can instead rename it to CANCELLED. These are
same-directory moves, not copy/delete operations. A delayed trigger names its
original request and cannot consume a later request.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

PROTOCOL = 2


def paths(directory: Path, request_id: str) -> dict[str, Path]:
    if not re.fullmatch(r"[a-f0-9]{12}", request_id):
        raise ValueError("INVALID_IPC_REQUEST_ID")
    prefix = directory / f"pcad-v2-{request_id}"
    return {
        state: Path(f"{prefix}.{state}.json")
        for state in ("ready", "claimed", "cancelled", "result")
    }


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


def cancel_unclaimed(directory: Path, request_id: str) -> bool:
    """Win the claim/cancel race or fail closed; retain the original envelope."""
    files = paths(directory, request_id)
    try:
        files["ready"].rename(files["cancelled"])
    except OSError:
        return False
    return True


def cancellation_proof(directory: Path, pending: dict) -> bool:
    if pending.get("protocol") != PROTOCOL:
        return False
    files = paths(directory, pending["request_id"])
    if any(files[key].exists() for key in ("ready", "claimed", "result")):
        return False
    try:
        return read_json(files["cancelled"]) == pending
    except (OSError, ValueError):
        return False
