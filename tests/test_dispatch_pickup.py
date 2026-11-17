"""File IPC v3 loss, race, late-result and isolation tests, without AutoCAD."""

import asyncio
import time

import pytest

import autocad_mcp.backends.file_ipc as file_ipc
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.desktop import acknowledge, inspect_pending
from autocad_mcp.ipc_protocol import cancel_unclaimed, move_unclaimed, paths, publish, read_json


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ipc, "PICKUP_TIMEOUT", 0.03)
    monkeypatch.setattr(file_ipc, "POLL_INTERVAL", 0.002)
    b = FileIPCBackend(transport="legacy")
    b._ipc_dir = tmp_path
    monkeypatch.setattr(b, "_type_dispatch_trigger", lambda rid: None)
    return b


def claim(directory, rid, answer=True):
    files = paths(directory, rid)
    if not move_unclaimed(directory, rid, "claimed"):
        return False
    if answer:
        publish(files["result"], {"request_id": rid, "ok": True, "payload": "pong"})
    return True


async def test_unclaimed_is_cancelled_and_fenced_until_ack(backend):
    start = time.monotonic()
    result = await backend._dispatch("ping", {}, timeout=10)
    assert not result.ok and "CANCELLED" in result.error
    assert time.monotonic() - start < 1
    rid = result.payload["request_id"]
    assert inspect_pending(backend._ipc_dir)["cancelled_before_claim"]
    assert not claim(backend._ipc_dir, rid)  # delayed trigger cannot run it
    assert (await backend._dispatch("ping", {})).error == "IPC_UNCERTAIN_RECONCILE_REQUIRED"
    acknowledge(backend._ipc_dir, rid)
    assert not inspect_pending(backend._ipc_dir)["pending"]
    assert paths(backend._ipc_dir, rid)["cancelled"].exists()


async def test_claimed_request_never_replayed_and_late_result_preserved(backend, monkeypatch):
    calls = []

    def trigger(rid):
        calls.append(rid)
        claim(backend._ipc_dir, rid, answer=False)

    monkeypatch.setattr(backend, "_type_dispatch_trigger", trigger)
    result = await backend._dispatch("execute-lisp", {}, timeout=0.06)
    assert not result.ok and "TIMEOUT" in result.error and len(calls) == 1
    rid = calls[0]
    assert paths(backend._ipc_dir, rid)["claimed"].exists()
    with pytest.raises(ValueError):
        acknowledge(backend._ipc_dir, rid)
    publish(
        paths(backend._ipc_dir, rid)["result"], {"request_id": rid, "ok": True, "payload": "done"}
    )
    assert inspect_pending(backend._ipc_dir)["receipt"]["payload"] == "done"
    acknowledge(backend._ipc_dir, rid)


async def test_success_retains_original_claim_and_result(backend, monkeypatch):
    monkeypatch.setattr(backend, "_type_dispatch_trigger", lambda rid: claim(backend._ipc_dir, rid))
    result = await backend._dispatch("ping", {})
    assert result.ok and result.payload == "pong"
    assert not inspect_pending(backend._ipc_dir)["pending"]
    assert len(list(backend._ipc_dir.glob("*.claimed.json"))) == 1
    assert len(list(backend._ipc_dir.glob("*.result.json"))) == 1


async def test_partial_delivery_cancels_without_retry(backend, monkeypatch):
    calls = []

    def fail(rid):
        calls.append(rid)
        raise RuntimeError("DELIVERY_FAILED after 4 characters")

    monkeypatch.setattr(backend, "_type_dispatch_trigger", fail)
    result = await backend._dispatch("execute-lisp", {})
    assert not result.ok and len(calls) == 1
    assert result.payload["state"] == "cancelled"
    assert inspect_pending(backend._ipc_dir)["cancelled_before_claim"]


async def test_delivery_error_after_claim_keeps_uncertainty(backend, monkeypatch):
    def fail(rid):
        claim(backend._ipc_dir, rid, False)
        raise RuntimeError("delivery failure")

    monkeypatch.setattr(backend, "_type_dispatch_trigger", fail)
    result = await backend._dispatch("execute-lisp", {})
    assert result.payload["state"] == "uncertain"
    with pytest.raises(ValueError):
        acknowledge(backend._ipc_dir, result.payload["request_id"])


async def test_old_trigger_cannot_claim_next_request(backend, monkeypatch):
    first = await backend._dispatch("ping", {})
    old = first.payload["request_id"]
    acknowledge(backend._ipc_dir, old)

    def new(rid):
        assert not claim(backend._ipc_dir, old)
        assert claim(backend._ipc_dir, rid)

    monkeypatch.setattr(backend, "_type_dispatch_trigger", new)
    assert (await backend._dispatch("ping", {})).ok


async def test_missing_ready_without_claim_is_not_proof(backend, monkeypatch):
    def disappear(rid):
        paths(backend._ipc_dir, rid)["ready"].unlink()

    monkeypatch.setattr(backend, "_type_dispatch_trigger", disappear)
    result = await backend._dispatch("ping", {})
    assert result.payload["state"] == "uncertain"
    assert not inspect_pending(backend._ipc_dir)["cancelled_before_claim"]


async def test_result_without_claim_is_rejected(backend, monkeypatch):
    def result_only(rid):
        publish(paths(backend._ipc_dir, rid)["result"], {"request_id": rid, "ok": True})

    monkeypatch.setattr(backend, "_type_dispatch_trigger", result_only)
    assert (await backend._dispatch("ping", {})).error == "IPC_CLAIM_PROOF_MISSING"


async def test_cancel_claim_race_has_one_winner(tmp_path):
    # Two competing exclusive decisions, repeated with both arrival orders.
    for i in range(80):
        rid = f"{i:012x}"
        publish(paths(tmp_path, rid)["ready"], {"request_id": rid})
        operations = [lambda: claim(tmp_path, rid, False), lambda: cancel_unclaimed(tmp_path, rid)]
        if i % 2:
            operations.reverse()
        results = await asyncio.gather(*(asyncio.to_thread(op) for op in operations))
        assert sum(results) == 1
        files = paths(tmp_path, rid)
        assert files["claimed"].exists() != files["cancelled"].exists()
        assert files["decision"].is_dir()


def test_failed_move_keeps_fence_and_never_proves_cancellation(tmp_path, monkeypatch):
    from pathlib import Path
    from autocad_mcp.ipc_protocol import PROTOCOL, cancellation_proof

    rid = "0123456789ab"
    pending = {"protocol": PROTOCOL, "request_id": rid}
    files = paths(tmp_path, rid)
    publish(files["ready"], pending)

    def fail_move(*args):
        raise OSError("simulated failure after exclusive mkdir")

    monkeypatch.setattr(Path, "rename", fail_move)
    assert not cancel_unclaimed(tmp_path, rid)
    assert files["decision"].is_dir() and files["ready"].exists()
    assert not move_unclaimed(tmp_path, rid, "claimed")
    assert not cancellation_proof(tmp_path, pending)


def test_cancelled_envelope_without_fence_is_not_proof(tmp_path):
    from autocad_mcp.ipc_protocol import PROTOCOL, cancellation_proof

    pending = {"protocol": PROTOCOL, "request_id": "0123456789ab"}
    publish(paths(tmp_path, pending["request_id"])["cancelled"], pending)
    assert not cancellation_proof(tmp_path, pending)


def test_old_namespace_is_not_consumed(tmp_path):
    rid = "0123456789ab"
    old = tmp_path / f"pcad-v2-{rid}.ready.json"
    publish(old, {"protocol": 2, "request_id": rid})
    assert not cancel_unclaimed(tmp_path, rid)
    assert not move_unclaimed(tmp_path, rid, "claimed")
    assert old.exists()


async def test_cancellation_proof_rejects_altered_envelope(backend):
    result = await backend._dispatch("ping", {})
    f = paths(backend._ipc_dir, result.payload["request_id"])["cancelled"]
    data = read_json(f)
    data["command"] = "erase"
    publish(f, data)
    assert not inspect_pending(backend._ipc_dir)["cancelled_before_claim"]
    with pytest.raises(ValueError):
        acknowledge(backend._ipc_dir, result.payload["request_id"])


def test_trigger_loads_per_document_before_specific_dispatch(backend, monkeypatch):
    posted = []
    # Restore actual trigger while keeping Win32 absent.
    monkeypatch.setattr(backend, "_post_text", posted.append)
    FileIPCBackend._type_dispatch_trigger(backend, "123456789abc")
    assert len(posted) == 1 and "*mcp-ipc-protocol* 3" in posted[0]
    assert '(mcp-dispatch-v3 "123456789abc")' in posted[0]
    assert "(c:mcp-dispatch)" not in posted[0]


async def test_cancelled_python_task_keeps_intent_for_late_native_claim(backend, monkeypatch):
    monkeypatch.setattr(file_ipc, 'PICKUP_TIMEOUT', 10)
    sent = []
    monkeypatch.setattr(backend, '_type_dispatch_trigger', sent.append)
    task = asyncio.create_task(backend._dispatch('execute-lisp', {}, timeout=10))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(sent) == 1
    assert inspect_pending(backend._ipc_dir)['pending']['request_id'] == sent[0]
    assert claim(backend._ipc_dir, sent[0])
    assert (await backend._dispatch('ping', {})).error == 'IPC_UNCERTAIN_RECONCILE_REQUIRED'
    acknowledge(backend._ipc_dir, sent[0])


async def test_completion_without_matching_claim_cannot_be_acknowledged(backend, monkeypatch):
    def forged(rid):
        publish(paths(backend._ipc_dir, rid)['result'], {'request_id': rid, 'ok': True})
    monkeypatch.setattr(backend, '_type_dispatch_trigger', forged)
    result = await backend._dispatch('ping', {})
    with pytest.raises(ValueError):
        acknowledge(backend._ipc_dir, result.payload['request_id'])


def test_disabled_autoload_stays_disabled(backend, monkeypatch):
    monkeypatch.setattr(file_ipc, 'AUTOLOAD_DISPATCHER', False)
    posted = []
    monkeypatch.setattr(backend, '_post_text', posted.append)
    FileIPCBackend._type_dispatch_trigger(backend, '123456789abc')
    assert posted == ['(mcp-dispatch-v3 "123456789abc")']
