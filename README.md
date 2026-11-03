# AutoCAD MCP Server

MCP server for AutoCAD LT automation and headless DXF generation.

Two backends, one API:

| Backend | Runtime | Requires AutoCAD? | Screenshot |
|---------|---------|-------------------|------------|
| **File IPC** | Windows Python | Yes — AutoCAD LT 2024+ (Windows) | Win32 PrintWindow |
| **ezdxf** | Any platform | No (headless) | matplotlib render |

The server exposes **8 consolidated tools** (`drawing`, `entity`, `layer`, `block`, `annotation`, `pid`, `view`, `system`) over the MCP stdio transport. An MCP client (Claude Desktop, Claude Code, etc.) connects and drives AutoCAD through natural-language requests.

## Prerequisites (File IPC backend)

- **Windows 10/11** (the File IPC backend uses Win32 APIs for focus-free window messaging)
- **AutoCAD LT 2024 or newer** — AutoLISP support was added in LT 2024 for Windows. AutoCAD LT for Mac exists but does **not** support AutoLISP.
- **Python 3.10+** (Windows native — not WSL Python)
- **uv** package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))

> The ezdxf headless backend works on any platform (Linux, macOS, WSL) for offline DXF generation without AutoCAD installed.

## Quick Start

### 1. Clone and install

```powershell
git clone https://github.com/puran-water/autocad-mcp.git
cd autocad-mcp
uv sync
```

### 2. Load the LISP dispatcher in AutoCAD LT

Open AutoCAD LT and load `mcp_dispatch.lsp` using **APPLOAD**:

1. Type `APPLOAD` in the AutoCAD command line
2. Browse to `<repo>/lisp-code/mcp_dispatch.lsp`
3. Click **Load**
4. You should see: `=== MCP Dispatch v3.1 loaded ===` and `Ready for commands via (c:mcp-dispatch)`

> **Tip:** Add the file to your AutoCAD Startup Suite (in the APPLOAD dialog) so it loads automatically with every drawing.

### 3. Configure your MCP client

Add to your MCP client configuration (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "autocad-mcp": {
      "command": "C:\\path\\to\\autocad-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "autocad_mcp"],
      "env": { "AUTOCAD_MCP_BACKEND": "file_ipc" }
    }
  }
}
```

**Key points:**

- The `command` must point to the **Windows Python** inside the project venv (not WSL python).
- `AUTOCAD_MCP_BACKEND` can be `auto` (default — tries File IPC, falls back to ezdxf), `file_ipc` (requires AutoCAD), or `ezdxf` (headless only).

#### Running from WSL

If your MCP client runs in WSL (e.g. Claude Code), launch the server through `cmd.exe` so it runs as a native Windows process:

```json
{
  "mcpServers": {
    "autocad-mcp": {
      "type": "stdio",
      "command": "cmd.exe",
      "args": ["/d", "/s", "/c", "cd /d C:\\path\\to\\autocad-mcp && set AUTOCAD_MCP_BACKEND=file_ipc&& .venv\\Scripts\\python.exe -m autocad_mcp"]
    }
  }
}
```

### 4. Verify

From your MCP client, call:

```
system(operation="status")
```

You should see `backend: "file_ipc"` if AutoCAD is running, or `backend: "ezdxf"` for headless mode.

For native DWG work, require `file_ipc`; the examples above disable headless fallback.
Verify `DWGNAME`, `DWGPREFIX`, `CTAB` and `DBMOD` with `drawing.get_variables`, then
inspect a screenshot before editing. `status` alone does not identify the active DWG.
Use a Windows-local working copy if Windows cannot access a WSL UNC path, and copy
saved deliverables back explicitly. Never use `drawing.create` to make a working copy:
it erases and purges the active drawing.

## Journaled native revisions

`system(operation="revision", data={"action": "read", "drawing": "C:/work/copy.dwg",
"handles": ["ABCD"]})` reads native entities. The revision adapter supports bounded
exports, layout frames, guarded edits, plotting and AutoCAD 2010 saves. Each mutation
batch has 1–7 operations, a unique journal path, expected document identity and old
entity values. Receipts retain before/after state and created handles. Read the
[operation reference and recovery procedure](docs-revision.md) before use.

Revision clouds can follow an orthogonal route instead of enclosing unrelated labels:

```json
{
  "action": "batch",
  "drawing": "C:/work/copy.dwg",
  "id": "route-cloud-01",
  "journal": "C:/work/route-cloud-01.json",
  "operations": [{
    "kind": "cloud", "item": "approved-change-42",
    "space": "Model", "layer": "REV-01",
    "points": [[0,0], [8,0], [8,2], [2,2], [2,8], [0,8]],
    "arc": 0.4, "text_height": 0.3, "revision": "01",
    "marker_position": [10,3]
  }]
}
```

Create the revision layer first. Pass this object to `system` with `operation="revision"`.
For the Windows CLI, save the object as `request.json` (the extra `action` is ignored):

```powershell
python -m autocad_mcp.revision batch request.json --output receipt.json
```

An existing MCP session can use the updated helper through `execute_lisp` without
acquiring a new tool schema: replace `action` with `operation` in the request, then
compile on WSL and submit the resulting expression as `data.code`:

```bash
python -m autocad_mcp.revision compile request.json \
  --lisp-path C:/path/to/autocad-mcp/lisp-code/revision_tools.lsp --output batch.lsp
```

Keep the Python compiler and Windows LISP helper at the same repository revision.
Inspect native readback and a screenshot after every batch; save checkpoints between
sheets. Journals are evidence, not automatic rollback. Serialize desktop clients and
human edits. On timeout, reconcile the pending receipt and actual drawing before any
retry; newer clients hold dispatch until explicit acknowledgement. Older running
clients need reconnecting to acquire the process lease and new operation schema.

The reusable `expected_state` helper preserves the first DXF value when codes repeat.
`cloud_covers_bounds` checks an entire target rectangle against a cloud polygon,
including its concave cutouts; bounding-box overlap alone is insufficient.

## Tools

### `drawing` — File/drawing management

| Operation | Description | File IPC | ezdxf |
|-----------|-------------|----------|-------|
| `create` | Reset to clean drawing (erase all + purge) | Yes | Yes |
| `open` | Open an existing drawing | Yes | Yes (DXF) |
| `info` | Get entity count and layers | Yes | Yes |
| `save` | Save current drawing (to path if given) | Yes | Yes |
| `save_as_dxf` | Export as DXF | Yes | Yes |
| `plot_pdf` | Plot to PDF | Yes | No |
| `purge` | Purge unused objects | Yes | Yes |
| `get_variables` | Get system variables by name | Yes | Yes |
| `undo` | Undo last operation | Yes | No |
| `redo` | Redo last undone operation | Yes | No |

### `entity` — Entity CRUD + modification

**Create:** `create_line`, `create_circle`, `create_polyline`, `create_rectangle`, `create_arc`, `create_ellipse`, `create_mtext`, `create_hatch`

**Read:** `list`, `count`, `get`

**Modify:** `copy`, `move`, `rotate`, `scale`, `mirror`, `offset`\*, `array`, `fillet`\*, `chamfer`\*, `erase`

> \* `offset`, `fillet`, `chamfer` are File IPC only (not supported in ezdxf headless backend).

### `layer` — Layer management

`list`, `create`, `set_current`, `set_properties`, `freeze`, `thaw`, `lock`, `unlock`

### `block` — Block operations

| Operation | File IPC | ezdxf |
|-----------|----------|-------|
| `list` | Yes | Yes |
| `insert` | Yes | Yes |
| `insert_with_attributes` | Yes | Yes |
| `get_attributes` | Yes | Yes |
| `update_attribute` | Yes | Yes |
| `define` | No | Yes |

### `annotation` — Text, dimensions, leaders

`create_text`, `create_dimension_linear`, `create_dimension_aligned`, `create_dimension_angular`, `create_dimension_radius`, `create_leader`

### `pid` — P&ID operations (CTO symbol library)

`setup_layers`, `insert_symbol`, `list_symbols`, `draw_process_line`, `connect_equipment`, `add_flow_arrow`, `add_equipment_tag`, `add_line_number`, `insert_valve`, `insert_instrument`, `insert_pump`, `insert_tank`

> P&ID symbol insertion requires the [CAD Tools Online](https://www.cadtoolsonline.com/) (CTO) P&ID Symbol Library installed at `C:\PIDv4-CTO\`. The ezdxf backend has built-in CTO library support. For the File IPC backend, some P&ID operations require additional LISP helpers — see the P&ID section in the wiki for setup details.

### `view` — Viewport and screenshot

| Operation | Description |
|-----------|-------------|
| `zoom_extents` | Zoom to show all entities |
| `zoom_window` | Zoom to a specified window |
| `get_screenshot` | Capture current AutoCAD view as PNG |

Screenshots use `PrintWindow` (Win32) for the File IPC backend — works even when AutoCAD is minimized or in the background. The ezdxf backend renders via matplotlib.

### `system` — Server management

`status`, `health`, `get_backend`, `runtime`, `init`, `execute_lisp`

> `execute_lisp` runs arbitrary AutoLISP code (File IPC only). Pass `data: {code: "(+ 1 2)"}`. This turns the server into an extensible automation platform — any valid AutoLISP expression can be executed.

## Architecture

```
MCP Client (Claude)
    │  stdio (JSON-RPC)
    ▼
Python MCP Server (autocad_mcp)
    │
    ├── File IPC Backend ──► C:/temp/*.json ──► mcp_dispatch.lsp (AutoCAD LT)
    │   PostMessageW(WM_CHAR) to MDIClient — no focus steal
    │
    └── ezdxf Backend ──► in-memory DXF (headless, no AutoCAD needed)
```

The File IPC backend sends keystrokes to AutoCAD's MDIClient window via `PostMessageW(WM_CHAR)`, triggering the `(c:mcp-dispatch)` AutoLISP command. This approach does **not** steal window focus — you can continue working in other applications while automation runs.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOCAD_MCP_BACKEND` | `auto` | Backend selection: `auto`, `file_ipc`, `ezdxf` |
| `AUTOCAD_MCP_IPC_DIR` | `C:/temp` | Directory for IPC command/result JSON files (must match on both Python and LISP sides) |
| `AUTOCAD_MCP_IPC_TIMEOUT` | `30.0` | IPC command timeout in seconds (1-300) |
| `AUTOCAD_MCP_IPC_LONG_TIMEOUT` | `120.0` | Timeout for `execute_lisp` and plotting (up to 600) |
| `AUTOCAD_MCP_AUTOLOAD` | `1` | Load `mcp_dispatch.lsp` into the active drawing when it does not answer (`0` to disable) |
| `AUTOCAD_MCP_ONLY_TEXT` | `false` | Disable screenshot capture (text feedback only) |

> **Note:** If you change `AUTOCAD_MCP_IPC_DIR`, you must also update the `*mcp-ipc-dir*` variable in `mcp_dispatch.lsp` to match.

## Development

```powershell
uv sync
uv run pytest tests/ -v
```

## AutoCAD LT AutoLISP Compatibility

AutoLISP was added to AutoCAD LT in the **2024 release (Windows only)**. AutoCAD LT for Mac does not support AutoLISP.

| Supported (LT 2024+ Windows) | Not Supported |
|-------------------------------|---------------|
| `.lsp` / `.fas` / `.vlx` / `.dcl` | VLIDE (Visual LISP IDE) |
| Supported `vl-*` and selected `vla-*`/`vlax-*` functions | Full ActiveX/COM API parity |
| File I/O (`open`, `read-line`, etc.) | Express Tools |
| Entity access (`entget`, `entmod`, etc.) | 3D operations |
| Selection sets | AutoLISP on Mac |

The revision helper was exercised in Windows AutoCAD LT 2027. It uses selected
`vla-*`/`vlax-*` functions available inside that runtime; this does not imply support
for the complete ActiveX API or external COM automation. Qualify required operations
on a disposable drawing for other releases.

After a document switch, a stale cached command window can prevent command pickup.
Inspect the desktop and pending receipts first. If the command was never picked up
and no mutation occurred, `system(operation="init")` refreshes the connection; verify
the active drawing again before resuming. Do not use reinitialization to bypass an
uncertain completion hold.

## What's New in v3.1

- **`execute_lisp`** — Run arbitrary AutoLISP code via temp file pattern. Turns the server from a fixed command set into an extensible automation platform.
- **Undo / Redo** — Single-step undo and redo via `drawing` tool.
- **Drawing open** — Open existing `.dwg` files programmatically (FILEDIA suppressed).
- **Drawing create** — Now resets current drawing (erase all + purge) instead of `_.NEW`, preserving the LISP dispatcher namespace.
- **Drawing save with path** — `save` with a `path` parameter uses SAVEAS; without path uses QSAVE.
- **`get_variables` fix** — Respects the `names` parameter; returns requested variables with proper type handling.
- **Polyline/leader fix** — Point arrays properly encoded via semicolon-delimited format.
- **ESC prefix** — Sends 2x ESC before each dispatch to cancel stale pending commands from prior timeouts.
- **UTF-8/cp1252 fallback** — Handles non-ASCII characters in LISP result files (AutoCAD writes Windows-1252).
- **Configurable IPC timeout** — `AUTOCAD_MCP_IPC_TIMEOUT` env var (1–300 seconds, default 30); `execute_lisp` and plotting use `AUTOCAD_MCP_IPC_LONG_TIMEOUT` (default 120).
- **Dispatcher autoload** — `mcp_dispatch.lsp` is loaded per drawing. When a command is not picked up within 3 s (new drawing tab, dispatcher never loaded), the server loads it into the active drawing and runs that same command; a command that was picked up is never re-sent. Disable with `AUTOCAD_MCP_AUTOLOAD=0`.
- **Thread-safe backend init** — `asyncio.Lock` prevents parallel initialization races.

## License

MIT
