"""Lazy backend singleton, _safe/_error/_json helpers, screenshot utility."""

from __future__ import annotations

import asyncio
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
_init_lock = asyncio.Lock()


async def get_backend() -> AutoCADBackend:
    """Return (and lazily initialize) the backend singleton.

    Uses an asyncio Lock to prevent concurrent initialization races
    when multiple MCP tool calls arrive simultaneously.
    """
    global _backend
    if _backend is not None:
        return _backend

    async with _init_lock:
        # Double-check after acquiring lock (another task may have initialized)
        if _backend is not None:
            return _backend

        backend_name = detect_backend()

        if backend_name == "file_ipc":
            from autocad_mcp.backends.file_ipc import FileIPCBackend

            backend = FileIPCBackend()
        else:
            from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

            backend = EzdxfBackend()

        result = await backend.initialize()
        if not result.ok:
            raise RuntimeError(f"Backend init failed: {result.error}")

        _backend = backend
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

    if 'worker' in msg_lower or 'handoff' in msg_lower or 'reconcile' in msg_lower:
        hint = "Inspect system worker observe and system desktop. Preserve pending receipts; reconcile before starting a fresh worker. Never replay an uncertain request."
    elif "window not found" in msg_lower or "no autocad" in msg_lower:
        hint = "AutoCAD LT is not running or no drawing is open. Start AutoCAD and open a .dwg file."
    elif "timeout" in msg_lower:
        hint = "Stop mutations. Inspect system desktop and reconcile the pending request and touched handles before any further dispatch. Do not retry or send ESC automatically."
    elif "not supported" in msg_lower or "backend" in msg_lower:
        hint = "Operation not supported on current backend. Check system(operation='status') for capabilities."
    elif "dispatcher" in msg_lower or "mcp_dispatch" in msg_lower:
        hint = "mcp_dispatch.lsp not loaded. In AutoCAD command line, type: (load \"mcp_dispatch.lsp\")"
    else:
        hint = "Inspect AutoCAD state and retained request evidence before deciding the next action. Do not replay an uncertain mutation."

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
    screenshot_path: str | None = None,
) -> list[TextContent | ImageContent] | str:
    """Append evidence; screenshot failure never hides a completed mutation."""
    if not include_screenshot and not screenshot_path:
        return _json(result.to_dict())
    if ONLY_TEXT_FEEDBACK and not screenshot_path:
        return _json(result.to_dict())
    value = result.to_dict()
    try:
        backend = await get_backend()
        screenshot_result = await backend.get_screenshot()
        if not screenshot_result.ok or not screenshot_result.payload:
            raise RuntimeError(screenshot_result.error or "SCREENSHOT_FAILED")
        if screenshot_path:
            from autocad_mcp.evidence import save_screenshot
            value["screenshot"] = save_screenshot(screenshot_result.payload, screenshot_path)
        if include_screenshot and not ONLY_TEXT_FEEDBACK:
            return [TextContent(type="text", text=_json(value)),
                    ImageContent(type="image", data=screenshot_result.payload, mimeType="image/png")]
    except Exception as exc:
        value["screenshot_error"] = str(exc)
        value["next_action"] = "Preserve the mutation result; recover screenshot separately. Do not replay the mutation."
    return _json(value)
