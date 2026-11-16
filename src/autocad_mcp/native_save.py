"""Save in place and optionally preserve a verified recovery copy; no document switch."""

import hashlib
import os
from pathlib import Path
import tempfile

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.desktop import ipc_lease, inspect_pending
from autocad_mcp.ipc_protocol import publish
from autocad_mcp.revision import compile_call, unpack
from autocad_mcp.worker import normalize_drawing

FORMATS = {b"AC1024": 48, b"AC1027": 60, b"AC1032": 64}


def fingerprint(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        version = stream.read(6)
        digest.update(version)
        size += len(version)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return dict(
        path=str(path), bytes=size, version=version.decode("ascii"), sha256=digest.hexdigest()
    )


def recovery_copy(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("RECOVERY_TARGET_EXISTS")
    before = fingerprint(source)
    if before["version"].encode() not in FORMATS:
        raise ValueError("DWG_FORMAT_NOT_QUALIFIED")
    fd, name = tempfile.mkstemp(prefix=".pcad-recovery-", dir=destination.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as original:
            while chunk := original.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        saved = fingerprint(temp)
        after = fingerprint(source)
        if before != after or saved["sha256"] != before["sha256"]:
            raise RuntimeError("RECOVERY_SOURCE_CHANGED")
        os.link(temp, destination)  # Exclusive atomic publication; a race cannot overwrite.
        return dict(saved, path=str(destination))
    finally:
        temp.unlink(missing_ok=True)


async def save_current(backend, *, expected=None, file_format=None, copy_path=None):
    async with backend._lock:
        try:
            with ipc_lease(backend._ipc_dir):
                if inspect_pending(backend._ipc_dir)["pending"]:
                    return CommandResult(ok=False, error="IPC_UNCERTAIN_RECONCILE_REQUIRED")
                if (backend._ipc_dir / "pcad-handoff.json").exists():
                    return CommandResult(ok=False, error="IPC_HANDOFF_RECONCILE_REQUIRED")
                await backend._worker_ready()
                current = normalize_drawing(backend._worker_state["drawing"])
                if expected and normalize_drawing(expected) != current:
                    return CommandResult(ok=False, error="SAVE_SOURCE_MISMATCH")
                if copy_path:
                    copy_path = Path(normalize_drawing(copy_path))
                    if copy_path.exists() or str(copy_path).replace("\\", "/") == current:
                        return CommandResult(ok=False, error="RECOVERY_TARGET_EXISTS")
                    if not copy_path.parent.is_dir():
                        return CommandResult(ok=False, error="RECOVERY_DIRECTORY_REQUIRED")
                with Path(current).open("rb") as stream:
                    original_format = FORMATS.get(stream.read(6))
                file_format = original_format if file_format is None else file_format
                if file_format not in FORMATS.values():
                    return CommandResult(ok=False, error="DWG_FORMAT_NOT_QUALIFIED")
                code = compile_call("save", {"drawing": current, "format": file_format})
                # execute_lisp's file publication is independent of native dispatch.
                import uuid

                code_file = backend._ipc_dir / f"pcad-save-{uuid.uuid4().hex}.lsp"
                code_file.write_text(code, encoding="utf-8")
                raw, _ = await backend._dispatch_unlocked(
                    "execute-lisp", {"code_file": code_file.as_posix()}, timeout=120
                )
                result = unpack(raw)
                if not result.ok:
                    return result
                saved = fingerprint(current)
                if (
                    FORMATS.get(saved["version"].encode()) != file_format
                    or result.payload.get("dbmod") != 0
                ):
                    return CommandResult(
                        ok=False,
                        error="SAVE_VERIFICATION_FAILED",
                        payload=result.payload,
                        receipt=raw.receipt,
                    )
                answer = dict(drawing=current, dbmod=0, file=saved)
                if copy_path:
                    try:
                        answer["recovery_copy"] = recovery_copy(current, copy_path)
                    except (OSError, ValueError, RuntimeError) as exc:
                        return CommandResult(
                            ok=False,
                            error=str(exc),
                            payload=dict(answer, native_save_completed=True),
                            receipt=raw.receipt,
                        )
                if raw.receipt:
                    publish(
                        backend._ipc_dir
                        / "pcad-receipts"
                        / f"{raw.receipt['request_id']}.save.json",
                        answer,
                    )
                return CommandResult(ok=True, payload=answer, receipt=raw.receipt)
        except (RuntimeError, ValueError, OSError) as exc:
            return CommandResult(ok=False, error=str(exc))
