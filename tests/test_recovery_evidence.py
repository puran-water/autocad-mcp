import base64
import hashlib
import json
import os
from unittest.mock import AsyncMock

import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.evidence import save_screenshot

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=")
ENCODED = base64.b64encode(PNG).decode()


def test_screenshot_saved_and_hashed_without_overwrite(tmp_path):
    target = tmp_path / "batch.png"
    receipt = save_screenshot(ENCODED, target)
    assert target.read_bytes() == PNG
    assert receipt == {"path": str(target), "bytes": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()}
    with pytest.raises(ValueError, match="TARGET_EXISTS"):
        save_screenshot(ENCODED, target)


def test_competing_writer_is_preserved(tmp_path, monkeypatch):
    target = tmp_path / "batch.png"
    link = os.link

    def race(source, destination):
        target.write_bytes(b"other evidence")
        link(source, destination)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(FileExistsError):
        save_screenshot(ENCODED, target)
    assert target.read_bytes() == b"other evidence"
    assert list(tmp_path.iterdir()) == [target]


async def test_desktop_with_pending_never_initializes_backend(tmp_path, monkeypatch):
    import autocad_mcp.config as config
    import autocad_mcp.desktop as desktop
    import autocad_mcp.server as server
    import autocad_mcp.backends.file_ipc as ipc
    from PIL import ImageGrab

    monkeypatch.setattr(config, "detect_backend", lambda: "file_ipc")
    monkeypatch.setattr(config, "IPC_DIR", tmp_path)
    forbidden = AsyncMock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(server, "get_backend", forbidden)
    monkeypatch.setattr(ipc, "find_autocad_window", lambda: 123)
    monkeypatch.setattr(desktop, "observe", lambda hwnd: {"hwnd": hwnd, "enabled": True})
    captures = []
    class DesktopImage:
        def save(self, output, format):
            assert format == 'PNG'
            output.write(PNG)
    def grab(**kwargs):
        captures.append(kwargs)
        return DesktopImage()
    monkeypatch.setattr(ImageGrab, 'grab', grab)
    marker = tmp_path / "pcad-pending.json"
    marker.write_text(json.dumps({"request_id": "pending123"}))
    before = marker.read_bytes()
    result = json.loads(await server.system("desktop", {"screenshot_path": str(tmp_path / "desktop.png")}))
    assert result["ok"] and result["ipc"]["pending"]["request_id"] == "pending123"
    assert result["screenshot"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert result['screenshot']['capture'] == 'desktop_all_screens'
    assert captures == [{'all_screens': True}]
    assert marker.read_bytes() == before
    forbidden.assert_not_awaited()


async def test_acknowledgement_does_not_need_init_but_requires_completion(tmp_path, monkeypatch):
    import autocad_mcp.config as config
    import autocad_mcp.server as server

    monkeypatch.setattr(config, "detect_backend", lambda: "file_ipc")
    monkeypatch.setattr(config, "IPC_DIR", tmp_path)
    forbidden = AsyncMock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(server, "get_backend", forbidden)
    marker = tmp_path / "pcad-pending.json"
    marker.write_text(json.dumps({"request_id": "late123"}))
    refused = json.loads(await server.system("acknowledge", {"request_id": "late123"}))
    assert "MATCHING_COMPLETION_RECEIPT_REQUIRED" in refused["error"] and marker.exists()
    (tmp_path / "autocad_mcp_result_late123.json").write_text(json.dumps({"request_id": "late123", "ok": True}))
    result = json.loads(await server.system("acknowledge", {"request_id": "late123"}))
    assert result["acknowledged"] == "late123" and not marker.exists()
    assert (tmp_path / "pcad-receipts/late123.json").exists()
    forbidden.assert_not_awaited()


async def test_bad_evidence_path_refuses_before_mutation(tmp_path, monkeypatch):
    import autocad_mcp.server as server

    forbidden = AsyncMock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(server, "get_backend", forbidden)
    target = tmp_path / "existing.png"
    target.write_bytes(PNG)
    for operation, request in [("execute_lisp", {"code": "(entdel...)"}), ("revision", {"action": "batch"})]:
        result = json.loads(await server.system(operation, {**request, "screenshot_path": str(target)}))
        assert "SCREENSHOT_TARGET_EXISTS" in result["error"]
    forbidden.assert_not_awaited()


async def test_capture_failure_preserves_mutation_result_and_never_replays(tmp_path, monkeypatch):
    import autocad_mcp.client as client
    import autocad_mcp.server as server

    backend = type("Backend", (), {})()
    backend.execute_lisp = AsyncMock(return_value=CommandResult(ok=True, payload={"handles": ["ABC"]}))
    backend.get_screenshot = AsyncMock(side_effect=RuntimeError("capture unavailable"))
    monkeypatch.setattr(server, "get_backend", AsyncMock(return_value=backend))
    monkeypatch.setattr(client, "get_backend", AsyncMock(return_value=backend))
    result = json.loads(await server.system("execute_lisp", {"code": "bounded batch", "screenshot_path": str(tmp_path / "batch.png")}))
    assert result["ok"] and result["payload"]["handles"] == ["ABC"]
    assert result["screenshot_error"] == "capture unavailable"
    backend.execute_lisp.assert_awaited_once()


async def test_mutation_and_screenshot_share_one_response(tmp_path, monkeypatch):
    import autocad_mcp.client as client
    import autocad_mcp.server as server

    backend = type("Backend", (), {})()
    backend.execute_lisp = AsyncMock(return_value=CommandResult(ok=True, payload={"journal": "complete"}))
    backend.get_screenshot = AsyncMock(return_value=CommandResult(ok=True, payload=ENCODED))
    monkeypatch.setattr(server, "get_backend", AsyncMock(return_value=backend))
    monkeypatch.setattr(client, "get_backend", AsyncMock(return_value=backend))
    result = await server.system("execute_lisp", {"code": "bounded batch", "screenshot_path": str(tmp_path / "batch.png")}, include_screenshot=True)
    assert json.loads(result[0].text)["payload"] == {"journal": "complete"}
    assert json.loads(result[0].text)["screenshot"]["bytes"] == len(PNG)
    assert result[1].type == "image" and (tmp_path / "batch.png").read_bytes() == PNG
    backend.execute_lisp.assert_awaited_once()


def test_timeout_hint_stops_instead_of_esc_retry():
    from autocad_mcp.client import _error
    result = json.loads(_error(RuntimeError("Timeout waiting for result")))
    assert "Stop mutations" in result["hint"] and "Do not retry" in result["hint"]
