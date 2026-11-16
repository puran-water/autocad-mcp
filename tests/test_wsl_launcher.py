import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "wsl_launcher", Path(__file__).parents[1] / "scripts/run-native-wsl.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_probe_does_not_pass_mcp_stdin_or_native_commands(monkeypatch):
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0, communicate=lambda **kw: (b"Microsoft Windows [Version 10]", b"")
        )

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    assert launcher.probe("/run/WSL/100_interop")["ok"]
    args, kw = calls[0]
    assert args[1:] == ["/d", "/c", "ver"]
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["cwd"] == "/mnt/c" and kw["env"]["WSL_INTEROP"] == "/run/WSL/100_interop"


def test_timeout_kills_only_harmless_probe(monkeypatch):
    killed = []

    class Probe:
        pid = 99
        stdout = SimpleNamespace(close=lambda: None)
        stderr = SimpleNamespace(close=lambda: None)

        def wait(self, *, timeout):
            assert timeout == 1
            return 0

        def communicate(self, **kwargs):
            if kwargs:
                raise subprocess.TimeoutExpired("ver", 4)
            pytest.fail("unbounded pipe drain after timeout")

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: Probe())
    monkeypatch.setattr(launcher.os, "killpg", lambda *args: killed.append(args))
    assert not launcher.probe("/run/WSL/100_interop")["ok"]
    assert killed == [(99, launcher.signal.SIGKILL)]


def test_fallback_stops_before_forwarding_and_never_restarts(monkeypatch):
    seen, forwarded = [], []
    monkeypatch.setattr(launcher.sys, "argv", ["launcher"])
    monkeypatch.setattr(launcher, "endpoints", lambda _: ["broken", "working", "unused"])
    child = object()

    def start(endpoint):
        seen.append(endpoint)
        return (child if endpoint == "working" else None), b"", b""

    monkeypatch.setattr(launcher, "start_candidate", start)
    monkeypatch.setattr(launcher, "forward", lambda *args: forwarded.append(args) or 7)
    with pytest.raises(SystemExit) as exc:
        launcher.main()
    assert exc.value.code == 7
    assert seen == ["broken", "working"] and len(forwarded) == 1
    assert forwarded[0][0] is child


def test_all_endpoints_broken_never_forwards_mcp_input(monkeypatch):
    monkeypatch.setattr(launcher.sys, "argv", ["launcher"])
    monkeypatch.setattr(launcher, "endpoints", lambda _: ["broken"])
    monkeypatch.setattr(launcher, "start_candidate", lambda p: (None, b"", b"not ready"))
    monkeypatch.setattr(launcher, "forward", lambda *a: pytest.fail("must not forward"))
    with pytest.raises(SystemExit) as exc:
        launcher.main()
    assert exc.value.code == 1


@pytest.mark.parametrize(
    "line,expected", [(b"TOKEN\r\npayload\n", True), (b"wrong\npayload\n", False)]
)
def test_ready_requires_exact_marker_and_preserves_following_bytes(line, expected):
    import os

    pipes = []
    for _ in range(2):
        r, w = os.pipe()
        pipes.extend([os.fdopen(r, "rb", buffering=0), os.fdopen(w, "wb", buffering=0)])
    try:
        pipes[1].write(line)
        child = SimpleNamespace(stdout=pipes[0], stderr=pipes[2])
        ok, remainder, errors = launcher.wait_ready(child, b"TOKEN", timeout=0.1)
        assert ok == expected and remainder == b"payload\n"
    finally:
        for pipe in pipes:
            pipe.close()


def test_wait_ready_is_bounded_with_open_silent_pipes():
    import os
    import time

    pipes = []
    for _ in range(2):
        r, w = os.pipe()
        pipes.extend([os.fdopen(r, "rb", buffering=0), os.fdopen(w, "wb", buffering=0)])
    try:
        child = SimpleNamespace(stdout=pipes[0], stderr=pipes[2])
        started = time.monotonic()
        ok, _, errors = launcher.wait_ready(child, b"TOKEN", timeout=0.02)
        assert not ok and time.monotonic() - started < 0.5
        assert b"NOT_READY" in errors
    finally:
        for pipe in pipes:
            pipe.close()


def test_established_proxy_preserves_bytes_and_closes_stdin_at_eof(monkeypatch):
    import sys
    import tempfile

    payload = bytes(range(256)) * 8192
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; d=sys.stdin.buffer.read(); sys.stdout.buffer.write(d); sys.stderr.buffer.write(b'diagnostic')",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    with (
        tempfile.TemporaryFile() as incoming,
        tempfile.TemporaryFile() as outgoing,
        tempfile.TemporaryFile() as errors,
    ):
        incoming.write(payload)
        incoming.seek(0)
        monkeypatch.setattr(launcher.sys, "stdin", SimpleNamespace(buffer=incoming))
        monkeypatch.setattr(launcher.sys, "stdout", SimpleNamespace(buffer=outgoing))
        monkeypatch.setattr(launcher.sys, "stderr", SimpleNamespace(buffer=errors))
        assert launcher.forward(child, b"prefix", b"startup") == 0
        outgoing.seek(0)
        errors.seek(0)
        assert outgoing.read() == b"prefix" + payload
        assert errors.read() == b"startupdiagnostic"
