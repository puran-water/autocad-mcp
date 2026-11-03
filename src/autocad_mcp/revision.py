"""Bounded, journaled native revision operations, also usable via existing execute_lisp.

No drawing-specific code. All mutation targets require a full expected document path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
from pathlib import Path

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.config import LISP_DIR


class RevisionError(ValueError):
    pass


def literal(value):
    """Encode data as an AutoLISP literal; never interpolate executable user strings."""
    if value is None:
        return "nil"
    if value is True:
        return "T"
    if value is False:
        return "nil"
    if isinstance(value, str):
        if any(ord(c) < 32 for c in value):
            # AutoLISP supports these escape sequences in string literals.
            if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
                raise RevisionError("CONTROL_CHARACTER_UNSUPPORTED")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise RevisionError("NONFINITE_NUMBER")
        return repr(value)
    if isinstance(value, dict):
        return "(" + " ".join(f"({literal(k)} . {literal(v)})" for k, v in value.items()) + ")"
    if isinstance(value, (list, tuple)):
        return "(" + " ".join(literal(v) for v in value) + ")"
    raise RevisionError(f"UNSUPPORTED_VALUE:{type(value).__name__}")


def normalize_ops(ops):
    if not isinstance(ops, list) or not 1 <= len(ops) <= 7:
        raise RevisionError("BATCH_SIZE_1_TO_7_REQUIRED")
    result = []
    touched = set()
    for original in ops:
        op = dict(original)
        if not op.get("item"):
            raise RevisionError("ITEM_REQUIRED")
        if op.get("handle"):
            handle = op["handle"].upper()
            if not re.fullmatch(r"[0-9A-F]+", handle):
                raise RevisionError("INVALID_HANDLE")
            if handle in touched:
                raise RevisionError("ONE_OPERATION_PER_HANDLE_PER_BATCH")
            touched.add(handle)
            op["handle"] = handle
            expect = op.get("expect", {})
            if "0" not in expect and 0 not in expect:
                raise RevisionError("EXPECTED_TYPE_REQUIRED")
        for key in ("expect", "set"):
            if key in op:
                op[key] = {int(k): v for k, v in op[key].items()}
        if op.get("kind") == "patch":
            if not set(op.get("set", {})) <= set(op.get("expect", {})):
                raise RevisionError("EXPECTED_FIELD_REQUIRED")
            if 1 in op.get("set", {}) and op["expect"].get(0) == "MTEXT":
                # Long MTEXT needs its group-3 chunks handled as a whole, never partial.
                if op["expect"].get(0) == "MTEXT":
                    raise RevisionError("LONG_MTEXT_REQUIRES_TEXT_OPERATION")
        result.append(op)
    return result


def compile_call(operation, data, *, lisp_path=None):
    drawing = data.get("drawing")
    if not drawing or not (re.match(r"^[A-Za-z]:[/\\]", drawing) or drawing.startswith("\\\\")):
        raise RevisionError("EXPECTED_WINDOWS_DOCUMENT_PATH_REQUIRED")
    if operation == "batch":
        ops = normalize_ops(data["operations"])
        batch_id = data["id"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", batch_id):
            raise RevisionError("INVALID_BATCH_ID")
        digest = hashlib.sha256(
            json.dumps(ops, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        args = [drawing, batch_id, digest, data["journal"], ops]
        fn = "pcad:batch"
    elif operation == "frames":
        args, fn = [drawing], "pcad:frames"
    elif operation == "read":
        handles = data["handles"]
        if not 1 <= len(handles) <= 100:
            raise RevisionError("READ_LIMIT_1_TO_100")
        args, fn = [drawing, handles], "pcad:read-handles"
    elif operation == "export_page":
        args = [
            drawing,
            data["path"],
            data.get("offset", 0),
            data.get("limit", 250),
            data.get("definitions", False),
        ]
        fn = "pcad:export-page"
    elif operation == "plot":
        args, fn = [drawing, data["layout"], data["path"]], "pcad:plot"
    elif operation == "save":
        args, fn = [drawing, data.get("path"), data.get("format", 48)], "pcad:save"
    else:
        raise RevisionError("UNKNOWN_REVISION_OPERATION")
    load = str(lisp_path or LISP_DIR / "revision_tools.lsp").replace("\\", "/")
    return f"(progn (load {literal(load)}) (pcad:run '{fn} '{literal(args)}))"


def unpack(result):
    if not result.ok:
        return result
    try:
        payload = json.loads(result.payload)
    except (TypeError, ValueError):
        return CommandResult(ok=False, error="INVALID_REVISION_RESPONSE", payload=result.payload)
    if isinstance(payload, dict) and payload.get("ok") is False:
        return CommandResult(ok=False, error=payload.get("error"), payload=payload)
    return CommandResult(ok=True, payload=payload)


async def execute(backend, operation, data):
    if backend.name != "file_ipc":
        return CommandResult(ok=False, error="NATIVE_BACKEND_REQUIRED")
    return unpack(await backend.execute_lisp(compile_call(operation, data)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["compile", "batch", "read", "export_page", "plot", "save", "frames"]
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--lisp-path", help="Windows helper path when compiling from WSL")
    args = parser.parse_args()
    data = json.loads(args.request.read_text(encoding="utf-8"))
    if args.operation == "compile":
        output = compile_call(data.pop("operation"), data, lisp_path=args.lisp_path)
    else:
        from autocad_mcp.backends.file_ipc import FileIPCBackend

        async def run():
            backend = FileIPCBackend()
            init = await backend.initialize()
            if not init.ok:
                return init
            return await execute(backend, args.operation, data)

        result = asyncio.run(run())
        output = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    if args.operation != "compile" and not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
