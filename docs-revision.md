# Native revision operations

`system(operation="revision", data={"action": ..., "drawing": "C:/full/path/copy.dwg", ...})`
uses the File IPC backend. The eight MCP tool names are unchanged. Reconnect an older
server process to acquire the new operations. The same implementation is available as
`python -m autocad_mcp.revision ACTION request.json --output receipt.json` on Windows.

Actions: `read` (1–100 handles), `export_page` (1–500 records, offset, definitions flag),
`frames` (activate layouts for valid viewport IDs), `batch`, `plot`, `save`.
Each export page ends with a completion/count record; a complete snapshot requires
all contiguous pages from one unchanged drawing, including the definition pages.

A batch has `id`, `journal` (new Windows JSON path), and 1–7 `operations`. Every operation
has a change-list `item` and `kind`. Existing targets supply a `handle` and `expect`
map of DXF codes to old values; include type, space, layer, owner and relevant geometry.
Supported kinds:

- `text`: `expected_text`, `text`; works on TEXT, MTEXT and ATTRIB, including long MTEXT.
- `patch`: `set` DXF fields, every field present in `expect`; use `text` for MTEXT content.
  DXF 6 selects a loaded linetype or ByLayer/ByBlock; native preflight refuses unknown
  names before any batch mutation. DXF 48 accepts a finite positive entity linetype
  scale. Supply the old values explicitly, including `null` for an absent field.
  Inspect the displayed pattern with the drawing's LTSCALE and viewport settings.
  DXF 370 supports standard lineweights in hundredths of a millimetre, including
  ByLayer (-1), ByBlock (-2) and Default (-3). Invalid weights are refused in both
  Python and LISP. Preserve effective plotted appearance when moving between layers.
- `scale`: absolute positive `target`; expected XYZ scales, uniform absolute scale only.
- `move`, `copy`: explicit `delta`; `erase`: captures original bounds in the journal.
- `line`: `space`, `layer`, `start`, `end`; `polyline`: `space`, `layer`, `points`, `closed`.
- `vertices`: straight planar LWPOLYLINE only; `expected_points`, `points` (2D coordinates).
- `split-line`: expected endpoints and ordered `gap` points; retains line appearance.
- `insert`: existing block `name`, `position`, `scale`, `rotation` in radians, `space`,
  `layer`, optional `attributes` map; every requested attribute must exist uniquely.
- `mtext`: `position`, `width`, `height`, `text`, `space`, `layer`, optional `style`.
- `cloud`: `space`, `layer`, positive `arc`, `text_height`, `revision`, and either 2D
  `min`/`max` for a rectangle or `points` for a simple orthogonal polygon (4–32 vertices).
  Creates an outward arc polyline, separate triangle and revision text. Optional
  `marker_position: [x,y]` places the revision text's alignment point independently;
  the triangle is centered around that point using `text_height`.
  The compiler rejects nonfinite geometry, diagonal/zero-length edges, intersections
  and outlines requiring more than 4096 arcs, and normalizes winding before dispatch.

Use `autocad_mcp.revision.expected_state(entity)` with a native readback to form the
geometry/identity precondition map. It retains the **first** occurrence of repeated DXF
codes, matching AutoLISP `assoc`; `dict(entity["dxf"])` incorrectly keeps the last
polyline vertex. Supply `expected_text` separately for text edits, and include any
additional old DXF fields being patched.

For cloud coverage, `cloud_covers_bounds(points, lo, hi)` checks the entire target
rectangle against the polygon, including concave cutouts. A polygon's bounding box
does **not** establish coverage. This conservative helper uses the inner straight
outline; inspect native arc readback and screenshots as well. It does not identify
which drawing objects require clouds or establish visual clearance automatically.

The journal records pre-state and native readback. It is not an automatic rollback.
Inspect any interrupted entry and created handles before proceeding; never replay a
batch with a different journal path to bypass duplicate protection. Take and inspect
a screenshot after each batch. Verify saved results independently.

`plot` uses a named `layout` and a new `path`, preserving its PC3/media/CTB settings.
`save` honors `format` with or without a new `path`; its default `48` writes
AutoCAD 2010. Omitting `path` saves the verified active copy through `SaveAs` with
that format. An explicit destination must not already exist. Verify the saved
file's version bytes (`AC1024` for 2010); an ordinary `drawing.save`/QSAVE can use
the application's newer default. Older-format custom-object dialogs may require inspection.
Never acknowledge an unseen dialog or convert/explode custom objects implicitly.

## Desktop and uncertain completion

All new File IPC clients share an OS lease in the IPC directory. They retain request
intent and completion receipts; timeout blocks new dispatches and never sends ESC.
Older already-running clients do not acquire this lease: serialize them explicitly
or reconnect before editing. Coordinate human use of the same desktop separately.

`system(operation="desktop")` observes windows and pending receipts without dispatch.
For a session unable to initialize, use `python -m autocad_mcp.desktop observe`.
`desktop screenshot --request window.json --output capture.png` captures the specified
`hwnd`, including a modal dialog. `desktop button --request button.json` posts BM_CLICK
only after matching the exact dialog HWND, child button HWND, PID, title and text.
No global keyboard injection, default dialog answers or automatic cancellation.

After reconciling a late result, `system(operation="acknowledge",data={"request_id":...})`
or `desktop acknowledge --request request.json` archives that exact completion and
releases the uncertainty hold. Missing completion evidence stays held; inspect the
window and drawing before manual recovery. A timeout is never proof of no mutation.

`revision compile request.json --lisp-path C:/.../revision_tools.lsp --output batch.lsp`
compiles safely quoted data on WSL for an existing `system.execute_lisp` session.
This compatibility route still requires manual client serialization and timeout checks.
