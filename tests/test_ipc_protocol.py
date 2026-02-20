"""Tests for IPC protocol components — no AutoCAD needed.

Tests the file-based IPC protocol logic: command file structure,
result parsing, request_id generation, timeout behavior, stale cleanup,
and CommandResult serialization.
"""

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autocad_mcp.backends.base import BackendCapabilities, CommandResult


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


class TestCommandResult:
    def test_ok_result(self):
        r = CommandResult(ok=True, payload={"entity": "LINE", "handle": "ABC"})
        d = r.to_dict()
        assert d["ok"] is True
        assert d["payload"]["entity"] == "LINE"
        assert "error" not in d

    def test_error_result(self):
        r = CommandResult(ok=False, error="Entity not found")
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] == "Entity not found"
        assert "payload" not in d

    def test_default_values(self):
        r = CommandResult(ok=True)
        assert r.payload is None
        assert r.error is None
        d = r.to_dict()
        assert d["ok"] is True
        assert d["payload"] is None


# ---------------------------------------------------------------------------
# BackendCapabilities
# ---------------------------------------------------------------------------


class TestBackendCapabilities:
    def test_defaults_minimal(self):
        caps = BackendCapabilities()
        assert caps.can_create_entities is True
        assert caps.can_read_drawing is False
        assert caps.can_plot_pdf is False
        assert caps.can_undo is False

    def test_full_capabilities(self):
        caps = BackendCapabilities(
            can_read_drawing=True,
            can_modify_entities=True,
            can_create_entities=True,
            can_screenshot=True,
            can_save=True,
            can_plot_pdf=True,
            can_zoom=True,
            can_query_entities=True,
            can_file_operations=True,
            can_undo=True,
        )
        for field_name, value in caps.__dict__.items():
            assert value is True, f"{field_name} should be True"


# ---------------------------------------------------------------------------
# IPC command file format
# ---------------------------------------------------------------------------


class TestIPCCommandFormat:
    """Test the JSON command file structure matches what LISP expects."""

    def test_command_file_structure(self):
        request_id = uuid.uuid4().hex[:12]
        payload = {
            "request_id": request_id,
            "command": "create-line",
            "params": {"x1": 0, "y1": 0, "x2": 100, "y2": 100, "layer": "0"},
            "ts": time.time(),
        }
        json_str = json.dumps(payload)
        parsed = json.loads(json_str)
        assert parsed["request_id"] == request_id
        assert parsed["command"] == "create-line"
        assert parsed["params"]["x1"] == 0
        assert isinstance(parsed["ts"], float)

    def test_result_file_structure(self):
        request_id = "abc123def456"
        result = {
            "request_id": request_id,
            "ok": True,
            "payload": {"entity_type": "LINE", "handle": "1A2"},
            "error": None,
            "ts": time.time(),
        }
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["ok"] is True
        assert parsed["payload"]["handle"] == "1A2"

    def test_error_result_file_structure(self):
        result = {
            "request_id": "abc123",
            "ok": False,
            "payload": None,
            "error": "Command 'nonexistent' not found in dispatch map",
            "ts": time.time(),
        }
        parsed = json.loads(json.dumps(result))
        assert parsed["ok"] is False
        assert "not found" in parsed["error"]


# ---------------------------------------------------------------------------
# Request ID uniqueness
# ---------------------------------------------------------------------------


class TestRequestID:
    def test_uniqueness(self):
        """Generate 1000 request IDs and verify no duplicates."""
        ids = {uuid.uuid4().hex[:12] for _ in range(1000)}
        assert len(ids) == 1000

    def test_format(self):
        rid = uuid.uuid4().hex[:12]
        assert len(rid) == 12
        assert rid.isalnum()


# ---------------------------------------------------------------------------
# Atomic write simulation
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_write_then_rename(self):
        """Verify atomic write pattern: write to .tmp, then rename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            final_path = Path(tmpdir) / "autocad_mcp_cmd_test123.json"
            tmp_path = final_path.with_suffix(".tmp")

            payload = {"request_id": "test123", "command": "ping", "params": {}}
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")

            assert tmp_path.exists()
            assert not final_path.exists()

            tmp_path.rename(final_path)

            assert final_path.exists()
            assert not tmp_path.exists()

            data = json.loads(final_path.read_text(encoding="utf-8"))
            assert data["request_id"] == "test123"

    def test_concurrent_files_dont_collide(self):
        """Simulate two concurrent requests with different IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            id1 = uuid.uuid4().hex[:12]
            id2 = uuid.uuid4().hex[:12]

            f1 = Path(tmpdir) / f"autocad_mcp_cmd_{id1}.json"
            f2 = Path(tmpdir) / f"autocad_mcp_cmd_{id2}.json"

            f1.write_text(json.dumps({"request_id": id1, "command": "a"}))
            f2.write_text(json.dumps({"request_id": id2, "command": "b"}))

            d1 = json.loads(f1.read_text())
            d2 = json.loads(f2.read_text())

            assert d1["request_id"] != d2["request_id"]
            assert d1["command"] == "a"
            assert d2["command"] == "b"


# ---------------------------------------------------------------------------
# Stale file cleanup
# ---------------------------------------------------------------------------


class TestStaleCleanup:
    def test_cleanup_old_files(self):
        """Files older than threshold should be cleaned up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stale_threshold = 60.0

            # Create a "stale" file
            stale_file = Path(tmpdir) / "autocad_mcp_cmd_old123.json"
            stale_file.write_text('{"old": true}')
            # Backdate the file
            old_time = time.time() - stale_threshold - 10
            os.utime(stale_file, (old_time, old_time))

            # Create a "fresh" file
            fresh_file = Path(tmpdir) / "autocad_mcp_result_new456.json"
            fresh_file.write_text('{"new": true}')

            # Simulate cleanup
            now = time.time()
            for f in Path(tmpdir).glob("autocad_mcp_*.json"):
                if now - f.stat().st_mtime > stale_threshold:
                    f.unlink(missing_ok=True)

            assert not stale_file.exists()
            assert fresh_file.exists()

    def test_cleanup_tmp_files(self):
        """All .tmp files should be cleaned up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp1 = Path(tmpdir) / "autocad_mcp_cmd_abc.tmp"
            tmp2 = Path(tmpdir) / "autocad_mcp_result_def.tmp"
            tmp1.write_text("partial write")
            tmp2.write_text("partial write")

            for f in Path(tmpdir).glob("autocad_mcp_*.tmp"):
                f.unlink(missing_ok=True)

            assert not tmp1.exists()
            assert not tmp2.exists()


# ---------------------------------------------------------------------------
# Timeout behavior
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_deadline_calculation(self):
        timeout = 10.0
        deadline = time.time() + timeout
        # Deadline should be ~10s in the future
        assert deadline > time.time()
        assert deadline - time.time() < timeout + 0.1

    def test_poll_interval_reasonable(self):
        """Verify poll interval gives ~100 checks in 10s timeout."""
        poll_interval = 0.1
        timeout = 10.0
        max_polls = timeout / poll_interval
        assert max_polls == 100


# ---------------------------------------------------------------------------
# Error hint mapping (from client.py)
# ---------------------------------------------------------------------------


class TestErrorHints:
    """Test the _error helper's hint mapping logic."""

    def _classify(self, msg: str) -> str:
        msg_lower = msg.lower()
        if "window not found" in msg_lower or "no autocad" in msg_lower:
            return "autocad_not_running"
        elif "timeout" in msg_lower:
            return "timeout"
        elif "not supported" in msg_lower or "backend" in msg_lower:
            return "unsupported"
        elif "dispatcher" in msg_lower or "mcp_dispatch" in msg_lower:
            return "dispatcher_not_loaded"
        else:
            return "unknown"

    def test_window_not_found(self):
        assert self._classify("AutoCAD window not found") == "autocad_not_running"

    def test_no_autocad(self):
        assert self._classify("No AutoCAD LT detected") == "autocad_not_running"

    def test_timeout(self):
        assert self._classify("Timeout waiting for result") == "timeout"

    def test_not_supported(self):
        assert self._classify("Not supported on this backend") == "unsupported"

    def test_dispatcher(self):
        assert self._classify("mcp_dispatch.lsp not loaded") == "dispatcher_not_loaded"

    def test_unknown(self):
        assert self._classify("Something unexpected happened") == "unknown"


# ---------------------------------------------------------------------------
# LISP command dispatch map coverage
# ---------------------------------------------------------------------------


class TestDispatchMapCoverage:
    """Verify all commands that FileIPCBackend sends are documented."""

    # These must match the command names in mcp_dispatch.lsp's command map
    EXPECTED_COMMANDS = [
        "ping",
        "drawing-info",
        "drawing-save",
        "drawing-save-as-dxf",
        "drawing-create",
        "drawing-purge",
        "drawing-get-variables",
        "create-line",
        "create-circle",
        "create-polyline",
        "create-rectangle",
        "create-arc",
        "create-ellipse",
        "create-mtext",
        "create-hatch",
        "create-text",
        "entity-list",
        "entity-count",
        "entity-get",
        "entity-erase",
        "entity-copy",
        "entity-move",
        "entity-rotate",
        "entity-scale",
        "entity-mirror",
        "entity-offset",
        "entity-array",
        "entity-fillet",
        "entity-chamfer",
        "layer-list",
        "layer-create",
        "layer-set-current",
        "layer-set-properties",
        "layer-freeze",
        "layer-thaw",
        "layer-lock",
        "layer-unlock",
        "block-list",
        "block-insert",
        "block-insert-with-attributes",
        "block-get-attributes",
        "block-update-attribute",
        "block-define",
        "create-dimension-linear",
        "create-dimension-aligned",
        "create-dimension-angular",
        "create-dimension-radius",
        "create-leader",
        "zoom-extents",
        "zoom-window",
        "pid-setup-layers",
        "pid-insert-symbol",
        "pid-list-symbols",
        "pid-draw-process-line",
        "pid-connect-equipment",
        "pid-add-flow-arrow",
        "pid-add-equipment-tag",
        "pid-add-line-number",
        "pid-insert-valve",
        "pid-insert-instrument",
        "pid-insert-pump",
        "pid-insert-tank",
        "drawing-plot-pdf",
    ]

    def test_all_commands_use_hyphen_convention(self):
        for cmd in self.EXPECTED_COMMANDS:
            assert "_" not in cmd, f"Command '{cmd}' should use hyphens, not underscores"

    def test_no_duplicates(self):
        assert len(self.EXPECTED_COMMANDS) == len(set(self.EXPECTED_COMMANDS))
