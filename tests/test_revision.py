import json
import math
import pytest
from autocad_mcp.revision import literal, compile_call, RevisionError, unpack
from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.desktop import ipc_lease, acknowledge


def test_compile_is_data_and_requires_preconditions():
    request = {
        "drawing": "C:/scratch/a.dwg",
        "id": "b",
        "journal": "C:/scratch/b.json",
        "operations": [
            {
                "kind": "text",
                "item": 1,
                "handle": "abcd",
                "expect": {"0": "TEXT"},
                "expected_text": "old",
                "text": '") (exit) ;',
            }
        ],
    }
    code = compile_call("batch", request, lisp_path="C:/tools/revision_tools.lsp")
    assert '\\"' in code and "pcad:batch" in code and '"ABCD"' in code
    request["operations"][0]["expect"] = {}
    with pytest.raises(RevisionError, match="EXPECTED_TYPE"):
        compile_call("batch", request)
    with pytest.raises(RevisionError, match="NONFINITE"):
        literal(math.inf)


def test_nested_error_never_reports_success():
    result = unpack(CommandResult(ok=True, payload='{"ok":false,"error":"WRONG_DOCUMENT"}'))
    assert not result.ok and result.error == "WRONG_DOCUMENT"


def test_cross_process_lock_refuses_second_handle(tmp_path):
    with ipc_lease(tmp_path):
        with pytest.raises(RuntimeError, match="BUSY"):
            with ipc_lease(tmp_path):
                pass
    with ipc_lease(tmp_path):
        pass


async def test_timeout_blocks_next_dispatch_until_matching_receipt(tmp_path, monkeypatch):
    backend = FileIPCBackend()
    backend._ipc_dir = tmp_path
    sent = []

    def claim():
        sent.append(1)
        for f in tmp_path.glob("autocad_mcp_cmd_*.json"):
            f.unlink()

    monkeypatch.setattr(backend, "_type_dispatch_trigger", claim)
    first = await backend._dispatch("execute-lisp", {}, timeout=0.01)
    assert not first.ok
    pending = json.loads((tmp_path / "pcad-pending.json").read_text())
    other = FileIPCBackend()
    other._ipc_dir = tmp_path
    second = await other._dispatch("ping", {})
    assert second.error == "IPC_UNCERTAIN_RECONCILE_REQUIRED" and sent == [1]
    with pytest.raises(ValueError):
        acknowledge(tmp_path, pending["request_id"])
    receipt = tmp_path / f"autocad_mcp_result_{pending['request_id']}.json"
    receipt.write_text(
        json.dumps({"request_id": pending["request_id"], "ok": True, "payload": "done"})
    )
    acknowledge(tmp_path, pending["request_id"])
    assert not (tmp_path / "pcad-pending.json").exists()
    assert (tmp_path / "pcad-receipts" / receipt.name.replace("autocad_mcp_result_", "")).exists()
