"""Worker session failures and v2 transport integration; no native claims."""

import asyncio
import os
import time

import pytest

from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.desktop import acknowledge, inspect_pending
from autocad_mcp.ipc_protocol import paths, publish, read_json
from autocad_mcp.worker import prepare, observe, preflight, request_stop, session_paths

DRAWING = "C:/qualification/copy.dwg"


@pytest.fixture
def live(tmp_path):
    manifest = prepare(tmp_path, DRAWING, idle_timeout_s=30, max_duration_s=60)
    files = session_paths(tmp_path, manifest["session"])
    publish(files["lock"], manifest)
    state = dict(
        worker_protocol=1,
        build=manifest["build"],
        writestat=1,
        session=manifest["session"],
        sequence=1,
        state="idle",
        drawing=DRAWING,
        cmdactive=0,
        dbmod=0,
        request_id="",
    )
    publish(files["heartbeat1"], state)
    return tmp_path, manifest, files, state


def test_session_is_bound_to_normalized_document_and_has_unique_token(tmp_path):
    a = prepare(tmp_path, DRAWING)
    b = prepare(tmp_path, DRAWING.replace("/", "\\"))
    assert a["drawing"] == b["drawing"] == DRAWING.upper()
    assert a["session"] != b["session"]


@pytest.mark.parametrize(
    "path", ["relative.dwg", "/tmp/copy.dwg", "C:/copy.dxf", "C:/a\n.dwg", None]
)
def test_invalid_drawing_cannot_prepare(tmp_path, path):
    with pytest.raises(ValueError):
        prepare(tmp_path, path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "name", ["pcad-pending.json", "pcad-worker.lock.json", "pcad-handoff.json"]
)
def test_existing_intent_is_never_replaced(tmp_path, name):
    p = tmp_path / name
    p.write_text("owned elsewhere")
    with pytest.raises(RuntimeError, match="RECONCILE"):
        prepare(tmp_path, DRAWING)
    assert p.read_text() == "owned elsewhere"


def test_one_heartbeat_slot_can_be_incomplete_without_losing_valid_snapshot(live):
    root, manifest, files, state = live
    files["heartbeat0"].write_text("{")
    assert preflight(root, manifest["session"])["sequence"] == 1
    publish(files["heartbeat0"], dict(state, sequence=2, state="stopped"))
    with pytest.raises(RuntimeError, match="NOT_READY: stopped"):
        preflight(root, manifest["session"])


def test_old_heartbeat_cannot_authorize_request(live):
    root, manifest, files, state = live
    os.utime(files["heartbeat1"], (time.time() - 10, time.time() - 10))
    with pytest.raises(RuntimeError, match="NOT_READY"):
        preflight(root, manifest["session"])


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"drawing": "C:/someone/other.dwg"}, "DRAWING_CHANGED"),
        ({"state": "running"}, "NOT_READY"),
    ],
)
def test_drawing_switch_and_busy_worker_fail_before_publication(live, change, reason):
    root, manifest, files, state = live
    publish(files["heartbeat0"], dict(state, sequence=2, **change))
    with pytest.raises(RuntimeError, match=reason):
        preflight(root, manifest["session"])
    assert not (root / "pcad-pending.json").exists()


def test_another_session_cannot_use_lock_or_heartbeat(live):
    root, manifest, files, state = live
    publish(files["lock"], dict(manifest, session="f" * 32))
    with pytest.raises(RuntimeError, match="LOCK_MISMATCH"):
        preflight(root, manifest["session"])


def test_stop_request_does_not_claim_worker_stopped_or_clear_intent(live):
    root, manifest, files, state = live
    publish(root / "pcad-pending.json", {"claim": "must retain"})
    assert request_stop(root, manifest["session"])["stop_requested"]
    assert observe(root, manifest["session"])["state"] == "idle"
    assert files["lock"].exists() and (root / "pcad-pending.json").exists()


async def test_worker_dispatch_has_no_keyboard_path_and_retains_claim_proof(live, monkeypatch):
    root, manifest, files, state = live
    b = FileIPCBackend(worker_session=manifest["session"])
    b._ipc_dir = root

    def forbidden(*args):
        pytest.fail("keyboard fallback attempted")

    monkeypatch.setattr(b, "_post_text", forbidden)

    async def native():
        while not list(root.glob("pcad-v3-*.ready.json")):
            await asyncio.sleep(0.001)
        ready = next(root.glob("pcad-v3-*.ready.json"))
        data = read_json(ready)
        assert data["worker_session"] == manifest["session"]
        assert data["expected_drawing"] == manifest["drawing"]
        f = paths(root, data["request_id"])
        ready.rename(f["claimed"])
        publish(f["result"], dict(request_id=data["request_id"], ok=True, payload="observed"))

    result, _ = await asyncio.gather(b._dispatch("ping", {}), native())
    assert result.ok and result.payload == "observed"
    assert not inspect_pending(root)["pending"]
    assert len(list(root.glob("*.claimed.json"))) == 1


async def test_missing_worker_does_not_publish_or_fall_back(tmp_path, monkeypatch):
    session = prepare(tmp_path, DRAWING)["session"]
    b = FileIPCBackend(worker_session=session)
    b._ipc_dir = tmp_path
    monkeypatch.setattr(b, "_post_text", lambda *args: pytest.fail("keyboard fallback"))
    result = await b._dispatch("ping", {})
    assert not result.ok and "NOT_RUNNING" in result.error
    assert not (tmp_path / "pcad-pending.json").exists()
    assert not list(tmp_path.glob("*.ready.json"))


async def test_worker_disappears_after_preflight_cancel_is_fenced(live, monkeypatch):
    import autocad_mcp.backends.file_ipc as module

    root, manifest, files, state = live
    monkeypatch.setattr(module, "PICKUP_TIMEOUT", 0.02)
    monkeypatch.setattr(module, "POLL_INTERVAL", 0.001)
    b = FileIPCBackend(worker_session=manifest["session"])
    b._ipc_dir = root
    monkeypatch.setattr(b, "_post_text", lambda *args: pytest.fail("keyboard fallback"))
    result = await b._dispatch("ping", {})
    assert not result.ok and "CANCELLED" in result.error
    assert inspect_pending(root)["cancelled_before_claim"]
    assert (await b._dispatch("ping", {})).error == "IPC_UNCERTAIN_RECONCILE_REQUIRED"
    acknowledge(root, result.payload["request_id"])


async def test_result_before_idle_does_not_publish_next_request_early(live, monkeypatch):
    root, manifest, files, state = live
    publish(files["heartbeat0"], dict(state, state="running", sequence=2))
    b = FileIPCBackend(worker_session=manifest["session"])
    b._ipc_dir = root
    monkeypatch.setattr(b, "_post_text", lambda *args: pytest.fail("keyboard fallback"))

    async def native():
        await asyncio.sleep(0.08)
        assert not (root / "pcad-pending.json").exists()
        assert not list(root.glob("*.ready.json"))
        publish(files["heartbeat1"], dict(state, sequence=3))
        while not list(root.glob("*.ready.json")):
            await asyncio.sleep(0.001)
        ready = next(root.glob("*.ready.json"))
        data = read_json(ready)
        f = paths(root, data["request_id"])
        ready.rename(f["claimed"])
        publish(f["result"], {"request_id": data["request_id"], "ok": True, "payload": "ready"})

    result, _ = await asyncio.gather(b._dispatch("ping", {}), native())
    assert result.ok


def test_successor_preparation_does_not_replace_live_binding(live):
    from autocad_mcp.worker import prepare_successor

    root, manifest, files, state = live
    successor = prepare_successor(root, manifest["session"], "C:/qualification/next.dwg")
    assert successor["parent_session"] == manifest["session"]
    assert successor["session"] != manifest["session"]
    assert read_json(files["lock"]) == manifest
    assert not files["stop"].exists()


def test_successor_refuses_pending_request(live):
    from autocad_mcp.worker import prepare_successor

    root, manifest, files, state = live
    publish(root / "pcad-pending.json", {"request_id": "must preserve"})
    with pytest.raises(RuntimeError, match="RECONCILE"):
        prepare_successor(root, manifest["session"], DRAWING)


async def test_default_client_discovers_existing_worker_after_server_restart(live, monkeypatch):
    from autocad_mcp.backends.base import CommandResult

    root, manifest, files, state = live
    b = FileIPCBackend()
    b._ipc_dir = root

    async def received(command, params, timeout):
        assert b._worker_session == manifest["session"]
        assert b._worker_state["drawing"] == DRAWING
        return CommandResult(ok=True, payload="attached"), True

    monkeypatch.setattr(b, "_dispatch_unlocked", received)
    monkeypatch.setattr(b, "_post_text", lambda *args: pytest.fail("keyboard fallback"))
    assert (await b._dispatch("ping", {})).ok


async def test_default_client_missing_worker_publishes_nothing(tmp_path, monkeypatch):
    b = FileIPCBackend()
    b._ipc_dir = tmp_path
    monkeypatch.setattr(b, "_post_text", lambda *args: pytest.fail("keyboard fallback"))
    result = await b._dispatch("ping", {})
    assert not result.ok and "WORKER_NOT_RUNNING" in result.error
    assert not list(tmp_path.glob("*.ready.json"))
    assert not (tmp_path / "pcad-pending.json").exists()


async def test_save_wrong_expected_source_is_rejected_before_intent(live):
    root, manifest, files, state = live
    b = FileIPCBackend()
    b._ipc_dir = root
    result = await b.worker_transition(
        "save",
        "C:/qualification/checkpoint.dwg",
        expected_drawing="C:/wrong/source.dwg",
        file_format=64,
    )
    assert result.error == "WORKER_TRANSITION_SOURCE_MISMATCH"
    assert not (root / "pcad-handoff.json").exists()
    assert len(list(root.glob("*.session.json"))) == 1


def test_lifecycle_prepare_is_file_only_and_preserves_fences(tmp_path):
    from autocad_mcp.worker_control import control

    result = control(tmp_path, "prepare", {"drawing": DRAWING}, "C:/tools/lisp-code")
    from pathlib import Path

    assert Path(result["script"]).exists()
    assert not (tmp_path / "pcad-worker.lock.json").exists()
    assert not (tmp_path / "pcad-pending.json").exists()
    publish(tmp_path / "pcad-handoff.json", {"state": "uncertain"})
    with pytest.raises(RuntimeError, match="RECONCILE"):
        control(tmp_path, "prepare", {"drawing": DRAWING}, "C:/tools/lisp-code")
    seen = control(tmp_path, "observe", {}, "C:/tools/lisp-code")
    assert seen["handoff"]["state"] == "uncertain"
    assert seen["worker"]["state"] == "absent"
