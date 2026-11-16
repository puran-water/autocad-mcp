import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.ipc_protocol import publish, read_json
from autocad_mcp.worker import prepare, session_paths
from autocad_mcp.worker_transition import transition_code, transition_locked


@pytest.fixture
def endpoint(tmp_path):
    manifest = prepare(tmp_path, "C:/qualification/source.dwg")
    files = session_paths(tmp_path, manifest["session"])
    publish(files["lock"], manifest)
    state = dict(
        worker_protocol=1,
        build=manifest["build"],
        writestat=1,
        session=manifest["session"],
        sequence=1,
        drawing=manifest["drawing"],
        state="idle",
        dbmod=0,
    )
    publish(files["heartbeat1"], state)
    backend = FileIPCBackend(worker_session=manifest["session"])
    backend._ipc_dir = tmp_path
    return backend, manifest, files, state


@pytest.mark.parametrize("mode,fmt", [("erase", 64), ("save", 999)])
async def test_invalid_transition_leaves_no_intent(endpoint, mode, fmt):
    backend, _, _, _ = endpoint
    with pytest.raises(ValueError):
        await transition_locked(backend, mode, "C:/qualification/next.dwg", file_format=fmt)
    assert not (backend._ipc_dir / "pcad-handoff.json").exists()


async def test_uncertain_handoff_fences_normal_requests(endpoint, monkeypatch):
    backend, manifest, files, state = endpoint

    async def uncertain(*args, **kwargs):
        return CommandResult(ok=False, error="IPC_TIMEOUT_RECONCILE_REQUIRED"), True

    monkeypatch.setattr(backend, "_dispatch_unlocked", uncertain)
    result = await transition_locked(backend, "save", "C:/qualification/next.dwg")
    assert not result.ok and result.error == "IPC_HANDOFF_RECONCILE_REQUIRED"
    assert not files["stop"].exists()  # Never stop an unconfirmed native command.
    assert backend._worker_session == manifest["session"]
    assert (await backend._dispatch("ping", {})).error == "IPC_HANDOFF_RECONCILE_REQUIRED"


async def test_success_requires_old_stopped_new_identity_and_new_lock(endpoint, monkeypatch):
    import autocad_mcp.worker_transition as module

    backend, manifest, files, state = endpoint

    async def queued(*args, **kwargs):
        return CommandResult(ok=True, payload="QUEUED_SUCCESSOR"), True

    monkeypatch.setattr(backend, "_dispatch_unlocked", queued)

    def native_stop(root, parent):
        next_session = read_json(root / "pcad-handoff.json")["successor"]
        next_files = session_paths(root, next_session["session"])
        publish(
            files["heartbeat0"], dict(state, sequence=2, state="stopped", reason="STOP_REQUESTED")
        )
        publish(files["lock"], next_session)
        publish(
            next_files["heartbeat1"],
            dict(state, session=next_session["session"], drawing=next_session["drawing"]),
        )

    monkeypatch.setattr(module, "request_stop", native_stop)
    (backend._ipc_dir / "pcad-receipts").mkdir()
    result = await transition_locked(backend, "save", "C:/qualification/next.dwg")
    assert result.ok
    assert result.payload["drawing"] == "C:/QUALIFICATION/NEXT.DWG"
    assert backend._worker_session != manifest["session"]
    assert not (backend._ipc_dir / "pcad-handoff.json").exists()
    assert len(list((backend._ipc_dir / "pcad-receipts").glob("handoff-*.json"))) == 1


async def test_missing_successor_retains_handoff_for_reconciliation(endpoint, monkeypatch):
    backend, manifest, files, state = endpoint

    async def queued(*args, **kwargs):
        return CommandResult(ok=True, payload="QUEUED_SUCCESSOR"), True

    monkeypatch.setattr(backend, "_dispatch_unlocked", queued)
    result = await transition_locked(backend, "save", "C:/qualification/next.dwg", timeout=0.01)
    assert not result.ok and files["stop"].exists()
    assert (backend._ipc_dir / "pcad-handoff.json").exists()
    assert backend._worker_session == manifest["session"]


def test_bootstrap_restores_security_before_entering_worker_and_quotes_paths():
    code = transition_code(
        "save", "C:/with spaces/next.dwg", {"session": "a" * 32}, "C:/worker libs"
    )
    assert "(vla-PostCommand pcadTarget " in code
    assert "SendInput" not in code and "PostMessage" not in code
    assert code.index('(setvar \\"SECURELOAD\\" pcadSecurity)') < code.index("(mcp-worker-run ")
    assert "C:/WITH SPACES/NEXT.DWG" in code
    assert "vla-Open" not in code
    assert "(setq pcadTarget nil)" not in code
    assert "vla-PostCommand pcadFound" not in code
    assert "vla-SaveAs" in code


async def test_open_is_disabled_before_any_native_work_or_session_creation(endpoint):
    backend, manifest, files, state = endpoint
    before = {p.name: p.read_bytes() for p in backend._ipc_dir.iterdir()}
    result = await backend.drawing_open("C:/qualification/next.dwg")
    assert not result.ok and "OPEN_NOT_QUALIFIED" in result.error
    assert before == {p.name: p.read_bytes() for p in backend._ipc_dir.iterdir()}
