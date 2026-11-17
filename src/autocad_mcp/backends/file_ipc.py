"""AutoCAD LT File IPC v3: request-specific trigger and atomic claim/cancel.

A new document loads the dispatcher before the first request, not after a failed
pickup. Requests are never automatically replayed. Claimed timeouts retain intent;
unclaimed timeouts retain a verifiable cancellation envelope for acknowledgement.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

import structlog

from autocad_mcp.backends.base import AutoCADBackend, BackendCapabilities, CommandResult
from autocad_mcp.desktop import ipc_lease, inspect_pending
from autocad_mcp.config import AUTOLOAD_DISPATCHER, IPC_DIR, IPC_LONG_TIMEOUT, IPC_TIMEOUT, LISP_DIR
from autocad_mcp.ipc_protocol import PROTOCOL, cancel_unclaimed, paths, publish, read_json
from autocad_mcp.native_input import post_expression
from autocad_mcp.build import LOADED, require_unchanged, qualification

log = structlog.get_logger()

# IPC settings
POLL_INTERVAL = 0.1  # seconds
TIMEOUT = IPC_TIMEOUT  # seconds (configurable via AUTOCAD_MCP_IPC_TIMEOUT)
LONG_TIMEOUT = IPC_LONG_TIMEOUT  # execute_lisp / plot (AUTOCAD_MCP_IPC_LONG_TIMEOUT)
PICKUP_TIMEOUT = 3.0  # seconds for the dispatcher to claim a command file


def find_autocad_window(pid: int | None = None) -> int | None:
    """Find the AutoCAD LT window handle by checking window titles."""
    if sys.platform != "win32":
        return None
    try:
        import win32gui
        import win32process

        windows: list[int] = []
        pin = str(pid) if pid is not None else os.environ.get('AUTOCAD_MCP_PID')
        if pin and (not pin.isdecimal() or int(pin) <= 0):
            raise RuntimeError('INVALID_AUTOCAD_PID')

        def callback(hwnd, result):
            if win32gui.IsWindowVisible(hwnd):
                text = win32gui.GetWindowText(hwnd).lower()
                if ("autocad" in text and ("drawing" in text or ".dwg" in text)
                        and win32gui.GetClassName(hwnd).startswith('AfxMDIFrame')
                        and (not pin or win32process.GetWindowThreadProcessId(hwnd)[1] == int(pin))):
                    result.append(hwnd)
            return True

        win32gui.EnumWindows(callback, windows)
        if len(windows) > 1:
            raise RuntimeError('AMBIGUOUS_AUTOCAD_PROCESSES; set AUTOCAD_MCP_PID')
        return windows[0] if windows else None
    except ImportError:
        return None


class FileIPCBackend(AutoCADBackend):
    """File-based IPC with AutoCAD LT via mcp_dispatch.lsp."""

    def __init__(self, *, worker_session: str | None = None, transport: str = 'worker'):
        if transport not in ('worker', 'legacy') or (worker_session and transport != 'worker'):
            raise ValueError('INVALID_IPC_TRANSPORT')
        self._hwnd: int | None = None
        self._ipc_dir = Path(IPC_DIR)
        self._screenshot_provider = None
        self._lock = asyncio.Lock()  # Single in-flight command
        self._worker_session = worker_session
        self._worker_state = None
        self._transport = transport

    async def _worker_ready(self):
        from autocad_mcp.worker import discover, wait_ready
        require_unchanged()
        if not self._worker_session:
            self._worker_session = discover(self._ipc_dir)
        self._worker_state = await wait_ready(self._ipc_dir, self._worker_session)
        if self._worker_state.get('build') != LOADED['sha256']:
            raise RuntimeError('NATIVE_WORKER_BUILD_MISMATCH')
        if self._worker_state.get('writestat') != 1:
            raise RuntimeError('WORKER_DRAWING_READ_ONLY')

    @property
    def name(self) -> str:
        return "file_ipc"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
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

    async def initialize(self) -> CommandResult:
        """Find AutoCAD window and verify dispatcher is loaded."""
        self._hwnd = find_autocad_window()
        if not self._hwnd:
            return CommandResult(ok=False, error="AutoCAD LT window not found")

        # Set up screenshot provider
        try:
            from autocad_mcp.screenshot import Win32ScreenshotProvider

            self._screenshot_provider = Win32ScreenshotProvider(self._hwnd)
        except Exception:
            pass

        # Ensure IPC directory exists
        self._ipc_dir.mkdir(parents=True, exist_ok=True)

        # Clean up stale IPC files
        self._cleanup_stale_files()

        # One fresh probe; any timeout is reconciled before further dispatch.
        result = await self._dispatch("ping", {}, timeout=5.0)
        if not result.ok:
            return result

        payload = {"backend": "file_ipc", "hwnd": self._hwnd}
        if isinstance(result.payload, dict):
            payload.update({k: result.payload.get(k) for k in ("version", "drawing")})
        return CommandResult(ok=True, payload=payload)

    @property
    def _lisp_path(self) -> str:
        return str(LISP_DIR / "mcp_dispatch.lsp").replace("\\", "/")

    async def status(self) -> CommandResult:
        info = {
            "backend": "file_ipc",
            "hwnd": self._hwnd,
            "ipc_dir": str(self._ipc_dir),
            "ipc_protocol": PROTOCOL,
            "automatic_replay": False,
            "input_transport": "native_queue_worker" if self._transport == 'worker' else "legacy_postmessage",
            "worker_session": self._worker_session,
            "loaded_build": LOADED['sha256'],
            "native_qualification": qualification(),
            "capabilities": {k: v for k, v in self.capabilities.__dict__.items()},
        }
        if self._worker_session:
            from autocad_mcp.worker import observe
            info['worker'] = observe(self._ipc_dir, self._worker_session)
        return CommandResult(ok=True, payload=info)

    # --- IPC dispatch ---

    async def _dispatch(
        self, command: str, params: dict, timeout: float | None = None
    ) -> CommandResult:
        """Serialize clients and refuse unresolved work. No automatic retry."""
        async with self._lock:
            try:
                with ipc_lease(self._ipc_dir):
                    if (self._ipc_dir / 'pcad-handoff.json').exists():
                        return CommandResult(ok=False, error='IPC_HANDOFF_RECONCILE_REQUIRED')
                    pending = inspect_pending(self._ipc_dir)
                    if pending["pending"]:
                        return CommandResult(
                            ok=False, error="IPC_UNCERTAIN_RECONCILE_REQUIRED", payload=pending
                        )
                    if self._transport == 'worker':
                        await self._worker_ready()
                    result, _ = await self._dispatch_unlocked(command, params, timeout)
                    return result
            except RuntimeError as exc:
                return CommandResult(ok=False, error=str(exc))

    async def worker_transition(self, mode, path=None, *, file_format=None, expected_drawing=None):
        if mode == 'open':
            return CommandResult(ok=False, error='WORKER_OPEN_NOT_QUALIFIED; stop and bootstrap explicitly in the intended document')
        if self._transport != 'worker':
            return CommandResult(ok=False, error='NATIVE_WORKER_REQUIRED')
        from autocad_mcp.worker import normalize_drawing
        from autocad_mcp.worker_transition import transition_locked
        async with self._lock:
            try:
                with ipc_lease(self._ipc_dir):
                    if inspect_pending(self._ipc_dir)['pending']:
                        return CommandResult(ok=False, error='IPC_UNCERTAIN_RECONCILE_REQUIRED')
                    if (self._ipc_dir/'pcad-handoff.json').exists():
                        return CommandResult(ok=False, error='IPC_HANDOFF_RECONCILE_REQUIRED')
                    await self._worker_ready()
                    current = normalize_drawing(self._worker_state['drawing'])
                    if expected_drawing and normalize_drawing(expected_drawing) != current:
                        return CommandResult(ok=False, error='WORKER_TRANSITION_SOURCE_MISMATCH')
                    if mode == 'save':
                        path = path or current
                        if file_format is None:
                            with Path(current).open('rb') as stream:
                                version = stream.read(6)
                            file_format = {b'AC1024':48, b'AC1027':60, b'AC1032':64}.get(version)
                            if file_format is None:
                                return CommandResult(ok=False, error='DWG_FORMAT_NOT_QUALIFIED')
                    else:
                        file_format = 64 if file_format is None else file_format
                    return await transition_locked(self, mode, path, file_format=file_format)
            except (RuntimeError, ValueError, OSError) as exc:
                return CommandResult(ok=False, error=str(exc))

    async def _dispatch_unlocked(
        self, command: str, params: dict, timeout: float | None = None
    ) -> tuple[CommandResult, bool]:
        request_id = uuid.uuid4().hex[:12]
        files = paths(self._ipc_dir, request_id)
        pending_file = self._ipc_dir / "pcad-pending.json"
        payload = {
            "protocol": PROTOCOL,
            "request_id": request_id,
            "command": command,
            "params": {k: v for k, v in params.items() if v is not None},
            "ts": time.time(),
        }
        if self._worker_session:
            from autocad_mcp.worker import normalize_drawing
            payload.update(worker_session=self._worker_session,
                           expected_drawing=normalize_drawing(self._worker_state['drawing']))
        publish(pending_file, payload)
        publish(files["ready"], payload)
        archive = self._ipc_dir / "pcad-receipts"
        archive.mkdir(exist_ok=True)
        completed = False
        delivery = None
        try:
            try:
                delivery = self._type_dispatch_trigger(request_id)
            except Exception as exc:
                cancelled = cancel_unclaimed(self._ipc_dir, request_id)
                receipt = {
                    "request_id": request_id,
                    "protocol": PROTOCOL,
                    "state": "cancelled" if cancelled else "uncertain",
                    "error": str(exc),
                }
                publish(archive / f"{request_id}.transport.json", receipt)
                return CommandResult(ok=False, error=str(exc), payload=receipt), not cancelled
            publish(
                archive / f"{request_id}.delivery.json",
                {"request_id": request_id, "delivery": delivery},
            )
            start = time.monotonic()
            deadline = start + (TIMEOUT if timeout is None else timeout)
            while True:
                if files["result"].exists():
                    try:
                        data = read_json(files["result"])
                        if data.get("request_id") == request_id:
                            # A v3 result alone is not enough: require its original claim.
                            if (
                                not files["claimed"].exists()
                                or read_json(files["claimed"]) != payload
                            ):
                                return CommandResult(
                                    ok=False,
                                    error="IPC_CLAIM_PROOF_MISSING",
                                    payload={"request_id": request_id},
                                ), True
                            publish(archive / f"{request_id}.json", data)
                            completed = True
                            return CommandResult(
                                ok=data.get("ok", False),
                                payload=data.get("payload"),
                                error=data.get("error"),
                                receipt={'request_id':request_id, 'protocol':PROTOCOL,
                                         'archive':str(archive/f'{request_id}.json'),
                                         'delivery':delivery},
                            ), True
                    except (ValueError, OSError):
                        pass
                elapsed = time.monotonic() - start
                claimed = files["claimed"].exists()
                if time.monotonic() >= deadline or (not claimed and elapsed >= PICKUP_TIMEOUT):
                    # Claim and cancellation compete for one exclusive decision directory.
                    # A disappearance without proof is still uncertain.
                    cancelled = cancel_unclaimed(self._ipc_dir, request_id)
                    receipt = {
                        "request_id": request_id,
                        "protocol": PROTOCOL,
                        "state": "cancelled" if cancelled else "uncertain",
                        "claimed": files["claimed"].exists(),
                        "delivery": delivery,
                    }
                    publish(archive / f"{request_id}.transport.json", receipt)
                    error = (
                        "IPC_NOT_PICKED_UP_CANCELLED; inspect desktop and acknowledge; do not retry"
                        if cancelled
                        else "IPC_TIMEOUT_RECONCILE_REQUIRED; command may still be running"
                    )
                    return CommandResult(ok=False, error=error, payload=receipt), not cancelled
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            if completed:
                pending_file.unlink(missing_ok=True)
            # Keep ready/claim/cancellation/result evidence even on process cancellation.
            # An unresolved pending marker fences every other client until reconciled.

    def _type_dispatch_trigger(self, request_id: str):
        """Bootstrap per document, then invoke only this request. No second trigger."""
        if self._transport == 'worker':
            if not self._worker_session:
                raise RuntimeError('WORKER_NOT_BOUND')
            # Publication is the trigger. Native claim/cancel remains authoritative.
            # No window message, focus change, keyboard fallback or auto-restart.
            return {"mode": "native_queue_worker", "session": self._worker_session}
        # A distinct filename namespace prevents legacy wildcard dispatchers from
        # consuming v2 requests, including delayed triggers from an old client.
        path = self._lisp_path.replace('"', '\\"')
        expression = f'(mcp-dispatch-v3 "{request_id}")'
        if AUTOLOAD_DISPATCHER:
            expression = (
                f'(progn (if (not (equal *mcp-ipc-protocol* 3)) (load "{path}")) {expression})'
            )
        return self._post_text(expression)

    def _post_text(self, text: str):
        return post_expression(self._hwnd, text)

    def _cleanup_stale_files(self):
        """Retain IPC evidence; elapsed age never proves a native command completed."""
        return

    # --- Drawing management ---

    async def drawing_info(self) -> CommandResult:
        return await self._dispatch("drawing-info", {})

    async def drawing_save(self, path: str | None = None) -> CommandResult:
        if self._transport == 'worker':
            if path is None:
                from autocad_mcp.native_save import save_current
                return await save_current(self)
            return await self.worker_transition('save', path)
        return await self._dispatch("drawing-save", {"path": path})

    async def drawing_save_as_dxf(self, path: str) -> CommandResult:
        if self._transport == 'worker':
            return CommandResult(ok=False, error='WORKER_DXF_TRANSITION_NOT_QUALIFIED')
        return await self._dispatch("drawing-save-as-dxf", {"path": path})

    async def drawing_create(self, name: str | None = None) -> CommandResult:
        return await self._dispatch("drawing-create", {"name": name})

    async def drawing_purge(self) -> CommandResult:
        return await self._dispatch("drawing-purge", {})

    async def drawing_plot_pdf(self, path: str) -> CommandResult:
        return await self._dispatch("drawing-plot-pdf", {"path": path}, timeout=LONG_TIMEOUT)

    async def drawing_get_variables(self, names: list[str] | None = None) -> CommandResult:
        if names:
            # Strip $ prefix for AutoCAD compatibility (ezdxf uses $ACADVER, AutoCAD uses ACADVER)
            clean_names = [n.lstrip("$") for n in names]
            names_str = ";".join(clean_names)
        else:
            names_str = ""
        return await self._dispatch("drawing-get-variables", {"names_str": names_str})

    async def drawing_open(self, path: str) -> CommandResult:
        if self._transport == 'worker':
            return await self.worker_transition('open', path)
        return await self._dispatch("drawing-open", {"path": path})

    # --- Undo / Redo ---

    async def undo(self) -> CommandResult:
        return await self._dispatch("undo", {})

    async def redo(self) -> CommandResult:
        return await self._dispatch("redo", {})

    # --- Freehand LISP execution ---

    async def execute_lisp(self, code: str) -> CommandResult:
        """Execute arbitrary AutoLISP code via temp file.

        File persists for session; cleaned up by _cleanup_stale_files().
        """
        from autocad_mcp.lisp_preflight import LispPreflightError, validate

        try:
            validate(code)
        except LispPreflightError as exc:
            return CommandResult(ok=False, error=str(exc), payload={"dispatched": False})
        request_id = uuid.uuid4().hex[:12]
        code_file = self._ipc_dir / f"autocad_mcp_lisp_{request_id}.lsp"
        code_file.write_text(code, encoding="utf-8")
        return await self._dispatch("execute-lisp", {
            "code_file": str(code_file).replace("\\", "/")
        }, timeout=LONG_TIMEOUT)

    # --- Entity operations ---

    async def create_line(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        return await self._dispatch("create-line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer})

    async def create_circle(self, cx, cy, radius, layer=None) -> CommandResult:
        return await self._dispatch("create-circle", {"cx": cx, "cy": cy, "radius": radius, "layer": layer})

    async def create_polyline(self, points, closed=False, layer=None) -> CommandResult:
        pts_str = ";".join(f"{p[0]},{p[1]}" for p in points)
        return await self._dispatch("create-polyline", {
            "points_str": pts_str, "closed": "1" if closed else "0", "layer": layer
        })

    async def create_rectangle(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        return await self._dispatch("create-rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer})

    async def create_arc(self, cx, cy, radius, start_angle, end_angle, layer=None) -> CommandResult:
        return await self._dispatch("create-arc", {"cx": cx, "cy": cy, "radius": radius, "start_angle": start_angle, "end_angle": end_angle, "layer": layer})

    async def create_ellipse(self, cx, cy, major_x, major_y, ratio, layer=None) -> CommandResult:
        return await self._dispatch("create-ellipse", {"cx": cx, "cy": cy, "major_x": major_x, "major_y": major_y, "ratio": ratio, "layer": layer})

    async def create_mtext(self, x, y, width, text, height=2.5, layer=None) -> CommandResult:
        return await self._dispatch("create-mtext", {"x": x, "y": y, "width": width, "text": text, "height": height, "layer": layer})

    async def create_hatch(self, entity_id, pattern="ANSI31", scale=1.0, angle=0.0) -> CommandResult:
        return await self._dispatch("create-hatch", {"entity_id": entity_id, "pattern": pattern, "scale": scale, "angle": angle})

    async def entity_list(self, layer=None) -> CommandResult:
        return await self._dispatch("entity-list", {"layer": layer})

    async def entity_count(self, layer=None) -> CommandResult:
        return await self._dispatch("entity-count", {"layer": layer})

    async def entity_get(self, entity_id) -> CommandResult:
        return await self._dispatch("entity-get", {"entity_id": entity_id})

    async def entity_erase(self, entity_id) -> CommandResult:
        return await self._dispatch("entity-erase", {"entity_id": entity_id})

    async def entity_copy(self, entity_id, dx, dy) -> CommandResult:
        return await self._dispatch("entity-copy", {"entity_id": entity_id, "dx": dx, "dy": dy})

    async def entity_move(self, entity_id, dx, dy) -> CommandResult:
        return await self._dispatch("entity-move", {"entity_id": entity_id, "dx": dx, "dy": dy})

    async def entity_rotate(self, entity_id, cx, cy, angle) -> CommandResult:
        return await self._dispatch("entity-rotate", {"entity_id": entity_id, "cx": cx, "cy": cy, "angle": angle})

    async def entity_scale(self, entity_id, cx, cy, factor) -> CommandResult:
        return await self._dispatch("entity-scale", {"entity_id": entity_id, "cx": cx, "cy": cy, "factor": factor})

    async def entity_mirror(self, entity_id, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("entity-mirror", {"entity_id": entity_id, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def entity_offset(self, entity_id, distance) -> CommandResult:
        return await self._dispatch("entity-offset", {"entity_id": entity_id, "distance": distance})

    async def entity_array(self, entity_id, rows, cols, row_dist, col_dist) -> CommandResult:
        return await self._dispatch("entity-array", {"entity_id": entity_id, "rows": rows, "cols": cols, "row_dist": row_dist, "col_dist": col_dist})

    async def entity_fillet(self, entity_id1, entity_id2, radius) -> CommandResult:
        return await self._dispatch("entity-fillet", {"id1": entity_id1, "id2": entity_id2, "radius": radius})

    async def entity_chamfer(self, entity_id1, entity_id2, dist1, dist2) -> CommandResult:
        return await self._dispatch("entity-chamfer", {"id1": entity_id1, "id2": entity_id2, "dist1": dist1, "dist2": dist2})

    # --- Layer operations ---

    async def layer_list(self) -> CommandResult:
        return await self._dispatch("layer-list", {})

    async def layer_create(self, name, color="white", linetype="CONTINUOUS") -> CommandResult:
        return await self._dispatch("layer-create", {"name": name, "color": color, "linetype": linetype})

    async def layer_set_current(self, name) -> CommandResult:
        return await self._dispatch("layer-set-current", {"name": name})

    async def layer_set_properties(self, name, color=None, linetype=None, lineweight=None) -> CommandResult:
        return await self._dispatch("layer-set-properties", {"name": name, "color": color, "linetype": linetype, "lineweight": lineweight})

    async def layer_freeze(self, name) -> CommandResult:
        return await self._dispatch("layer-freeze", {"name": name})

    async def layer_thaw(self, name) -> CommandResult:
        return await self._dispatch("layer-thaw", {"name": name})

    async def layer_lock(self, name) -> CommandResult:
        return await self._dispatch("layer-lock", {"name": name})

    async def layer_unlock(self, name) -> CommandResult:
        return await self._dispatch("layer-unlock", {"name": name})

    # --- Block operations ---

    async def block_list(self) -> CommandResult:
        return await self._dispatch("block-list", {})

    async def block_insert(self, name, x, y, scale=1.0, rotation=0.0, block_id=None) -> CommandResult:
        return await self._dispatch("block-insert", {"name": name, "x": x, "y": y, "scale": scale, "rotation": rotation, "block_id": block_id})

    async def block_insert_with_attributes(self, name, x, y, scale=1.0, rotation=0.0, attributes=None) -> CommandResult:
        return await self._dispatch("block-insert-with-attributes", {"name": name, "x": x, "y": y, "scale": scale, "rotation": rotation, "attributes": attributes or {}})

    async def block_get_attributes(self, entity_id) -> CommandResult:
        return await self._dispatch("block-get-attributes", {"entity_id": entity_id})

    async def block_update_attribute(self, entity_id, tag, value) -> CommandResult:
        return await self._dispatch("block-update-attribute", {"entity_id": entity_id, "tag": tag, "value": value})

    async def block_define(self, name, entities) -> CommandResult:
        return await self._dispatch("block-define", {"name": name, "entities": entities})

    # --- Annotation ---

    async def create_text(self, x, y, text, height=2.5, rotation=0.0, layer=None) -> CommandResult:
        return await self._dispatch("create-text", {"x": x, "y": y, "text": text, "height": height, "rotation": rotation, "layer": layer})

    async def create_dimension_linear(self, x1, y1, x2, y2, dim_x, dim_y) -> CommandResult:
        return await self._dispatch("create-dimension-linear", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "dim_x": dim_x, "dim_y": dim_y})

    async def create_dimension_aligned(self, x1, y1, x2, y2, offset) -> CommandResult:
        return await self._dispatch("create-dimension-aligned", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "offset": offset})

    async def create_dimension_angular(self, cx, cy, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("create-dimension-angular", {"cx": cx, "cy": cy, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def create_dimension_radius(self, cx, cy, radius, angle) -> CommandResult:
        return await self._dispatch("create-dimension-radius", {"cx": cx, "cy": cy, "radius": radius, "angle": angle})

    async def create_leader(self, points, text) -> CommandResult:
        pts_str = ";".join(f"{p[0]},{p[1]}" for p in points)
        return await self._dispatch("create-leader", {"points_str": pts_str, "text": text})

    # --- P&ID ---

    async def pid_setup_layers(self) -> CommandResult:
        return await self._dispatch("pid-setup-layers", {})

    async def pid_insert_symbol(self, category, symbol, x, y, scale=1.0, rotation=0.0) -> CommandResult:
        return await self._dispatch("pid-insert-symbol", {"category": category, "symbol": symbol, "x": x, "y": y, "scale": scale, "rotation": rotation})

    async def pid_list_symbols(self, category) -> CommandResult:
        return await self._dispatch("pid-list-symbols", {"category": category})

    async def pid_draw_process_line(self, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("pid-draw-process-line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def pid_connect_equipment(self, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("pid-connect-equipment", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def pid_add_flow_arrow(self, x, y, rotation=0.0) -> CommandResult:
        return await self._dispatch("pid-add-flow-arrow", {"x": x, "y": y, "rotation": rotation})

    async def pid_add_equipment_tag(self, x, y, tag, description="") -> CommandResult:
        return await self._dispatch("pid-add-equipment-tag", {"x": x, "y": y, "tag": tag, "description": description})

    async def pid_add_line_number(self, x, y, line_num, spec) -> CommandResult:
        return await self._dispatch("pid-add-line-number", {"x": x, "y": y, "line_num": line_num, "spec": spec})

    async def pid_insert_valve(self, x, y, valve_type, rotation=0.0, attributes=None) -> CommandResult:
        return await self._dispatch("pid-insert-valve", {"x": x, "y": y, "valve_type": valve_type, "rotation": rotation, "attributes": attributes or {}})

    async def pid_insert_instrument(self, x, y, instrument_type, rotation=0.0, tag_id="", range_value="") -> CommandResult:
        return await self._dispatch("pid-insert-instrument", {"x": x, "y": y, "instrument_type": instrument_type, "rotation": rotation, "tag_id": tag_id, "range_value": range_value})

    async def pid_insert_pump(self, x, y, pump_type, rotation=0.0, attributes=None) -> CommandResult:
        return await self._dispatch("pid-insert-pump", {"x": x, "y": y, "pump_type": pump_type, "rotation": rotation, "attributes": attributes or {}})

    async def pid_insert_tank(self, x, y, tank_type, scale=1.0, attributes=None) -> CommandResult:
        return await self._dispatch("pid-insert-tank", {"x": x, "y": y, "tank_type": tank_type, "scale": scale, "attributes": attributes or {}})

    # --- View ---

    async def zoom_extents(self) -> CommandResult:
        return await self._dispatch("zoom-extents", {})

    async def zoom_window(self, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("zoom-window", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def get_screenshot(self) -> CommandResult:
        if self._hwnd and sys.platform == "win32":
            try:
                import win32gui

                # PrintWindow on a minimized window returns an all-black image
                if win32gui.IsIconic(self._hwnd):
                    return CommandResult(
                        ok=False,
                        error="AutoCAD window is minimized; restore it to capture a screenshot",
                    )
            except ImportError:
                pass
        if self._screenshot_provider:
            data = self._screenshot_provider.capture()
            if data:
                return CommandResult(ok=True, payload=data)
        return CommandResult(ok=False, error="Screenshot capture failed")
