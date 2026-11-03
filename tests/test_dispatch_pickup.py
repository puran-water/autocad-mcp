"""Tests for command pickup detection and dispatcher autoload — no AutoCAD needed.

The dispatcher deletes the command file as soon as it reads it. A file that is
never claimed means the active drawing has no dispatcher: the backend must
fail fast (not wait the full timeout) and, with autoload on, ask AutoCAD to load
mcp_dispatch.lsp and run that same command file, never sending it twice.
"""

import json
import time

import pytest

import autocad_mcp.backends.file_ipc as file_ipc
from autocad_mcp.backends.file_ipc import FileIPCBackend


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ipc, "PICKUP_TIMEOUT", 0.3)
    b = FileIPCBackend()
    b._ipc_dir = tmp_path
    monkeypatch.setattr(b, "_type_dispatch_trigger", lambda: None)
    return b


def _answer_pending(ipc_dir, payload="\"pong\""):
    """Play the LISP dispatcher: claim the pending command and write its result."""
    for cmd in ipc_dir.glob("autocad_mcp_cmd_*.json"):
        data = json.loads(cmd.read_text(encoding="utf-8"))
        cmd.unlink()
        rid = data["request_id"]
        (ipc_dir / f"autocad_mcp_result_{rid}.json").write_text(
            json.dumps({"request_id": rid, "ok": True, "payload": json.loads(payload)}), encoding="utf-8"
        )


async def test_unclaimed_command_fails_fast_with_hint(backend, monkeypatch):
    monkeypatch.setattr(file_ipc, "AUTOLOAD_DISPATCHER", False)
    posted = []
    monkeypatch.setattr(backend, "_post_text", posted.append)

    start = time.time()
    result = await backend._dispatch("ping", {}, timeout=10.0)

    assert not result.ok
    assert time.time() - start < 2.0  # PICKUP_TIMEOUT, not the 10 s timeout
    assert "mcp_dispatch.lsp" in result.error
    assert posted == []


async def test_autoload_runs_the_same_command_file_once(backend, monkeypatch):
    monkeypatch.setattr(file_ipc, "AUTOLOAD_DISPATCHER", True)
    posted = []

    def fake_post(text):
        posted.append(text)
        _answer_pending(backend._ipc_dir)  # the freshly loaded dispatcher answers

    monkeypatch.setattr(backend, "_post_text", fake_post)

    result = await backend._dispatch("ping", {}, timeout=10.0)

    assert result.ok and result.payload == "pong"
    assert len(posted) == 1
    assert posted[0].startswith("(progn (load ") and posted[0].endswith("(c:mcp-dispatch))")


async def test_claimed_command_is_never_resent(backend, monkeypatch):
    """A slow command (claimed, no result yet) must time out, not trigger autoload."""
    monkeypatch.setattr(file_ipc, "AUTOLOAD_DISPATCHER", True)
    posted = []
    monkeypatch.setattr(backend, "_post_text", posted.append)
    # Claim the file immediately but never answer
    monkeypatch.setattr(
        backend, "_type_dispatch_trigger",
        lambda: [f.unlink() for f in backend._ipc_dir.glob("autocad_mcp_cmd_*.json")],
    )

    result = await backend._dispatch("execute-lisp", {}, timeout=0.8)

    assert not result.ok
    assert "Timeout" in result.error
    assert posted == []
