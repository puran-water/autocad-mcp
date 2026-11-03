"""Tool output shape and result parsing — no AutoCAD needed."""

import json
from pathlib import Path

import pytest

from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.config import LISP_DIR


@pytest.mark.asyncio
async def test_tools_do_not_emit_structured_output():
    """Union return types made FastMCP serialize results (incl. base64 images) a second time."""
    from autocad_mcp.server import mcp

    tools = await mcp.list_tools()
    assert tools
    for tool in tools:
        assert tool.outputSchema is None, f"{tool.name} duplicates its content as structured output"


@pytest.mark.asyncio
async def test_result_with_raw_newline_is_parsed(tmp_path: Path):
    """A multi-line LISP result must not make the poll loop wait for the timeout."""
    backend = FileIPCBackend()
    backend._ipc_dir = tmp_path

    def trigger():
        cmd = json.loads(next(tmp_path.glob("autocad_mcp_cmd_*.json")).read_text(encoding="utf-8"))
        rid = cmd["request_id"]
        (tmp_path / f"autocad_mcp_result_{rid}.json").write_text(
            '{"request_id":"%s","ok":false,"error":"line one\nline two"}' % rid, encoding="utf-8"
        )

    backend._type_dispatch_trigger = trigger
    result = await backend._dispatch("ping", {})
    assert result.ok is False
    assert result.error == "line one\nline two"


def test_lisp_escapes_control_characters():
    """Smoke check only: AutoLISP cannot run offline, so verify the escape branches exist."""
    lisp = (LISP_DIR / "mcp_dispatch.lsp").read_text(encoding="utf-8")
    for needle in ('((= ch "\\n")', '((= ch "\\r")', '((= ch "\\t")'):
        assert needle in lisp
