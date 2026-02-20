"""Lazy backend singleton, _safe/_error/_json helpers, screenshot utility."""

from __future__ import annotations

import base64
import functools
import json
from typing import Any

import structlog
from mcp.types import ImageContent, TextContent

from autocad_mcp.backends.base import AutoCADBackend, CommandResult
from autocad_mcp.config import ONLY_TEXT_FEEDBACK, detect_backend

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Lazy backend singleton
# ---------------------------------------------------------------------------

_backend: AutoCADBackend | None = None


async def get_backend() -> AutoCADBackend:
    """Return (and lazily initialize) the backend singleton."""
    global _backend
    if _backend is not None:
        return _backend

    backend_name = detect_backend()

    if backend_name == "file_ipc":
        from autocad_mcp.backends.file_ipc import FileIPCBackend

        _backend = FileIPCBackend()
    else:
        from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

        _backend = EzdxfBackend()

    result = await _backend.initialize()
    if not result.ok:
        _backend = None
        raise RuntimeError(f"Backend init failed: {result.error}")

    log.info("backend_initialized", backend=_backend.name)
    return _backend


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------


def _json(data: Any) -> str:
    """Serialize to compact JSON string."""
    return json.dumps(data, default=str, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Error formatting with actionable hints
# ---------------------------------------------------------------------------


def _error(e: Exception, context: str = "") -> str:
    """Format an exception with an actionable hint."""
    msg = str(e)
    msg_lower = msg.lower()

    if "window not found" in msg_lower or "no autocad" in msg_lower:
        hint = "AutoCAD LT is not running or no drawing is open. Start AutoCAD and open a .dwg file."
    elif "timeout" in msg_lower:
        hint = "Command timed out. AutoCAD may be in a modal dialog. Press ESC in AutoCAD and retry."
    elif "not supported" in msg_lower or "backend" in msg_lower:
        hint = "Operation not supported on current backend. Check system(operation='status') for capabilities."
    elif "dispatcher" in msg_lower or "mcp_dispatch" in msg_lower:
        hint = "mcp_dispatch.lsp not loaded. In AutoCAD command line, type: (load \"mcp_dispatch.lsp\")"
    else:
        hint = "Unexpected error. Check AutoCAD is responsive and retry."

    return _json({"error": f"[{context}] {msg}" if context else msg, "hint": hint})


# ---------------------------------------------------------------------------
# _safe decorator for tool error handling
# ---------------------------------------------------------------------------


def _safe(tool_name: str):
    """Wrap an async tool handler with uniform error handling."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                op = kwargs.get("operation", "unknown")
                log.error("tool_error", tool=tool_name, operation=op, error=str(e))
                return _error(e, f"{tool_name}.{op}")

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------


def _format_result(
    result: CommandResult,
    include_screenshot: bool = False,
    screenshot_data: str | None = None,
) -> list[TextContent | ImageContent] | str:
    """Format a CommandResult for MCP response.

    Returns a list with TextContent + optional ImageContent if screenshot requested,
    or a plain JSON string if no screenshot.
    """
    text = _json(result.to_dict())

    if not include_screenshot or ONLY_TEXT_FEEDBACK or not screenshot_data:
        return text

    return [
        TextContent(type="text", text=text),
        ImageContent(
            type="image",
            data=screenshot_data,
            mimeType="image/png",
        ),
    ]


async def add_screenshot_if_available(
    result: CommandResult,
    include_screenshot: bool = False,
) -> list[TextContent | ImageContent] | str:
    """Conditionally append a screenshot to the result."""
    if not include_screenshot or ONLY_TEXT_FEEDBACK:
        return _json(result.to_dict())

    backend = await get_backend()
    screenshot_result = await backend.get_screenshot()

    if screenshot_result.ok and screenshot_result.payload:
        return _format_result(result, True, screenshot_result.payload)

    return _json(result.to_dict())
