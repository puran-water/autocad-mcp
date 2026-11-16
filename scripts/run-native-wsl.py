#!/usr/bin/env python3
"""Start the Windows native MCP through a working WSL interop endpoint.

Probe mode uses cmd.exe ver. Server mode waits for a readiness marker from the
same Windows process that launches MCP. No MCP input is forwarded until ready;
no fallback/restart occurs after forwarding starts. AutoCAD is never probed here.
"""

from __future__ import annotations

import json
import select
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

CMD = "/mnt/c/Windows/System32/cmd.exe"


def endpoints(current, root=Path("/run/WSL"), proc=Path("/proc")):
    found = []
    candidates = ([Path(current)] if current else []) + sorted(
        root.glob("*_interop"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for p in candidates:
        try:
            resolved = p.resolve(strict=True)
            pid = int(resolved.name.removesuffix("_interop"))
            info = resolved.stat()
            comm = (proc / str(pid) / "comm").read_text().strip()
            if (
                resolved.parent != root
                or info.st_uid != 0
                or not stat.S_ISSOCK(info.st_mode)
                or not (comm.startswith("Relay(") or comm.startswith("SessionLeader"))
            ):
                continue
        except (OSError, ValueError):
            continue
        value = str(resolved)
        if value not in found:
            found.append(value)
    return found[:12]


def probe(endpoint, timeout=4):
    env = dict(os.environ, WSL_INTEROP=endpoint)
    p = subprocess.Popen(
        [CMD, "/d", "/c", "ver"],
        env=env,
        cwd="/mnt/c",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        output, error = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # A Windows-side peer can retain pipe handles after the Linux bridge dies.
        # Never drain those pipes without a deadline during timeout cleanup.
        p.stdout.close()
        p.stderr.close()
        try:
            p.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return {"endpoint": endpoint, "ok": False, "reason": "WINDOWS_PROBE_TIMEOUT"}
    ok = p.returncode == 0 and b"Microsoft Windows" in output
    return {
        "endpoint": endpoint,
        "ok": ok,
        "reason": None
        if ok
        else (error.decode(errors="replace").strip() or f"exit {p.returncode}"),
    }


def stop_candidate(process):
    """Close a failed startup without waiting on Windows-owned pipe handles."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe:
            pipe.close()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def wait_ready(process, marker, timeout=6):
    """Read only startup output; never consume/forward client input here."""
    deadline = time.monotonic() + timeout
    output, errors = bytearray(), bytearray()
    readers = [process.stdout, process.stderr]
    while readers and time.monotonic() < deadline:
        ready, _, _ = select.select(readers, [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        for pipe in ready:
            data = os.read(pipe.fileno(), 65536)
            if not data:
                readers.remove(pipe)
                continue
            if pipe is process.stderr:
                errors.extend(data[: max(0, 65536 - len(errors))])
                continue
            output.extend(data)
            if len(output) > 65536:
                return False, b"", bytes(errors) + b"STARTUP_OUTPUT_LIMIT"
            # Only the exact first stdout line proves the expected batch began.
            if b"\n" in output:
                first, remainder = bytes(output).split(b"\n", 1)
                return first.rstrip(b"\r") == marker, remainder, bytes(errors)
    return False, b"", bytes(errors) + b"WINDOWS_STARTUP_NOT_READY"


def start_candidate(endpoint):
    marker = ("PCAD_NATIVE_READY_" + uuid.uuid4().hex).encode("ascii")
    env = dict(os.environ, WSL_INTEROP=endpoint)
    process = subprocess.Popen(
        [
            CMD,
            "/d",
            "/c",
            r"%USERPROFILE%\mcp-servers\autocad-mcp\run-native-ready.cmd",
            marker.decode(),
        ],
        env=env,
        cwd="/mnt/c",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        bufsize=0,
    )
    ready, remainder, errors = wait_ready(process, marker)
    if not ready:
        stop_candidate(process)
        return None, remainder, errors
    return process, remainder, errors


def pump(source, destination):
    try:
        while True:
            data = os.read(source.fileno(), 65536)
            if not data:
                break
            # Raw pipe writes may be short.
            view = memoryview(data)
            while view:
                count = os.write(destination.fileno(), view)
                view = view[count:]
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            destination.flush()
        except (OSError, ValueError):
            pass


def forward(process, remainder, errors):
    """One established stream for its lifetime. A server exit is terminal."""
    if errors:
        sys.stderr.buffer.write(errors)
        sys.stderr.buffer.flush()
    if remainder:
        sys.stdout.buffer.write(remainder)
        sys.stdout.buffer.flush()

    def client_to_server():
        pump(sys.stdin.buffer, process.stdin)
        try:
            process.stdin.close()
        except OSError:
            pass

    workers = [
        threading.Thread(target=client_to_server, daemon=True),
        threading.Thread(target=pump, args=(process.stdout, sys.stdout.buffer), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, sys.stderr.buffer), daemon=True),
    ]
    for worker in workers:
        worker.start()
    try:
        return process.wait()
    finally:
        # Output workers normally see EOF; client stdin may remain open until exit.
        for worker in workers[1:]:
            worker.join(timeout=1)


def main():
    if sys.argv[1:] not in ([], ["--probe"]):
        raise SystemExit("Usage: run-native-wsl.py [--probe]")
    attempts = []
    for endpoint in endpoints(os.environ.get("WSL_INTEROP")):
        if sys.argv[1:] == ["--probe"]:
            outcome = probe(endpoint)
            attempts.append(outcome)
            if outcome["ok"]:
                print(json.dumps({"ok": True, "selected": endpoint, "attempts": attempts}))
                return
            continue
        process, remainder, errors = start_candidate(endpoint)
        if process is None:
            attempts.append({"endpoint": endpoint, "error": errors.decode(errors="replace")})
            continue
        print(
            "AutoCAD MCP: forwarding established Windows process via " + endpoint, file=sys.stderr
        )
        # Deliberately no retry around forward: MCP input may contain native edits.
        raise SystemExit(forward(process, remainder, errors))
    report = {"ok": False, "selected": None, "attempts": attempts}
    if sys.argv[1:] == ["--probe"]:
        print(json.dumps(report))
    else:
        print("AUTOCAD_MCP_WSL_INTEROP_UNAVAILABLE: " + json.dumps(report), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
