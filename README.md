# AutoCAD MCP Server

## PuranOS role and current scope

This is an active drawing-automation integration used alongside the PuranOS engineering workflow. AutoCAD LT and headless DXF are distinct backends with the platform requirements below. The current guarded revision helpers operate on an explicitly identified working drawing; engineering basis, canonical equipment records and release decisions live in the wider workflow.

[Sizing-to-balance, proposals and BEPs](https://github.com/puran-water/PuranOS-public/blob/main/docs/workflows.md) produce the engineering basis. [PuranOS Studio](https://github.com/puran-water/PuranOS-public/blob/main/docs/studio.md) presents retained review publications. Neither a successful CAD command nor a screenshot approves a design. This repository is maintained separately from the six-server public calculation mirror.

Built by [Puran Water](https://puranwater.com/) · [Discuss engineering or a drawing workflow](https://puranwater.com/contact/).


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
Native revision `save` defaults to AutoCAD 2010 (`format:48`) and honors the format
even when `path` is omitted. It saves the verified active copy in that case;
an explicit new destination must not exist. Verify `AC1024` in the saved DWG header.
The ordinary `drawing.save`/QSAVE tool follows the application's save defaults.

Guarded revision patches can set an entity's linetype (DXF 6) and linetype scale
(DXF 48), for example electrical signal lines in a P&ID. The linetype must already
be loaded, or be ByLayer/ByBlock; the scale must be finite and positive. Old-value
checks apply to both fields. Inspect the native pattern at the intended plot scale.

For repeated instrument labels, an `attributes` operation updates up to 16 attributes
on one existing block. Supply the changed `attributes` and their `expected_attributes`;
native preflight checks unique tags, old values and unlocked layers before the batch
starts. This groups function/loop/alarm changes while retaining the seven-operation
batch limit, before/after receipts and screenshot review. It does not infer loop identities.

Use the guarded `rotate` revision operation to turn an existing planar block and its
attributes together about its insertion point. `target` is an absolute angle in radians;
include the expected insertion point (DXF 10), rotation (50) and normal (210), in addition
to type, owner and layer. This supports upright instrument lettering without recreating
each attribute. Recheck process/signal attachments: arbitrary equipment rotation can move
its ports. Old-value failures now identify the handle, DXF field and conflicting values.

Inspect native readback and a screenshot after every batch; save checkpoints between
sheets. Journals are evidence, not automatic rollback. Serialize desktop clients and
human edits. On timeout, reconcile the pending receipt and actual drawing before any
retry; newer clients hold dispatch until explicit acknowledgement. Older running
clients need reconnecting to acquire the process lease and new operation schema.

### Consistent symbols, crossing gaps and layers

`autocad_mcp.symbol_normalization.scale_with_connections(symbol, entities, target,
ports, item=..., anchor=...)` plans a bounded resize from qualified block-local XY
ports. It preserves rotation and mirror signs, matches LINE/open straight-polyline
endpoints, and refuses a resize that would turn an orthogonal connection diagonal.
The optional local anchor keeps that point fixed and retains attribute lettering
through a transform patch. Review unmatched ports, junctions and lettering yourself;
the planner does not infer equipment families or reroute a network. Its return value
contains `operations` and the before/after port matches. Supply fresh native entities.

For CTO symbols, inspect the installed DWG definition's units, base point and real
process ports before choosing target scale. Equal insertion factors across unrelated
definitions are not equal visible sizes. Keep proprietary CTO artwork outside this
repository. Normalize legend samples with the adopted symbol families.

`spline-gap` removes a short, explicitly selected interior interval of an open SPLINE.
Supply its full `expected_dxf` and two ordered 3D `gap` points on the curve. The helper
uses `command-s` inside the guarded call and verifies both resulting spline endpoints.
Use this for an embedded crossing jumper only after identifying the true crossing;
retain hose curvature elsewhere. A non-erase batch operation returning no handles now
fails with `MUTATION_RETURNED_NO_HANDLES` rather than claiming an empty success.

`rename_layers` accepts `drawing`, a new `journal` path, and 1–7 `pairs` of old/new
names. It preflights all names, preserves layer objects, compares all recorded layer
properties except name/ename, and records each rename. It does not merge layers or
change entity overrides. Update cached entity layers before planning another edit.
Layer `0` and `Defpoints` are reserved. Example compiler request:

```json
{"operation":"rename_layers","drawing":"C:/work/copy.dwg",
 "journal":"C:/work/layers-01.json","pairs":[["Process","P-PIPE-MAIN"]]}
```

These operations were used in a native AutoCAD LT 2027 P&ID revision with bounded
readback and viewport review; no automated test suite was run for this change.
Refresh the actual view after a zoom before capturing it: `ZoomCenter` using the
current `VIEWCTR`/`VIEWSIZE` resolved stale screenshot frames where Regen alone did
not. Cloud replacement must account for the boundary, triangle and revision text
handles returned by its receipt.

The reusable `expected_state` helper preserves the first DXF value when codes repeat.
`cloud_covers_bounds` checks an entire target rectangle against a cloud polygon,
including its concave cutouts; bounding-box overlap alone is insufficient.

For layer cleanup or matching new piping to an existing run, a guarded `patch` can
set colour (DXF 62), layer (8), and lineweight (370). Include their actual old values
in `expect`; use `null` only when the native DXF group is absent. For example,
`"set": {"62": 221, "370": 13}` sets colour 221 and 0.13 mm lineweight. Read the
source run and its layer first: assigning ByLayer can change the plotted appearance.
The adapter validates the standard lineweight values, but does not decide the
engineering role, crossing priority, plot-style overrides or approved house weights.

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
