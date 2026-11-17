"""Compiler/transport coverage; native resource checks live in native/resources.lsp."""

from unittest.mock import AsyncMock

import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.lisp_preflight import validate
from autocad_mcp.revision import RevisionError, compile_call, execute


def test_preflight_requires_document_identity():
    with pytest.raises(RevisionError, match="EXPECTED_WINDOWS_DOCUMENT_PATH_REQUIRED"):
        compile_call("preflight", {})
    code = compile_call("preflight", {"drawing": 'C:/work/a"b.dwg'})
    validate(code)
    assert "pcad:resource-preflight" in code
    assert 'a\\"b.dwg' in code


async def test_preflight_preserves_native_resource_evidence():
    backend = AsyncMock(name="backend")
    backend.name = "file_ipc"
    backend.execute_lisp.return_value = CommandResult(
        ok=True,
        payload='{"styles":[{"font":"simplex","font_status":"resolved"}],'
        '"layouts":[{"style":"missing.ctb","style_status":"missing"}],'
        '"rendering_verified":false}',
        receipt={"request_id": "abc"},
    )
    result = await execute(backend, "preflight", {"drawing": "C:/work/a.dwg"})
    assert result.ok
    assert result.payload["styles"][0]["font_status"] == "resolved"
    assert result.payload["layouts"][0]["style_status"] == "missing"
    assert result.payload["rendering_verified"] is False
    assert result.receipt == {"request_id": "abc"}
    backend.execute_lisp.assert_awaited_once()


@pytest.mark.parametrize("error", ["PLOT_STYLE_MISSING", "PLOT_STYLE_UNVERIFIED"])
async def test_plot_refusal_is_an_error_without_retry(error):
    backend = AsyncMock(name="backend")
    backend.name = "file_ipc"
    backend.execute_lisp.return_value = CommandResult(
        ok=True, payload='{"ok":false,"error":"' + error + '"}',
    )
    result = await execute(backend, "plot", {
        "drawing": "C:/work/a.dwg", "layout": "Layout1", "path": "C:/work/a.pdf",
    })
    assert not result.ok and result.error == error
    backend.execute_lisp.assert_awaited_once()
